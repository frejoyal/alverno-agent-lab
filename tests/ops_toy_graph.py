"""Test-only toy graph of module 3 and its deliberately broken variants (I5 spec decision 8).

The toy is 3a-like and runs on the real parts: the simulated ERP of ``data/eval/ops/``
(with a stub quarantine), :class:`~alverno_lab.tools.executor.ToolExecutor`,
:class:`~alverno_lab.tools.authority.ApprovalAuthority` and the shared approval tail::

    route → fetch → [customer_reply] propose → approve ⇄ reguard → apply → handoff → END
                    [order_status]   answer → END
    route / fetch → refuse → END   (no order id; unknown order: record_not_found)

It has no model: ``propose`` and ``answer`` fill fixed fictional templates with the order
id (and its status code). Every part is wrapped in the probes of
:mod:`~alverno_lab.agents.ops.probes`, so a test reads one timeline of log writes,
executor calls and connector effects.

Each variant removes one barrier with one small, explicit change, for the negative
controls (ADR 0013 « Barrières et contrôles négatifs »):

- ``approve_bypassed``: ``propose`` goes straight to ``apply`` (and marks the decision
  approved itself); the executor is intact, so the write is attempted without a token;
- ``stale_hash_apply``: ``apply`` calls the executor with arguments the operator never
  approved (a stale draft digest) and the real token;
- ``effect_in_approve``: the ``approve`` node writes through the connector before its
  interrupt, so the write runs again on every resume;
- ``non_consuming_verifier``: the executor's verifier never spends a nonce, and ``apply``
  replays its call once (an at-least-once retry); with the intact verifier that replay
  is refused, with this double it executes again;
- ``reguard_bypassed``: an edit goes from ``approve`` straight back to ``approve``; the
  intact envelope refuses that re-presentation (same round, same action id), so the
  metric control pairs it with :class:`LaxPresentationEnvelope`.

Three envelope variants complete them: :class:`KeepThreadEnvelope` skips
``delete_thread``, :class:`LateDecisionLogEnvelope` writes the decision record after
the resume instead of before it, and :class:`LaxPresentationEnvelope` skips the
re-presentation rules. :class:`PayloadSpyEnvelope` is the intact envelope
keeping the interrupt payloads, :class:`CountingAuthority` the real authority counting
its tokens, and :class:`WriteFailingErp` an ERP whose write is down. Fixture ids are
picked by rule, never typed in; no holdout set is read.
"""

from __future__ import annotations

import dataclasses
import re
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

import langsmith.utils as ls_utils
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from alverno_lab.agents.ops import (
    APPLY_NODE,
    APPROVE_NODE,
    HANDOFF_NODE,
    REGUARD_NODE,
    ApprovalDecision,
    CountingConnector,
    CountingExecutor,
    EditCheck,
    OpsState,
    ProposedAction,
    Timeline,
    TimelineLog,
    make_apply_node,
    make_approve_node,
    make_handoff_node,
    make_reguard_node,
    route_after_approve,
    route_after_reguard,
    scrub_entry,
    text_sha256,
)
from alverno_lab.agents.ops.approval import EditChecker, OpsNode, ToolCaller
from alverno_lab.agents.ops.runtime import (
    DraftHeader,
    OpsEnvelope,
    OpsGraph,
    RequestContext,
)
from alverno_lab.connectors import (
    ConnectorError,
    ContactView,
    OrderView,
    QuarantinedText,
    SimErp,
    WriteReceipt,
    WriteToken,
    WriteToolName,
)
from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.privacy.access_log import AccessLog
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord, OpsEvent
from alverno_lab.privacy.ops_policy import OpsRefusalPolicy
from alverno_lab.privacy.ops_screen import screen_service
from alverno_lab.privacy.ops_types import OpsRefusalCode
from alverno_lab.tools import (
    ApprovalAuthority,
    ApprovalToken,
    ApprovalVerifier,
    RecordReplyPreparedArgs,
    ToolExecutor,
    ToolResult,
    VerifyResult,
)

from .ops_privacy_helpers import ops_privacy_ctx

REPO_ROOT = Path(__file__).parent.parent
OPS = REPO_ROOT / "data" / "eval" / "ops"
RECORD_REPLY: WriteToolName = "erp.record_reply_prepared"
# An order id of the right shape that the fixture does not hold (checked in the world).
UNKNOWN_ORDER = "CMD-1999-9999"
_ORDER_RE = re.compile(r"CMD-[0-9]{4}-[0-9]{4}")
_REPLY_WORDS = ("réponse", "répondre", "brouillon")

# Fictional templates: the order id (and its status code) is the only fixture value.
BODY = "Bonjour, voici le suivi de votre commande {order}. Merci de votre confiance."
ANSWER = "La commande {order} est au statut « {status} »."
STALE_BODY = "Texte qui n'a jamais été approuvé pour la commande {order}."
# The operator header of the toy: fixed fictional values (I6 reads the contact by code).
HEADER_ACCOUNT = "Compte fictif du labo"
HEADER_FUNCTION = "Responsable des achats"
# An edited body with this marker fails the toy's output check (« output_pi »).
PI_MARKER = "[courriel]"

ToyVariant = Literal[
    "intact",
    "approve_bypassed",
    "stale_hash_apply",
    "effect_in_approve",
    "non_consuming_verifier",
    "reguard_bypassed",
]
TOY_VARIANTS: tuple[ToyVariant, ...] = (
    "intact",
    "approve_bypassed",
    "stale_hash_apply",
    "effect_in_approve",
    "non_consuming_verifier",
    "reguard_bypassed",
)


def stub_quarantine(text: str) -> QuarantinedText:
    """Stub quarantine: a fixed placeholder, so no fixture text reaches a view."""
    return QuarantinedText(text="texte mis en quarantaine", flags=("stub",))


# --------------------------------------------------------------------------- doubles
class CountingAuthority(ApprovalAuthority):
    """The real authority, counting the tokens it mints."""

    __slots__ = ("minted",)

    def __init__(self, key: bytes | None = None) -> None:
        super().__init__(key)
        self.minted = 0

    def mint(self, action_id: str, tool: WriteToolName, args_sha256: str) -> ApprovalToken:
        """Count, then mint."""
        self.minted += 1
        return super().mint(action_id, tool, args_sha256)


class NonConsumingVerifier(ApprovalVerifier):
    """Broken double: ``redeem`` verifies but never spends the nonce."""

    __slots__ = ()

    def redeem(self, token: ApprovalToken, tool: str, args_sha256: str) -> VerifyResult:
        """Verify only: the same token can run again."""
        return self.verify(token, tool, args_sha256)


class StaleArgsCaller:
    """Broken ``apply`` executor: the token goes out with arguments nobody approved."""

    def __init__(self, inner: ToolCaller, order_id: str) -> None:
        self.inner = inner
        self.stale_sha256 = text_sha256(STALE_BODY.format(order=order_id))

    def call(
        self, name: str, args: Mapping[str, object], *, token: ApprovalToken | None = None
    ) -> ToolResult:
        """Swap the draft digest of a reply, then delegate."""
        if name == RECORD_REPLY:
            args = {**args, "draft_sha256": self.stale_sha256}
        return self.inner.call(name, args, token=token)


class ReplayingCaller:
    """``apply`` executor that replays every write once (an at-least-once retry)."""

    def __init__(self, inner: ToolCaller) -> None:
        self.inner = inner

    def call(
        self, name: str, args: Mapping[str, object], *, token: ApprovalToken | None = None
    ) -> ToolResult:
        """Call, and call a write once more with the same token; return the first result."""
        first = self.inner.call(name, args, token=token)
        if name == RECORD_REPLY:
            self.inner.call(name, args, token=token)
        return first


@dataclasses.dataclass(frozen=True)
class StandInToken:
    """What the broken ``approve`` node hands the connector: ids only, no MAC."""

    action_id: str
    tool: str
    args_sha256: str


class WriteFailingErp:
    """An ERP whose reads work and whose write fails (a connector outage at ``apply``)."""

    def __init__(self, inner: SimErp) -> None:
        self.inner = inner

    def get_order(self, order_id: str) -> OrderView | None:
        """The real read."""
        return self.inner.get_order(order_id)

    def get_order_contact(self, order_id: str) -> ContactView | None:
        """The real read."""
        return self.inner.get_order_contact(order_id)

    def record_reply_prepared(
        self, order_id: str, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        """Always fails, as a connector that is down."""
        raise ConnectorError("toy:write_down")


# --------------------------------------------------------------------------- world
class ToyWorld:
    """The real ERP, counted, behind the real executor and authority; one timeline."""

    def __init__(
        self, *, variant: ToyVariant = "intact", with_erp: bool = True, write_fails: bool = False
    ) -> None:
        self.variant = variant
        self.erp = SimErp.from_yaml(OPS / "erp.yaml", quarantine=stub_quarantine)
        assert UNKNOWN_ORDER not in self.erp.order_ids, "toy:unknown_order_exists"
        ids = sorted(self.erp.order_ids)
        noted = [i for i in ids if (view := self.erp.get_order(i)) and view.customer_visible_note]
        # An order with a customer note, so that its quarantine flag reaches the log.
        self.order_id = noted[0] if noted else ids[0]
        self.timeline = Timeline()
        erp: ErpConnector | None = None
        if with_erp:
            erp = WriteFailingErp(self.erp) if write_fails else self.erp
        self.connector = CountingConnector(timeline=self.timeline, erp=erp)
        key = secrets.token_bytes(32)
        self.authority = CountingAuthority(key)
        verifier: ApprovalVerifier = (
            NonConsumingVerifier(key)
            if variant == "non_consuming_verifier"
            else self.authority.verifier
        )
        self.executor = CountingExecutor(
            ToolExecutor(self.connector, self.connector, verifier), self.timeline
        )

    def body(self, template: str = BODY) -> str:
        """A fixed fictional text about the toy's order."""
        return template.format(order=self.order_id)

    def reply_request(self, order_id: str | None = None) -> str:
        """A customer-reply request that passes the service screen."""
        return f"Prépare une réponse au client pour la commande {order_id or self.order_id}."

    def status_request(self) -> str:
        """An order-status request that passes the service screen."""
        return f"Quel est le statut de la commande {self.order_id} ?"


def reply_action(order_id: str, body: str) -> ProposedAction:
    """The reply-prepared action of a body: the order and the body's digest."""
    args = RecordReplyPreparedArgs(order_id=order_id, draft_sha256=text_sha256(body))
    return ProposedAction.build(RECORD_REPLY, args, summary_fr="Réponse préparée")


def toy_check_edit(state: OpsState, decision: ApprovalDecision) -> EditCheck:
    """The toy's edit check: a text edit without the PI marker passes; nothing else does."""
    text = decision.edited_text
    if text is None:
        return EditCheck(ok=False, reason="invalid_args")
    if PI_MARKER in text:
        return EditCheck(ok=False, reason="output_pi")
    ids = state.get("ids") or []
    action = reply_action(ids[0], text)
    return EditCheck(ok=True, draft_body=text, proposed_action=action.model_dump(mode="json"))


def toy_header(state: OpsState) -> DraftHeader:
    """The toy's operator header: fixed fictional values and the cited ids."""
    return DraftHeader(
        account_label=HEADER_ACCOUNT,
        recipient_function=HEADER_FUNCTION,
        cited_ids=tuple(state.get("draft_citations") or ()),
    )


# --------------------------------------------------------------------------- graph
def build_toy_graph(
    world: ToyWorld,
    *,
    tracing_probe: list[tuple[str, bool]] | None = None,
    fetch_raises: Exception | None = None,
    max_edit_rounds: int = 2,
    check_edit: EditChecker | None = None,
) -> Callable[[InMemorySaver], OpsGraph]:
    """The ``build_graph`` of an envelope: the toy graph of ``world.variant``.

    ``tracing_probe`` receives ``(node, langsmith tracing enabled)`` from every node;
    ``fetch_raises`` makes ``fetch`` raise that exception after ``route`` has run;
    ``check_edit`` replaces :func:`toy_check_edit` in ``reguard``.
    """
    variant = world.variant
    executor: ToolCaller = world.executor

    def recorded(name: str, node: OpsNode) -> OpsNode:
        if tracing_probe is None:
            return node

        def probe(state: OpsState) -> OpsState:
            tracing_probe.append((name, bool(ls_utils.tracing_is_enabled())))
            return node(state)

        return probe

    def route(state: OpsState) -> OpsState:
        text = state.get("request_redacted") or ""
        ids = list(dict.fromkeys(_ORDER_RE.findall(text)))
        if not ids:
            return {
                "outcome": "refused",
                "refusal": "order_not_identified",
                "trace": [scrub_entry("route", {"refusal": "order_not_identified"})],
            }
        flow = (
            "customer_reply" if any(w in text.casefold() for w in _REPLY_WORDS) else "order_status"
        )
        return {
            "flow": flow,
            "ids": ids[:1],
            "trace": [scrub_entry("route", {"flow": flow, "id_count": len(ids)})],
        }

    def fetch(state: OpsState) -> OpsState:
        if fetch_raises is not None:
            raise fetch_raises
        order_id = (state.get("ids") or [""])[0]
        result = world.executor.call("erp.get_order", {"order_id": order_id})
        events = list(state.get("tool_events") or [])
        events.append({"tool": result.tool, "ok": result.ok, "error": result.error})
        detail = {"tool": result.tool, "ok": result.ok, "error": result.error}
        if not result.ok:
            return {
                "tool_events": events,
                "outcome": "error",
                "refusal": "connector_unavailable",
                "trace": [scrub_entry("fetch", detail)],
            }
        if result.data is None:
            return {
                "tool_events": events,
                "outcome": "refused",
                "refusal": "record_not_found",
                "trace": [scrub_entry("fetch", {**detail, "refusal": "record_not_found"})],
            }
        view = result.data
        assert not isinstance(view, tuple), "toy:order_view"
        note = getattr(view, "customer_visible_note", None)
        flags = list(note.flags) if note is not None else []
        return {
            "tool_events": events,
            "records": [view.model_dump(mode="json")],
            "quarantine_flags": flags,
            "trace": [scrub_entry("fetch", {**detail, "record_count": 1})],
        }

    def answer(state: OpsState) -> OpsState:
        record = (state.get("records") or [{}])[0]
        body = ANSWER.format(order=record.get("order_id"), status=record.get("status"))
        return {
            "draft_body": body,
            "outcome": "answered",
            "trace": [scrub_entry("compose_internal", {"ok": True})],
        }

    def refuse(state: OpsState) -> OpsState:
        return {"trace": [scrub_entry("refuse", {"refusal": state.get("refusal")})]}

    def propose(state: OpsState) -> OpsState:
        order_id = (state.get("ids") or [""])[0]
        body = BODY.format(order=order_id)
        action = reply_action(order_id, body)
        update: OpsState = {
            "draft_id": secrets.token_hex(16),
            "draft_body": body,
            "draft_sha256": text_sha256(body),
            "draft_citations": [order_id],
            "edit_round": 0,
            "proposed_action": action.model_dump(mode="json"),
            "trace": [scrub_entry("propose", {"tool": RECORD_REPLY, "edit_round": 0})],
        }
        if variant == "approve_bypassed":
            # BROKEN: the graph marks its own action approved and skips the interrupt.
            update["decision"] = {"decision": "approve"}
        return update

    def after_route(state: OpsState) -> str:
        return "refuse" if state.get("outcome") is not None else "fetch"

    def after_fetch(state: OpsState) -> str:
        if state.get("outcome") is not None:
            return "refuse"
        return "propose" if state.get("flow") == "customer_reply" else "answer"

    approve: OpsNode = make_approve_node(max_edit_rounds=max_edit_rounds)
    if variant == "effect_in_approve":
        approve = _effect_before_interrupt(world, approve)
    apply_executor: ToolCaller = executor
    if variant == "stale_hash_apply":
        apply_executor = StaleArgsCaller(executor, world.order_id)
    elif variant == "non_consuming_verifier":
        apply_executor = ReplayingCaller(executor)

    builder: StateGraph[OpsState, None, OpsState, OpsState] = StateGraph(OpsState)
    nodes: dict[str, OpsNode] = {
        "route": route,
        "fetch": fetch,
        "answer": answer,
        "refuse": refuse,
        "propose": propose,
        APPROVE_NODE: approve,
        REGUARD_NODE: make_reguard_node(check_edit or toy_check_edit),
        APPLY_NODE: make_apply_node(apply_executor),
        HANDOFF_NODE: make_handoff_node(),
    }
    for name, node in nodes.items():
        builder.add_node(name, recorded(name, node))
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", after_route, {"refuse": "refuse", "fetch": "fetch"})
    builder.add_conditional_edges(
        "fetch", after_fetch, {"refuse": "refuse", "propose": "propose", "answer": "answer"}
    )
    builder.add_edge("answer", END)
    builder.add_edge("refuse", END)
    # BROKEN when bypassed: the approved-by-itself action goes straight to apply.
    builder.add_edge("propose", APPLY_NODE if variant == "approve_bypassed" else APPROVE_NODE)
    # BROKEN when bypassed: an edit loops back to approve without its checks.
    edit_target = APPROVE_NODE if variant == "reguard_bypassed" else REGUARD_NODE
    builder.add_conditional_edges(
        APPROVE_NODE,
        route_after_approve,
        {APPLY_NODE: APPLY_NODE, REGUARD_NODE: edit_target, END: END},
    )
    builder.add_conditional_edges(
        REGUARD_NODE, route_after_reguard, {APPROVE_NODE: APPROVE_NODE, END: END}
    )
    builder.add_edge(APPLY_NODE, HANDOFF_NODE)
    builder.add_edge(HANDOFF_NODE, END)

    def build(saver: InMemorySaver) -> OpsGraph:
        return builder.compile(checkpointer=saver)

    return build


def _effect_before_interrupt(world: ToyWorld, approve: OpsNode) -> OpsNode:
    """BROKEN: a write placed in ``approve`` before its interrupt (re-run on resume)."""

    def approve_with_effect(state: OpsState) -> OpsState:
        action = ProposedAction.model_validate(state.get("proposed_action"))
        token = StandInToken(action.action_id, action.tool, action.args_sha256)
        world.connector.record_reply_prepared(
            str(action.args["order_id"]), str(action.args["draft_sha256"]), token=token
        )
        return approve(state)

    return approve_with_effect


# --------------------------------------------------------------------------- envelope
class KeepThreadEnvelope(OpsEnvelope):
    """BROKEN: ``delete_thread`` is skipped, so the request's state stays in memory."""

    def _delete_thread(self, thread_id: str) -> None:
        return None


LateRecord = tuple[RequestContext, OpsEvent, OpsRefusalCode | None, ProposedAction | None]


class LateDecisionLogEnvelope(OpsEnvelope):
    """BROKEN: the decision record is written after the resume, not before it."""

    def _late(self) -> list[LateRecord]:
        held: list[LateRecord] = self.__dict__.setdefault("late_records", [])
        return held

    def _log_decision(
        self,
        ctx: RequestContext,
        event: OpsEvent,
        *,
        reason: OpsRefusalCode | None,
        action: ProposedAction | None,
    ) -> None:
        self._late().append((ctx, event, reason, action))

    def _invoke(self, ctx: RequestContext, value: OpsState | Command[str]) -> Mapping[str, object]:
        result = super()._invoke(ctx, value)
        late = self._late()
        while late:
            late_ctx, event, reason, action = late.pop(0)
            super()._log_decision(late_ctx, event, reason=reason, action=action)
        return result


class LaxPresentationEnvelope(OpsEnvelope):
    """BROKEN: the re-presentation rules are skipped (any round, any action id)."""

    def _check_presentation(
        self,
        step: str,
        ctx: RequestContext,
        state: OpsState,
        action: ProposedAction,
        previous: ProposedAction | None,
        shown_round: int,
    ) -> None:
        return None


class PayloadSpyEnvelope(OpsEnvelope):
    """The intact envelope, keeping each interrupt payload for the tests to inspect."""

    @property
    def payloads(self) -> list[object]:
        """Every interrupt payload so far, in order."""
        held: list[object] = self.__dict__.setdefault("spied_payloads", [])
        return held

    def _invoke(self, ctx: RequestContext, value: OpsState | Command[str]) -> Mapping[str, object]:
        result = super()._invoke(ctx, value)
        interrupts = result.get("__interrupt__")
        if isinstance(interrupts, list | tuple):
            self.payloads.extend(getattr(item, "value", None) for item in interrupts)
        return result


def toy_log(directory: Path, world: ToyWorld) -> TimelineLog:
    """The service access log under ``directory``, adding its writes to the world's timeline."""
    return TimelineLog(directory, prefix="service", timeline=world.timeline)


def make_envelope(
    world: ToyWorld,
    log: AccessLog[OpsAccessLogRecord],
    *,
    envelope_cls: type[OpsEnvelope] = OpsEnvelope,
    build_graph: Callable[[InMemorySaver], OpsGraph] | None = None,
    tracing_probe: list[tuple[str, bool]] | None = None,
    max_edit_rounds: int = 2,
    model_name: Callable[[], str | None] | None = None,
    chat_attempts: Callable[[], int] | None = None,
    clock: Callable[[], float] | None = None,
) -> OpsEnvelope:
    """An envelope over the toy graph, with the real service screen and policy."""
    ctx = ops_privacy_ctx()
    graph = build_graph or build_toy_graph(
        world, tracing_probe=tracing_probe, max_edit_rounds=max_edit_rounds
    )
    return envelope_cls(
        graph,
        module="service",
        screen=lambda text: screen_service(text, ctx),
        log=log,
        authority=world.authority,
        policy=OpsRefusalPolicy("service"),
        channel="eval",
        header_for=toy_header,
        max_edit_rounds=max_edit_rounds,
        model_name=model_name or (lambda: None),
        chat_attempts=chat_attempts or (lambda: 0),
        clock=clock or time.perf_counter,
    )
