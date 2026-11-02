"""The 3a graph: order status and customer reply (ADR 0012 « Graphes », ADR 0013).

::

    route → fetch → scope_check → quarantine →
        [order_status]   compose_internal → guard → END
        [customer_reply] draft → verify → compose → guard → propose
                         → approve ⇄ reguard → apply → handoff → END
    any refusal → END (``outcome`` and ``refusal`` set)

:func:`build_service_graph` returns the builder an
:class:`~alverno_lab.agents.ops.runtime.OpsEnvelope` takes: it compiles the graph with
the envelope's checkpointer. The code routes, plans the single read, fills every
argument and joins the contact; the drafter only writes a body from the facts.

- ``route``: :func:`~alverno_lab.agents.service.route.route_service` on the redacted
  request (``order_not_identified``, ``multi_order``).
- ``fetch``: one ``erp.get_order`` through the executor. An unknown order is refused
  ``record_not_found``; a connector failure ends in ``error`` with
  ``connector_unavailable``. The view goes to ``records`` as JSON (the quarantined note
  keeps only its scrubbed text and flags).
- ``scope_check``: every record carries the order's account, and the request names no
  value of another account (``check_scope``); else ``cross_account``, before any draft.
- ``quarantine``: the records' quarantine flags, for the access log.
- ``compose_internal`` (``order_status``): a French answer for the operator, made of the
  facts block lines.
- ``draft`` (``customer_reply``): the drafter gets the facts only (no account label, no
  contact, no request) and returns ``DraftBody``. A drafter failure is an ``error``
  without reason (``draft_failed``), except a model that cannot be reached
  (:class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError`), which escapes to the
  envelope like a connector failure.
- ``verify``: numbers grounded in the facts (``find_ungrounded_numbers``), cited and
  written order ids among those read, every status form the rendering of the ERP status,
  no placeholder but ``{salutation}`` and ``{signature}``, and no closed-list term the
  facts do not hold (:func:`find_unsupported_terms`: a number or a date written in
  words, a link or an e-mail address, a commercial commitment, English function
  words); else ``ungrounded``.
- ``compose`` (code): the salutation (civility and surname, or « Bonjour, » for a sole
  proprietorship), the signature (the service role mailbox) and the « À : » line, from
  ``get_order_contact``. The contact view is never stored in state; only the composed
  text is.
- ``guard``: ``check_scope`` first (``cross_account``); for a reply, the quarantine
  rules on the composed text, as ``reguard`` applies them to an edit (an injection, an
  action request or a credential is ``output_pi``, rule ``quarantine``); then
  ``check_output`` with the facts block and, for a reply, the in-scope contact as cited
  extracts (``output_pi``, the guard rule's name in the trace). A failure withholds the
  text.
- ``propose``: the digest of the composed text, the ``erp.record_reply_prepared`` action
  bound to it and a new ``draft_id``.
- The approval tail (:func:`~alverno_lab.agents.ops.approval.add_approval_nodes`) with
  the 3a edit check (:func:`make_check_edit`): ``check_scope`` first (``cross_account``),
  then the quarantine rules on the edit (an injection, an action request or a
  credential is ``output_pi``), then ``check_output`` (``output_pi``).

The guard, the edit check and the header read the contact again by code, for the
extracts and the display only. Every node appends one scrubbed trace entry
(:func:`~alverno_lab.agents.ops.scrub.scrub_entry`); no node writes the access log.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import JsonValue

from alverno_lab.agents.ops.approval import (
    APPROVE_NODE,
    MAX_EDIT_ROUNDS,
    ApprovalDecision,
    EditCheck,
    EditChecker,
    OpsNode,
    ToolCaller,
    add_approval_nodes,
)
from alverno_lab.agents.ops.rendering import FORM_STATUS, MONTHS_FR, find_status_forms
from alverno_lab.agents.ops.runtime import ModelUnavailableError, OpsGraph
from alverno_lab.agents.ops.scrub import scrub_entry
from alverno_lab.agents.ops.state import OpsState, ProposedAction, text_sha256
from alverno_lab.agents.quality.graph import find_ungrounded_numbers
from alverno_lab.agents.service.drafting import (
    PLACEHOLDERS,
    SALUTATION,
    SIGNATURE,
    DraftBody,
    Drafter,
    order_facts,
)
from alverno_lab.agents.service.route import find_order_ids, route_service
from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.connectors.types import RECORD_REPLY_TOOL, ContactView, OrderView
from alverno_lab.privacy.guard import check_output
from alverno_lab.privacy.lexicon import normalize
from alverno_lab.privacy.ops_policy import SERVICE_MAILBOX
from alverno_lab.privacy.quarantine import scrub_tool_text
from alverno_lab.privacy.scope import ScopeError, ScopeIndex, check_scope
from alverno_lab.privacy.types import PrivacyContext
from alverno_lab.tools.registry import RecordReplyPreparedArgs

__all__ = [
    "NEUTRAL_SALUTATION",
    "SERVICE_NODES",
    "SIGNATURE_TEXT",
    "TO_PREFIX",
    "build_service_graph",
    "compose_text",
    "contact_extract",
    "find_unsupported_terms",
    "make_check_edit",
    "quarantine_stop",
    "salutation_for",
    "service_answer",
    "verify_body",
]

NEUTRAL_SALUTATION = "Bonjour,"
SIGNATURE_TEXT = f"Le service à la clientèle\n{SERVICE_MAILBOX}"
TO_PREFIX = "À : "
GET_ORDER = "erp.get_order"
SUMMARY_FR = "Réponse préparée au client, à envoyer à la main par l'opérateur"
SERVICE_NODES: tuple[str, ...] = (
    "route",
    "fetch",
    "scope_check",
    "quarantine",
    "compose_internal",
    "draft",
    "verify",
    "compose",
    "guard",
    "propose",
)

# Both connector codes of the executor become the « connector_unavailable » refusal.
_CONNECTOR_ERRORS: frozenset[str] = frozenset({"connector_error", "connector_unavailable"})
# Quarantine flags that withhold an edit: an order, an action request or a secret.
_EDIT_STOP_FLAGS: frozenset[str] = frozenset({"injection", "action_request", "credential"})
_BRACES_RE = re.compile(r"\{[^{}\n]*\}")
_WORD_RE = re.compile(r"[a-z]+")
# A link or an e-mail address: the body never needs one (the signature is filled by code).
_LINK_RE = re.compile(
    r"(?i)(?:https?://|www\.)\S+"
    r"|[^\s@]+@[^\s@]+\.[a-z]{2,}"
    r"|\b[a-z0-9-]+\.(?:com|ca|net|org|info|io|fr|biz)\b"
)
# Closed lists of words the facts must hold before a body may use them, matched on
# ``normalize``d words (case and accents folded; a hyphen splits « dix-sept »). « un » and
# « une » are articles and are left out; « neuf » (also « new ») is kept, so a body using
# it in that sense is withheld: that false positive is accepted.
_NUMBER_WORDS: frozenset[str] = frozenset(
    {
        "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze",
        "douze", "treize", "quatorze", "quinze", "seize", "vingt", "vingts", "trente",
        "quarante", "cinquante", "soixante", "septante", "huitante", "octante", "nonante",
        "cent", "cents", "mille", "million", "millions", "milliard", "milliards",
        "douzaine", "douzaines", "centaine", "centaines", "millier", "milliers",
    }
)  # fmt: skip
_DATE_WORDS: frozenset[str] = frozenset(
    {
        *(normalize(month) for month in MONTHS_FR),
        "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
        "aujourd", "demain", "hier",
    }
)  # fmt: skip
# Word prefixes of a commercial commitment (a discount, a credit, a refund, a free item,
# a compensation, a guarantee): only a person may promise one.
_COMMITMENT_PREFIXES: tuple[str, ...] = (
    "rabais", "credit", "rembours", "escompt", "gratuit", "frais", "dedommag", "indemn",
    "compens", "ristourne", "garanti",
)  # fmt: skip
# English function words; French has none of them as a word of its own.
_ENGLISH_WORDS: frozenset[str] = frozenset(
    {
        "the", "your", "you", "we", "our", "will", "is", "are", "and", "with", "this",
        "that", "for", "has", "have", "been", "be", "of", "to", "it", "please", "thank",
        "thanks", "would", "should", "from", "by",
    }
)  # fmt: skip
# Two distinct English function words make a body « not French » (one may be a name).
_ENGLISH_MIN = 2


# --------------------------------------------------------------------------- helpers
def _trace(node: str, detail: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    return [scrub_entry(node, detail)]


def _stop(node: str, outcome: str, refusal: str | None, **detail: object) -> OpsState:
    """End the request at ``node``: the outcome, its refusal code and the trace entry."""
    update: OpsState = {
        "outcome": outcome,
        "refusal": refusal,
        "trace": _trace(node, {**detail, "refusal": refusal}),
    }
    if outcome == "withheld":
        # A withheld text is never kept: the checkpoint holds no copy of it.
        update["draft_body"] = None
    return update


def _order(state: OpsState) -> OrderView | None:
    """The in-scope order view, rebuilt from ``records``; None when it cannot be."""
    ids = state.get("ids") or []
    if len(ids) != 1:
        return None
    for record in state.get("records") or []:
        if isinstance(record, dict) and record.get("order_id") == ids[0]:
            try:
                return OrderView.model_validate(record)
            # Never raise or chain: a broken record fails the request closed.
            except Exception:
                return None
    return None


def _fetched_ids(state: OpsState) -> frozenset[str]:
    return frozenset(
        str(record.get("order_id"))
        for record in state.get("records") or []
        if isinstance(record, dict)
    )


def salutation_for(contact: ContactView) -> str:
    """« Bonjour {civility} {surname}, », or « Bonjour, » for a sole proprietorship."""
    if contact.natural_person:
        return NEUTRAL_SALUTATION
    return f"Bonjour {contact.civility} {contact.surname},"


def contact_extract(contact: ContactView) -> str:
    """The in-scope contact as a cited extract of the guard (memory only, never stored)."""
    return "\n".join(
        [
            f"{contact.civility} {contact.given_name} {contact.surname}",
            contact.function,
            contact.business_email,
            contact.account_label,
        ]
    )


def compose_text(body: str, contact: ContactView) -> str:
    """The « À : » line, then the body with its two placeholders filled by code."""
    filled = body.replace(SALUTATION, salutation_for(contact)).replace(SIGNATURE, SIGNATURE_TEXT)
    return f"{TO_PREFIX}{contact.business_email}\n\n{filled}"


def service_answer(order: OrderView, erp: ErpConnector) -> str:
    """The operator's answer of ``order_status``: a lead line, then the facts lines."""
    facts = order_facts(order, erp)
    lead = f"Voici l'état de la commande {order.order_id}, selon l'ERP."
    return "\n".join([lead, *facts.lines()[1:]])


def find_unsupported_terms(body: str, block: str) -> list[str]:
    """The closed-list kinds of terms in ``body`` that the facts ``block`` does not hold.

    Each kind is a code, never a value: ``number_word`` (a quantity written in words),
    ``date_word`` (a month, a weekday, « aujourd'hui », « demain » or « hier »), ``link``
    (a URL, a domain or an e-mail address), ``commitment`` (a discount, a credit, a
    refund, a free item, a compensation or a guarantee) and ``foreign`` (at least two
    distinct English function words: the reply is written in French). A term the block
    holds (a label, the note) is grounded and allowed. ``find_ungrounded_numbers`` only
    reads digits; these checks close the same gap for words.
    """
    allowed = set(_WORD_RE.findall(normalize(block)))
    used = set(_WORD_RE.findall(normalize(body))) - allowed
    found: list[str] = []
    if used & _NUMBER_WORDS:
        found.append("number_word")
    if used & _DATE_WORDS:
        found.append("date_word")
    if any(match.group(0) not in block for match in _LINK_RE.finditer(body)):
        found.append("link")
    if any(word.startswith(_COMMITMENT_PREFIXES) for word in used):
        found.append("commitment")
    if len(used & _ENGLISH_WORDS) >= _ENGLISH_MIN:
        found.append("foreign")
    return found


def quarantine_stop(text: str, ctx: PrivacyContext) -> bool:
    """Whether the quarantine rules flag ``text``: an injection, an action request or a
    credential (the same rules for a model draft in ``guard`` and an edit in ``reguard``)."""
    return bool(set(scrub_tool_text(text, ctx).flags) & _EDIT_STOP_FLAGS)


def verify_body(
    body: str, citations: list[str], order: OrderView, block: str, fetched: frozenset[str]
) -> bool:
    """Whether a drafted body is grounded in the facts block (ADR 0012 ``verify``)."""
    if find_ungrounded_numbers(body, [block]):
        return False
    if any(cited not in fetched for cited in citations):
        return False
    if any(written not in fetched for written in find_order_ids(body)):
        return False
    if any(FORM_STATUS[form] != order.status for form in find_status_forms(body)):
        return False
    placeholders = set(_BRACES_RE.findall(body))
    if not placeholders <= PLACEHOLDERS:
        return False
    # A brace left once the two placeholders are removed is an unknown placeholder.
    rest = body
    for placeholder in PLACEHOLDERS:
        rest = rest.replace(placeholder, "")
    if "{" in rest or "}" in rest:
        return False
    return not find_unsupported_terms(rest, block)


def _contact(erp: ErpConnector, order: OrderView) -> ContactView | None:
    """The order's contact when it belongs to the order's account, else None."""
    contact = erp.get_order_contact(order.order_id)
    if contact is None or contact.account_id != order.account_id:
        return None
    return contact


def _in_scope(text: str, account_id: str, index: ScopeIndex) -> bool:
    try:
        return check_scope(text, account_id, index).ok
    # An account the index does not know cannot be checked: fail closed.
    except ScopeError:
        return False


def _reply_action(order_id: str, text: str) -> ProposedAction:
    args = RecordReplyPreparedArgs(order_id=order_id, draft_sha256=text_sha256(text))
    return ProposedAction.build(RECORD_REPLY_TOOL, args, summary_fr=SUMMARY_FR)


def make_check_edit(
    *, erp: ErpConnector, ctx: PrivacyContext, scope_index: ScopeIndex
) -> EditChecker:
    """The 3a edit check of ``reguard`` (i2-spec §6 order: scope, quarantine, output)."""

    def check_edit(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        edited = decision.edited_text
        order = _order(state)
        if edited is None or order is None:
            return EditCheck(ok=False, reason="invalid_args")
        if not _in_scope(edited, order.account_id, scope_index):
            return EditCheck(ok=False, reason="cross_account")
        if quarantine_stop(edited, ctx):
            return EditCheck(ok=False, reason="output_pi")
        extracts = [order_facts(order, erp).block]
        contact = _contact(erp, order)
        if contact is not None:
            extracts.append(contact_extract(contact))
        verdict = check_output(
            edited,
            redacted_question=state.get("request_redacted") or "",
            raw_values=(),
            cited_extracts=extracts,
            ctx=ctx,
        )
        if not verdict.ok:
            return EditCheck(ok=False, reason="output_pi")
        action = _reply_action(order.order_id, edited)
        return EditCheck(ok=True, draft_body=edited, proposed_action=action.model_dump(mode="json"))

    return check_edit


def _after(target: str) -> Callable[[OpsState], str]:
    def route(state: OpsState) -> str:
        return END if state.get("outcome") is not None else target

    return route


def _after_quarantine(state: OpsState) -> str:
    # The quarantine node never ends a request: the flow alone chooses the branch.
    return "draft" if state.get("flow") == "customer_reply" else "compose_internal"


def _after_guard(state: OpsState) -> str:
    if state.get("outcome") is None and state.get("flow") == "customer_reply":
        return "propose"
    return END


# --------------------------------------------------------------------------- graph
def build_service_graph(
    *,
    executor: ToolCaller,
    erp: ErpConnector,
    ctx: PrivacyContext,
    scope_index: ScopeIndex,
    drafter: Drafter,
    max_edit_rounds: int = MAX_EDIT_ROUNDS,
) -> Callable[[BaseCheckpointSaver[str]], OpsGraph]:
    """The builder of the 3a graph, for :class:`~alverno_lab.agents.ops.runtime.OpsEnvelope`.

    ``executor`` runs the read and the write (with the token); ``erp`` is read by code
    for the labels and the contact; ``scope_index`` indexes every account's values;
    ``max_edit_rounds`` must equal the envelope's (``ops_max_edit_rounds``).
    """

    def route(state: OpsState) -> OpsState:
        request = state.get("request_redacted") or ""
        result = route_service(request)
        if result.refusal is not None:
            count = len(find_order_ids(request))
            return _stop("route", "refused", result.refusal, id_count=count)
        ids = [str(result.order_id)]
        return {
            "flow": result.flow,
            "ids": ids,
            "trace": _trace("route", {"flow": result.flow, "ids": ids, "id_count": 1}),
        }

    def fetch(state: OpsState) -> OpsState:
        ids = list(state.get("ids") or [])
        result = executor.call(GET_ORDER, {"order_id": ids[0] if ids else ""})
        events = list(state.get("tool_events") or [])
        events.append({"tool": result.tool, "ok": result.ok, "error": result.error})
        detail: dict[str, object] = {
            "tool": result.tool,
            "ok": result.ok,
            "error": result.error,
            "ids": ids,
        }
        if not result.ok:
            refusal = "connector_unavailable" if result.error in _CONNECTOR_ERRORS else None
            return {"tool_events": events, **_stop("fetch", "error", refusal, **detail)}
        view = result.data
        if view is None:
            refused = _stop("fetch", "refused", "record_not_found", **detail)
            return {"tool_events": events, **refused}
        if not isinstance(view, OrderView):
            return {"tool_events": events, **_stop("fetch", "error", None, **detail)}
        return {
            "tool_events": events,
            "records": [view.model_dump(mode="json")],
            "trace": _trace("fetch", {**detail, "record_count": 1}),
        }

    def scope(state: OpsState) -> OpsState:
        order = _order(state)
        if order is None:
            return _stop("scope_check", "error", None, ok=False)
        records = state.get("records") or []
        same_account = all(
            isinstance(record, dict) and record.get("account_id") == order.account_id
            for record in records
        )
        request = state.get("request_redacted") or ""
        if not same_account or not _in_scope(request, order.account_id, scope_index):
            return _stop("scope_check", "refused", "cross_account", ok=False)
        return {"trace": _trace("scope_check", {"ok": True})}

    def quarantine(state: OpsState) -> OpsState:
        flags: list[str] = []
        for record in state.get("records") or []:
            note = record.get("customer_visible_note") if isinstance(record, dict) else None
            raw = note.get("flags") if isinstance(note, dict) else None
            for flag in raw if isinstance(raw, list) else []:
                if isinstance(flag, str) and flag not in flags:
                    flags.append(flag)
        return {
            "quarantine_flags": flags,
            "trace": _trace("quarantine", {"flags": flags, "flag_count": len(flags)}),
        }

    def compose_internal(state: OpsState) -> OpsState:
        order = _order(state)
        if order is None:
            return _stop("compose_internal", "error", None, ok=False)
        return {
            "draft_body": service_answer(order, erp),
            "trace": _trace("compose_internal", {"ok": True}),
        }

    def draft(state: OpsState) -> OpsState:
        order = _order(state)
        if order is None:
            return _stop("draft", "error", None, ok=False, error="no_order")
        # Built outside the try: a connector failure on the label lookups escapes to the
        # envelope, which logs it as connector_unavailable (as on the order_status path).
        facts = order_facts(order, erp)
        drafted: object = None
        unavailable = False
        try:
            drafted = drafter.draft(facts)
        except ModelUnavailableError:
            unavailable = True
        # Never raise or chain: a drafter's error may describe the facts.
        except Exception:
            drafted = None
        if unavailable:
            # The model could not be reached: like a connector failure, it escapes to the
            # envelope (logged error/connector_unavailable), raised here without a chain.
            raise ModelUnavailableError("draft:model_unavailable")
        if not isinstance(drafted, DraftBody):
            return _stop("draft", "error", None, ok=False, error="draft_failed")
        citations = [str(cited) for cited in drafted.cited_ids]
        return {
            "draft_body": drafted.body,
            "draft_citations": citations,
            "trace": _trace("draft", {"ok": True, "citation_count": len(citations)}),
        }

    def verify(state: OpsState) -> OpsState:
        order = _order(state)
        body = state.get("draft_body")
        citations = list(state.get("draft_citations") or [])
        if order is None or not isinstance(body, str):
            return _stop("verify", "error", None, ok=False)
        block = order_facts(order, erp).block
        if not verify_body(body, citations, order, block, _fetched_ids(state)):
            return _stop("verify", "withheld", "ungrounded", ok=False)
        return {"trace": _trace("verify", {"ok": True, "citation_count": len(citations)})}

    def compose(state: OpsState) -> OpsState:
        order = _order(state)
        body = state.get("draft_body")
        if order is None or not isinstance(body, str):
            return _stop("compose", "error", None, ok=False)
        contact = _contact(erp, order)
        if contact is None:
            return _stop("compose", "withheld", "cross_account", ok=False)
        return {
            "draft_body": compose_text(body, contact),
            "trace": _trace("compose", {"ok": True}),
        }

    def guard(state: OpsState) -> OpsState:
        order = _order(state)
        text = state.get("draft_body")
        if order is None or not isinstance(text, str):
            return _stop("guard", "error", None, ok=False)
        if not _in_scope(text, order.account_id, scope_index):
            return _stop("guard", "withheld", "cross_account", ok=False)
        extracts = [order_facts(order, erp).block]
        if state.get("flow") == "customer_reply":
            # The model's text meets the quarantine rules an operator's edit meets.
            if quarantine_stop(text, ctx):
                return _stop("guard", "withheld", "output_pi", ok=False, rule="quarantine")
            contact = _contact(erp, order)
            if contact is None:
                return _stop("guard", "withheld", "cross_account", ok=False)
            extracts.append(contact_extract(contact))
        verdict = check_output(
            text,
            redacted_question=state.get("request_redacted") or "",
            raw_values=(),
            cited_extracts=extracts,
            ctx=ctx,
        )
        if not verdict.ok:
            # The rule's name (a code, never a value) is kept for the evaluation's counts.
            return _stop("guard", "withheld", "output_pi", ok=False, rule=verdict.rule)
        update: OpsState = {"trace": _trace("guard", {"ok": True})}
        if state.get("flow") != "customer_reply":
            update["outcome"] = "answered"
            update["refusal"] = None
        return update

    def propose(state: OpsState) -> OpsState:
        order = _order(state)
        body = state.get("draft_body")
        if order is None or not isinstance(body, str):
            return _stop("propose", "error", None)
        action = _reply_action(order.order_id, body)
        citations = list(state.get("draft_citations") or [])
        return {
            "draft_id": uuid.uuid4().hex,
            "draft_sha256": text_sha256(body),
            "proposed_action": action.model_dump(mode="json"),
            "edit_round": 0,
            "trace": _trace(
                "propose",
                {
                    "tool": action.tool,
                    "edit_round": 0,
                    "citation_count": len(citations),
                    "ids": [order.order_id],
                },
            ),
        }

    builder: StateGraph[OpsState, None, OpsState, OpsState] = StateGraph(OpsState)
    nodes: dict[str, OpsNode] = {
        "route": route,
        "fetch": fetch,
        "scope_check": scope,
        "quarantine": quarantine,
        "compose_internal": compose_internal,
        "draft": draft,
        "verify": verify,
        "compose": compose,
        "guard": guard,
        "propose": propose,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", _after("fetch"), {"fetch": "fetch", END: END})
    builder.add_conditional_edges(
        "fetch", _after("scope_check"), {"scope_check": "scope_check", END: END}
    )
    builder.add_conditional_edges(
        "scope_check", _after("quarantine"), {"quarantine": "quarantine", END: END}
    )
    builder.add_conditional_edges(
        "quarantine",
        _after_quarantine,
        {"draft": "draft", "compose_internal": "compose_internal"},
    )
    builder.add_conditional_edges("compose_internal", _after("guard"), {"guard": "guard", END: END})
    builder.add_conditional_edges("draft", _after("verify"), {"verify": "verify", END: END})
    builder.add_conditional_edges("verify", _after("compose"), {"compose": "compose", END: END})
    builder.add_conditional_edges("compose", _after("guard"), {"guard": "guard", END: END})
    builder.add_conditional_edges("guard", _after_guard, {"propose": "propose", END: END})
    builder.add_conditional_edges(
        "propose", _after(APPROVE_NODE), {APPROVE_NODE: APPROVE_NODE, END: END}
    )
    add_approval_nodes(
        builder,
        executor=executor,
        check_edit=make_check_edit(erp=erp, ctx=ctx, scope_index=scope_index),
        max_edit_rounds=max_edit_rounds,
    )

    def build(saver: BaseCheckpointSaver[str]) -> OpsGraph:
        return builder.compile(checkpointer=saver)

    return build
