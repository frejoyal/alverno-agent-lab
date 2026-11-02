"""The envelope of a module 3 request: screen, graph, access log, token (ADR 0013, ADR 0014).

:class:`OpsEnvelope` runs one request at a time through a compiled ops graph and owns
everything a node must not do: writing the access log, minting the approval token and
destroying the request's state. Its order is fixed (ADR 0013 « Ordre de l'enveloppe »)
and fails closed:

1. :meth:`OpsEnvelope.start`: ``check_tracing_environment()``; uuid4 request and thread
   ids (never logged); the request screen. A screen refusal is logged ``refused`` and
   its policy message returned: the graph is never invoked, so no connector and no
   model is called. Otherwise the graph runs under ``tracing_context(enabled=False)``.
   On an interrupt the draft is read by ``graph.get_state``, ``drafted`` is written,
   and only then is the draft returned (``pending``) with its operator header. On the
   end of the graph its record (``answered``, ``refused``, ``withheld`` or ``error``)
   is written before its text is returned.
2. :meth:`OpsEnvelope.decide`: the decision is validated with
   :class:`~alverno_lab.agents.ops.approval.ApprovalDecision` (an edit past the last
   round, or a decision that brings its own token, is invalid). The decision record
   (``approved``, ``edit_submitted``, or ``rejected`` with ``approval_rejected`` or
   ``invalid_decision``) is written **first**; the token is minted **after** it, and
   only on an approval, from the presented action held in state; the graph is then
   resumed, under the same tracing rules. A new interrupt is logged ``re_presented``
   before it is returned. ``withheld`` and ``error`` endings get their record; an
   ``executed`` or ``rejected`` ending gets none (the decision record is final).
3. :meth:`OpsEnvelope.abandon` (end of input, Ctrl-C): ``abandoned`` is written.
4. The thread is deleted from the checkpointer in a ``finally`` block on every path but
   ``pending``, exceptions included.

One step runs at a time: each claims the envelope under a lock (``<step>:busy`` while
another runs, ``start:pending`` while a draft waits), and ``decide``/``abandon`` accept
the shown ``draft_id`` so that a decision about another draft is refused before anything
is logged. Every presented draft is checked against the protocol before ``drafted`` or
``re_presented`` is written: round 0 first, then one round more per logged edit, a new
``action_id``, and a reply action bound to the digest of the text shown. An ending after
a resume must match the logged decision (a rejection only after a logged rejection or an
invalid decision), and an exception of any kind escaping a step after the screen gets an
``error`` record unless a closing record was already written, so every draft ends with a
terminal record.

A failed log write (:class:`~alverno_lab.privacy.access_log.AccessLogError`) anywhere
gives the ``audit_unavailable`` turn: nothing is shown, no token is minted, the graph is
not resumed and no other record is attempted. An exception escaping the graph (an
unknown thread, a validation error, the recursion limit, a connector failure, any
other) is logged ``error`` (``connector_unavailable`` for a connector; with an empty flow
when it comes before routing, ADR 0014 « Précisions ») and re-raised as
:class:`OpsRuntimeError` ``<step>:<rule>``, outside the ``except`` block, so that no
exception chain keeps an input value.
``LegacyTracingError`` propagates unchanged, as in modules 1 and 2.

The checkpointer is an :class:`~langgraph.checkpoint.memory.InMemorySaver` whose
serializer refuses ``pickle`` and every msgpack type outside LangGraph's safe set,
**explicitly in its constructor** (stronger than the ``LANGGRAPH_STRICT_MSGPACK``
variable, which LangGraph reads once at import). When that variable is true, ``compile``
hands the graph a shallow copy of the saver whose allowlist adds LangGraph's curated
types for the graph's schemas; the envelope accepts that copy, and uses it, only while it
shares the saver's storage and still refuses pickle and unlisted types. The state is JSON
primitives only (:mod:`~alverno_lab.agents.ops.state`), so nothing needs either. The
graph's ``approve`` node must use the envelope's ``max_edit_rounds``: a readable
mismatch is refused at construction (``envelope:max_edit_rounds``).

The envelope never writes a text, a digest of a text (``action_args_sha256`` aside), a
contact value or an edit to the log or to a turn's trace.
"""

from __future__ import annotations

import dataclasses
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Literal, get_args

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from alverno_lab.agents.ops.approval import (
    APPROVE_NODE,
    INTERRUPT_KEYS,
    ApprovalDecision,
    approve_node_rounds,
    parse_decision,
    text_bound,
)
from alverno_lab.agents.ops.scrub import scrub_entry
from alverno_lab.agents.ops.state import OpsState, ProposedAction, is_json_value
from alverno_lab.agents.quality.graph import LegacyTracingError, check_tracing_environment
from alverno_lab.connectors.types import RECORD_REPLY_TOOL, ConnectorError
from alverno_lab.privacy.access_log import PI_COUNT_CAP, AccessLog, AccessLogError
from alverno_lab.privacy.ops_access_log import (
    ACCOUNT_PATTERN,
    APPROVAL_FLOWS,
    ENTITY_PATTERN,
    FLAG_PATTERN,
    MAX_ENTITY_REFS,
    OpsAccessLogRecord,
    OpsEvent,
)
from alverno_lab.privacy.ops_policy import OpsRefusalPolicy
from alverno_lab.privacy.ops_screen import OpsScreening
from alverno_lab.privacy.ops_types import OPS_REFUSAL_CODES, Flow, OpsFlag, OpsRefusalCode
from alverno_lab.privacy.types import PIType
from alverno_lab.tools.authority import ApprovalAuthority, ApprovalToken
from alverno_lab.tools.registry import TOOL_NAMES

__all__ = [
    "DRAFT_NOTICE",
    "RECURSION_LIMIT",
    "DraftHeader",
    "DraftNotice",
    "OpsEnvelope",
    "OpsGraph",
    "OpsRuntimeError",
    "OpsTurn",
    "OpsTurnKind",
    "RequestContext",
]

RECURSION_LIMIT = 25
DraftNotice = Literal["Brouillon préparé par un outil automatisé — à relire"]
DRAFT_NOTICE: DraftNotice = "Brouillon préparé par un outil automatisé — à relire"

OpsTurnKind = Literal[
    "answered",
    "refused",
    "withheld",
    "pending",
    "executed",
    "rejected",
    "abandoned",
    "error",
    "audit_unavailable",
]
OpsGraph = CompiledStateGraph[OpsState, None, OpsState, OpsState]
DecisionKind = Literal["approve", "edit", "reject", "invalid"]

_HEX32 = re.compile(r"[0-9a-f]{32}")
_ENTITY = re.compile(ENTITY_PATTERN)
_ACCOUNT = re.compile(ACCOUNT_PATTERN)
_FLAG = re.compile(FLAG_PATTERN)
_FLOWS: frozenset[str] = frozenset(get_args(Flow))
_TOOLS: frozenset[str] = frozenset(TOOL_NAMES)
# The resume value of an invalid decision: a fixed marker the approve node turns into
# ``invalid_decision``. Never the raw value (it would enter the checkpoint), never None
# or an empty dict (LangGraph reads those as no resume, or as a resume map).
_INVALID_RESUME: dict[str, JsonValue] = {"decision": "invalid"}
# Graph endings each step may meet, and the record each one is due (None: no record,
# the decision record written before the resume is final, ADR 0014).
_START_ENDINGS: Mapping[str, OpsEvent | None] = {
    "answered": "answered",
    "refused": "refused",
    "withheld": "withheld",
    "error": "error",
}
_DECIDE_ENDINGS: Mapping[str, OpsEvent | None] = {
    "executed": None,
    "rejected": None,
    "withheld": "withheld",
    "error": "error",
}
# The logged decisions each ending after a resume may follow: an ending the envelope did
# not log a decision for (a rejection after a logged edit, say) is a protocol violation.
_DECIDE_AFTER: Mapping[str, frozenset[DecisionKind]] = {
    "executed": frozenset({"approve"}),
    "rejected": frozenset({"reject", "invalid"}),
    "withheld": frozenset({"edit"}),
    "error": frozenset({"approve", "edit", "reject", "invalid"}),
}
# The refusal a rejection must carry, by logged decision (the decision record's reason).
_REJECTION_REASON: Mapping[DecisionKind | None, OpsRefusalCode] = {
    "reject": "approval_rejected",
    "invalid": "invalid_decision",
}
# Records after which a draft needs no other: an exception escaping a step after one of
# them writes nothing more. ``approved`` is not one: an error after it is still logged.
_CLOSING_EVENTS: frozenset[str] = frozenset(
    {"answered", "refused", "withheld", "rejected", "abandoned", "error"}
)


class OpsRuntimeError(RuntimeError):
    """The envelope could not complete a step. The message is ``<step>:<rule>``, no value."""


class DraftHeader(BaseModel):
    """The operator header shown above a pending draft (ADR 0013, « Responsabilité »).

    The account label and the recipient's function are shown to the operator only: they
    are kept out of ``repr`` and never reach a prompt, the log or a trace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    account_label: str = Field(repr=False)
    recipient_function: str = Field(repr=False)
    cited_ids: tuple[str, ...] = Field(default=(), max_length=MAX_ENTITY_REFS)
    notice: DraftNotice = DRAFT_NOTICE


class OpsTurn(BaseModel):
    """What one envelope step returns to the CLI or the evaluation.

    ``text`` is the served text (an answer, the draft to review, the approved draft to
    send by hand) or the policy message of a refusal; it is kept out of ``repr``, like
    the header and the trace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    kind: OpsTurnKind
    text: str | None = Field(default=None, repr=False)
    reason: OpsRefusalCode | None = None
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    header: DraftHeader | None = Field(default=None, repr=False)
    edit_round: int = Field(default=0, ge=0, le=2)
    flags: tuple[OpsFlag, ...] = ()
    trace: tuple[dict[str, JsonValue], ...] = Field(default=(), repr=False)


@dataclasses.dataclass(eq=False)
class RequestContext:
    """Bookkeeping of one request inside the envelope; never logged as such.

    ``thread_id`` and ``request_id`` stay in memory; the other fields are what the next
    record needs (the flow, the ids, the presented action, what changed since the last
    record).
    """

    request_id: str
    thread_id: str
    flags: tuple[OpsFlag, ...] = ()
    pi_types: tuple[PIType, ...] = ()
    pi_count: int = 0
    mark: float = 0.0
    flow: str | None = None
    draft_id: str | None = None
    entity_refs: tuple[str, ...] = ()
    account_ref: str | None = None
    quarantine_flags: tuple[str, ...] = ()
    tool_events: tuple[str, ...] = ()
    tool_events_seen: int = 0
    chat_seen: int = 0
    action: ProposedAction | None = None
    edit_round: int = 0
    decision: DecisionKind | None = None
    closed: bool = False

    @property
    def config(self) -> RunnableConfig:
        """The LangGraph config of this request's thread."""
        return {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }


class _Abort(Exception):
    """A protocol violation found by the envelope itself; ``rule`` is a code."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


@dataclasses.dataclass(frozen=True)
class _Failure:
    rule: str
    reason: OpsRefusalCode | None = None


@dataclasses.dataclass(frozen=True)
class _Ending:
    """A checked graph result: an interrupt to present, or an outcome to log and serve."""

    state: OpsState
    outcome: str | None = None
    refusal: OpsRefusalCode | None = None
    action: ProposedAction | None = None


def _classify(exc: Exception) -> _Failure:
    """The rule (and refusal reason) of an exception; the exception itself is dropped."""
    if isinstance(exc, _Abort):
        return _Failure(exc.rule)
    if isinstance(exc, ConnectorError):
        return _Failure("connector_unavailable", "connector_unavailable")
    if isinstance(exc, KeyError):
        return _Failure("unknown_thread")
    if isinstance(exc, ValidationError):
        return _Failure("invalid_value")
    if isinstance(exc, GraphRecursionError):
        return _Failure("recursion_limit")
    return _Failure("graph_error")


def _strict_saver() -> InMemorySaver:
    """One in-memory checkpointer: no pickle, msgpack restricted to LangGraph's safe set."""
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
    return InMemorySaver(serde=serde)


def _compiled_saver(saver: InMemorySaver, compiled: object) -> InMemorySaver | None:
    """The saver the graph was compiled with, when it is ``saver`` or a strict clone of it.

    With ``LANGGRAPH_STRICT_MSGPACK=true``, ``compile`` replaces the saver by a shallow
    copy whose serializer adds LangGraph's curated types for the graph's schemas. The
    copy shares the storage, the writes and the blobs; it is accepted only while its
    serializer still refuses pickle and keeps an allowlist (never "every type").
    """
    if compiled is saver:
        return saver
    if not isinstance(compiled, InMemorySaver):
        return None
    serde = compiled.serde
    if (
        compiled.storage is not saver.storage
        or compiled.writes is not saver.writes
        or compiled.blobs is not saver.blobs
        or not isinstance(serde, JsonPlusSerializer)
        or serde.pickle_fallback is not False
        or getattr(serde, "_allowed_msgpack_modules", True) is True
    ):
        return None
    return compiled


def _graph_edit_rounds(graph: OpsGraph) -> int | None:
    """The edit-round limit of the graph's ``approve`` node, when it can be read."""
    try:
        spec = graph.builder.nodes.get(APPROVE_NODE)
        return approve_node_rounds(getattr(spec, "runnable", None))
    # A graph built some other way simply cannot be checked here; _check still is.
    except Exception:
        return None


def _no_header(state: OpsState) -> DraftHeader | None:
    return None


def _no_model() -> str | None:
    return None


def _no_attempts() -> int:
    return 0


def _unique(values: object, pattern: re.Pattern[str], limit: int | None = None) -> tuple[str, ...]:
    """The strings of ``values`` matching ``pattern``, deduplicated in order."""
    if type(values) is not list:
        return ()
    kept: list[str] = []
    for value in values:
        if type(value) is str and pattern.fullmatch(value) is not None and value not in kept:
            kept.append(value)
    return tuple(kept[:limit] if limit is not None else kept)


def _action(value: object) -> ProposedAction | None:
    try:
        return ProposedAction.model_validate(value)
    # Never chain: the error would describe the arguments.
    except Exception:
        return None


def _refusal(value: object) -> OpsRefusalCode | None:
    if type(value) is str and value in OPS_REFUSAL_CODES:
        return value  # type: ignore[return-value]  # checked against the Literal's values
    return None


class OpsEnvelope:
    """Runs module 3 requests through one compiled graph, in the ADR 0013 order.

    ``build_graph`` receives the envelope's checkpointer and must compile the graph with
    it. ``screen`` is the sub-module's request screen. ``header_for`` builds the operator
    header of a pending draft from state (the 3a pipeline reads the contact by code).
    ``model_name`` and ``chat_attempts`` fill the ``model`` field: a non-decision record
    names the model when the attempt count grew since the previous record.
    ``max_edit_rounds`` is ``ops_max_edit_rounds`` (1 or 2).
    """

    def __init__(
        self,
        build_graph: Callable[[InMemorySaver], OpsGraph],
        *,
        module: Literal["service", "maint"],
        screen: Callable[[str], OpsScreening],
        log: AccessLog[OpsAccessLogRecord],
        authority: ApprovalAuthority,
        policy: OpsRefusalPolicy,
        channel: Literal["cli", "eval"],
        header_for: Callable[[OpsState], DraftHeader | None] = _no_header,
        model_name: Callable[[], str | None] = _no_model,
        chat_attempts: Callable[[], int] = _no_attempts,
        max_edit_rounds: int = 2,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        rule: str | None = None
        if module not in ("service", "maint"):
            rule = "module"
        elif channel not in ("cli", "eval"):
            rule = "channel"
        elif not isinstance(policy, OpsRefusalPolicy) or policy.module != module:
            rule = "policy"
        elif not isinstance(log, AccessLog) or log.prefix != module:
            rule = "log"
        elif not isinstance(authority, ApprovalAuthority):
            rule = "authority"
        elif type(max_edit_rounds) is not int or not 1 <= max_edit_rounds <= 2:
            rule = "max_edit_rounds"
        if rule is not None:
            raise OpsRuntimeError(f"envelope:{rule}")
        self.module: Literal["service", "maint"] = module
        self.channel: Literal["cli", "eval"] = channel
        self.policy = policy
        self.log = log
        self.authority = authority
        self.max_edit_rounds = max_edit_rounds
        self._screen = screen
        self._header_for = header_for
        self._model_name = model_name
        self._chat_attempts = chat_attempts
        self._clock = clock
        saver = _strict_saver()
        self.graph = build_graph(saver)
        compiled = _compiled_saver(saver, self.graph.checkpointer)
        if compiled is None:
            raise OpsRuntimeError("envelope:checkpointer")
        # The saver the graph really uses (LangGraph's strict mode may have cloned it).
        self.checkpointer = compiled
        rounds = _graph_edit_rounds(self.graph)
        if rounds is not None and rounds != max_edit_rounds:
            # The envelope logs an edit as accepted before the graph sees it: both limits
            # must agree (a graph whose approve node is wrapped is checked by _check).
            raise OpsRuntimeError("envelope:max_edit_rounds")
        self._pending: RequestContext | None = None
        # One step at a time: start, decide and abandon claim the envelope atomically.
        self._lock = threading.Lock()
        self._busy = False

    # ------------------------------------------------------------------ public API
    @property
    def pending(self) -> bool:
        """Whether a draft waits for a decision (its thread is the only one kept)."""
        return self._pending is not None

    @property
    def presented_action(self) -> ProposedAction | None:
        """The action of the pending draft, as presented (None when nothing is pending).

        The evaluation driver merges a structured edit over its arguments (3b); the
        action holds ids, closed codes and digests, never a text.
        """
        ctx = self._pending
        return None if ctx is None else ctx.action

    def thread_count(self) -> int:
        """Threads held by the checkpointer (``residual_threads`` after an item)."""
        saver = self.checkpointer
        threads = set(saver.storage)
        threads.update(key[0] for key in saver.writes)
        threads.update(key[0] for key in saver.blobs)
        return len(threads)

    def start(self, request: str) -> OpsTurn:
        """Screen ``request`` and run the graph up to its end or its first interrupt."""
        self._claim_idle()
        try:
            return self._start(request)
        finally:
            self._release()

    def decide(self, raw: object, *, draft_id: str | None = None) -> OpsTurn:
        """Log the operator's decision, mint a token on approval, then resume the graph.

        ``draft_id`` is the id of the draft the operator was shown; when given, a decision
        about another draft is refused (``decide:draft_mismatch``) before anything is
        logged or minted.
        """
        ctx = self._claim_draft("decide", draft_id)
        try:
            return self._decide(ctx, raw)
        except BaseException:
            self._close_quietly(ctx)
            raise
        finally:
            if self._pending is not ctx:
                self._delete_thread(ctx.thread_id)
            self._release()

    def abandon(self, *, draft_id: str | None = None) -> OpsTurn:
        """Log ``abandoned`` for the pending draft and drop its thread."""
        ctx = self._claim_draft("abandon", draft_id)
        try:
            try:
                self._write(ctx, "abandoned")
            except AccessLogError:
                return self._audit_unavailable(ctx)
            return OpsTurn(
                kind="abandoned",
                draft_id=ctx.draft_id,
                edit_round=ctx.edit_round,
                flags=ctx.flags,
            )
        finally:
            self._delete_thread(ctx.thread_id)
            self._release()

    # ------------------------------------------------------------------ claim
    def _claim_idle(self) -> None:
        """Claim the envelope for ``start``, atomically (``start:busy``, ``start:pending``)."""
        with self._lock:
            if self._busy:
                raise OpsRuntimeError("start:busy")
            if self._pending is not None:
                raise OpsRuntimeError("start:pending")
            self._busy = True

    def _claim_draft(self, step: str, draft_id: str | None) -> RequestContext:
        """Claim the envelope and take the pending draft, atomically; nothing is logged.

        Refusals: another step running (``<step>:busy``), no draft (``<step>:no_pending``),
        or a ``draft_id`` other than the pending one (``<step>:draft_mismatch``).
        """
        with self._lock:
            if self._busy:
                raise OpsRuntimeError(f"{step}:busy")
            ctx = self._pending
            if ctx is None:
                raise OpsRuntimeError(f"{step}:no_pending")
            if draft_id is not None and draft_id != ctx.draft_id:
                raise OpsRuntimeError(f"{step}:draft_mismatch")
            self._pending = None
            self._busy = True
            return ctx

    def _release(self) -> None:
        with self._lock:
            self._busy = False

    def _start(self, request: str) -> OpsTurn:
        check_tracing_environment()
        ctx = RequestContext(request_id=uuid.uuid4().hex, thread_id=uuid.uuid4().hex)
        ctx.mark = self._clock()
        screening: object = None
        try:
            screening = self._screen(request)
        # Never chain: the error could describe the request.
        except Exception:
            screening = None
        if not isinstance(screening, OpsScreening):
            raise OpsRuntimeError("start:screen")
        ctx.flags = screening.assessment.flags
        ctx.pi_types = tuple(sorted({span.type for span in screening.spans}, key=str))
        ctx.pi_count = min(len(screening.spans), PI_COUNT_CAP)
        refusal = screening.assessment.refusal
        if refusal is not None:
            # The graph is never invoked: no connector call, no model call.
            try:
                self._write(ctx, "refused", reason=refusal)
            except AccessLogError:
                return self._audit_unavailable(ctx)
            return OpsTurn(
                kind="refused", text=self.policy.message(refusal), reason=refusal, flags=ctx.flags
            )
        inputs: OpsState = {
            "request_redacted": screening.redacted,
            "flow": None,
            "ids": [],
            "tool_events": [],
            "records": [],
            "quarantine_flags": [],
            "draft_body": None,
            "draft_citations": [],
            "draft_id": None,
            "draft_sha256": None,
            "proposed_action": None,
            "decision": None,
            "edit_round": 0,
            "refusal": None,
            "outcome": None,
            "handed_off": False,
            "trace": [],
        }
        try:
            return self._advance("start", ctx, inputs)
        except BaseException:
            self._close_quietly(ctx)
            raise
        finally:
            if self._pending is not ctx:
                self._delete_thread(ctx.thread_id)

    # ------------------------------------------------------------------ hooks
    def _invoke(self, ctx: RequestContext, value: OpsState | Command[str]) -> Mapping[str, object]:
        """One ``graph.invoke`` (first call or resume), hosted tracing off."""
        check_tracing_environment()
        with tracing_context(enabled=False):
            result = self.graph.invoke(value, ctx.config)
        if not isinstance(result, dict):
            raise _Abort("invalid_result")
        return result

    def _log_decision(
        self,
        ctx: RequestContext,
        event: OpsEvent,
        *,
        reason: OpsRefusalCode | None,
        action: ProposedAction | None,
    ) -> None:
        """Write the decision record; it always precedes the token and the resume."""
        self._write(ctx, event, reason=reason, action=action, decision=True)

    def _delete_thread(self, thread_id: str) -> None:
        """Destroy the request's checkpoints and writes (ADR 0013, art. 23)."""
        self.checkpointer.delete_thread(thread_id)

    # ------------------------------------------------------------------ steps
    def _decide(self, ctx: RequestContext, raw: object) -> OpsTurn:
        # A v1 tracing variable set since start: decide() closes the draft with ``error``,
        # then the LegacyTracingError propagates unchanged.
        check_tracing_environment()
        failure: _Failure | None = None
        try:
            self._check_thread(ctx)
        except Exception as exc:
            failure = _classify(exc)
        if failure is not None:
            return self._fail("decide", ctx, failure)
        decision = self._validated(ctx, raw)
        kind: DecisionKind = "invalid" if decision is None else decision.decision
        ctx.decision = kind
        event: OpsEvent
        reason: OpsRefusalCode | None = None
        # The approved action's digest is the one bound to the token; an edit names the
        # tool only; a rejected action leaves no digest (ADR 0014, « Jamais consigné »).
        action: ProposedAction | None = None
        if kind == "approve":
            event, action = "approved", ctx.action
        elif kind == "edit":
            event, action = "edit_submitted", ctx.action
        elif kind == "reject":
            event, reason = "rejected", "approval_rejected"
        else:
            event, reason = "rejected", "invalid_decision"
        try:
            self._log_decision(ctx, event, reason=reason, action=action)
        except AccessLogError:
            return self._audit_unavailable(ctx)
        value: dict[str, JsonValue] = (
            dict(_INVALID_RESUME) if decision is None else decision.model_dump(mode="json")
        )
        if kind == "approve":
            token: ApprovalToken | None = None
            try:
                token = self._mint(ctx)
            except Exception as exc:
                failure = _classify(exc)
            if failure is not None or token is None:
                return self._fail("decide", ctx, failure or _Failure("mint"))
            value["token"] = dict(token.model_dump())
        return self._advance("decide", ctx, Command(resume=value))

    def _validated(self, ctx: RequestContext, raw: object) -> ApprovalDecision | None:
        """The decision, or None when invalid (an edit past the last round, a token)."""
        decision = parse_decision(raw)
        if decision is None or decision.token is not None:
            # Only the envelope mints tokens: a decision that brings one is invalid.
            return None
        if decision.decision == "edit" and ctx.edit_round >= self.max_edit_rounds:
            return None
        return decision

    def _mint(self, ctx: RequestContext) -> ApprovalToken:
        action = ctx.action
        if action is None:
            raise _Abort("invalid_action")
        # From the presented action in state, never from the operator's value.
        return self.authority.mint(action.action_id, action.tool, action.args_sha256)

    def _check_thread(self, ctx: RequestContext) -> None:
        """Refuse a resume whose thread is gone (LangGraph would silently start over)."""
        snapshot = self.graph.get_state(ctx.config)
        if APPROVE_NODE not in snapshot.next:
            raise KeyError("thread")

    def _advance(self, step: str, ctx: RequestContext, value: OpsState | Command[str]) -> OpsTurn:
        """Invoke or resume, check the result, then log and build the turn."""
        failure: _Failure | None = None
        ending: _Ending | None = None
        try:
            ending = self._check(step, ctx, self._invoke(ctx, value))
        except LegacyTracingError:
            raise
        except Exception as exc:
            failure = _classify(exc)
        if failure is not None or ending is None:
            self._absorb_quietly(ctx)
            return self._fail(step, ctx, failure or _Failure("graph_error"))
        if ending.action is not None:
            # Only an interrupt carries the action to present.
            return self._present(step, ctx, ending)
        return self._end(step, ctx, ending)

    def _check(self, step: str, ctx: RequestContext, result: Mapping[str, object]) -> _Ending:
        """Read the state and check it against the protocol; raise :class:`_Abort`."""
        values = dict(self.graph.get_state(ctx.config).values)
        state: OpsState = values  # type: ignore[assignment]  # checked just below
        previous, shown_round = ctx.action, ctx.edit_round
        # Absorbed first (field by field, by exact type), so a broken state still gets
        # its ``error`` record.
        self._absorb(ctx, state)
        if not is_json_value(values):
            raise _Abort("state_not_json")
        interrupts = result.get("__interrupt__")
        if interrupts is not None:
            if not isinstance(interrupts, list | tuple) or len(interrupts) != 1:
                raise _Abort("invalid_interrupt")
            payload = getattr(interrupts[0], "value", None)
            action = _action(state.get("proposed_action"))
            if action is None:
                raise _Abort("invalid_action")
            if (
                ctx.flow not in APPROVAL_FLOWS
                or ctx.draft_id is None
                or type(state.get("draft_body")) is not str
                or not isinstance(payload, dict)
                or tuple(payload) != INTERRUPT_KEYS
                or payload.get("action_id") != action.action_id
                or payload.get("draft_id") != ctx.draft_id
                or payload.get("tool") != action.tool
                or payload.get("edit_round") != state.get("edit_round")
            ):
                raise _Abort("invalid_interrupt")
            self._check_presentation(step, ctx, state, action, previous, shown_round)
            if not text_bound(action, state.get("draft_body")) or (
                action.tool == RECORD_REPLY_TOOL
                and state.get("draft_sha256") != action.args.get("draft_sha256")
            ):
                # The token would bind a text other than the one shown (ADR 0014).
                raise _Abort("invalid_action")
            return _Ending(state=state, action=action)
        endings = _START_ENDINGS if step == "start" else _DECIDE_ENDINGS
        outcome = state.get("outcome")
        if type(outcome) is not str or outcome not in endings:
            raise _Abort("unexpected_outcome")
        if ctx.flow is None and outcome not in ("refused", "error"):
            # Only a refusal or an error can come before routing (ADR 0014, ``flow``).
            raise _Abort("unexpected_outcome")
        refusal = _refusal(state.get("refusal"))
        if outcome in ("refused", "withheld", "rejected") and refusal is None:
            raise _Abort("invalid_refusal")
        if step != "start" and (
            ctx.decision not in _DECIDE_AFTER[outcome]
            or (outcome == "rejected" and refusal != _REJECTION_REASON.get(ctx.decision))
        ):
            # An ending the logged decision does not explain (a rejection after a logged
            # edit, an execution without a logged approval): no record would cover it.
            raise _Abort("unexpected_outcome")
        if outcome in ("answered", "executed") and type(state.get("draft_body")) is not str:
            raise _Abort("no_text")
        if outcome == "error" and refusal != "connector_unavailable":
            refusal = None
        return _Ending(state=state, outcome=outcome, refusal=refusal)

    def _check_presentation(
        self,
        step: str,
        ctx: RequestContext,
        state: OpsState,
        action: ProposedAction,
        previous: ProposedAction | None,
        shown_round: int,
    ) -> None:
        """The rules of a (re-)presentation (ADR 0013); raise ``invalid_interrupt``.

        A first draft is round 0. A re-presentation follows a logged edit, with the next
        round (at most ``max_edit_rounds``), the same tool and a new ``action_id``. A
        test variant overrides this hook to remove the barrier.
        """
        edit_round = state.get("edit_round")
        if step == "start":
            if edit_round != 0:
                raise _Abort("invalid_interrupt")
            return
        if (
            ctx.decision != "edit"
            or previous is None
            or edit_round != shown_round + 1
            or shown_round + 1 > self.max_edit_rounds
            or action.tool != previous.tool
            or action.action_id == previous.action_id
        ):
            raise _Abort("invalid_interrupt")

    def _present(self, step: str, ctx: RequestContext, ending: _Ending) -> OpsTurn:
        """Log ``drafted`` or ``re_presented``, then return the draft and its header."""
        state = ending.state
        ctx.action = ending.action
        event: OpsEvent = "drafted" if step == "start" else "re_presented"
        try:
            self._write(ctx, event, action=ending.action)
        except AccessLogError:
            return self._audit_unavailable(ctx)
        failure: _Failure | None = None
        header: object = None
        try:
            header = self._header_for(state)
            if header is not None and not isinstance(header, DraftHeader):
                raise _Abort("invalid_header")
        except Exception as exc:
            failure = _classify(exc)
        if failure is not None:
            return self._fail(step, ctx, failure)
        self._pending = ctx
        return OpsTurn(
            kind="pending",
            text=str(state.get("draft_body")),
            draft_id=ctx.draft_id,
            header=header if isinstance(header, DraftHeader) else None,
            edit_round=ctx.edit_round,
            flags=ctx.flags,
            trace=_trace(state),
        )

    def _end(self, step: str, ctx: RequestContext, ending: _Ending) -> OpsTurn:
        """Write the record an ending is due, then return its text."""
        outcome = str(ending.outcome)
        endings = _START_ENDINGS if step == "start" else _DECIDE_ENDINGS
        event = endings[outcome]
        if event is not None:
            try:
                self._write(ctx, event, reason=ending.refusal)
            except AccessLogError:
                return self._audit_unavailable(ctx)
        state = ending.state
        text: str | None
        if outcome in ("answered", "executed"):
            text = str(state.get("draft_body"))
        elif ending.refusal is not None:
            text = self.policy.message(ending.refusal)
        else:
            text = None
        return OpsTurn(
            kind=outcome,
            text=text,
            reason=ending.refusal,
            draft_id=ctx.draft_id if outcome != "answered" else None,
            edit_round=ctx.edit_round,
            flags=ctx.flags,
            trace=_trace(state),
        )

    def _fail(self, step: str, ctx: RequestContext, failure: _Failure) -> OpsTurn:
        """Log ``error`` (an empty flow before routing), then raise; a log failure wins."""
        try:
            self._write(ctx, "error", reason=failure.reason)
        except AccessLogError:
            return self._audit_unavailable(ctx)
        # Outside any except block: the exception has no context to show.
        raise OpsRuntimeError(f"{step}:{failure.rule}")

    def _audit_unavailable(self, ctx: RequestContext) -> OpsTurn:
        """The served message of a failed log write; nothing else happens after it."""
        self._pending = None
        ctx.closed = True
        return OpsTurn(
            kind="audit_unavailable",
            text=self.policy.message("audit_unavailable"),
            reason="audit_unavailable",
            flags=ctx.flags,
        )

    # ------------------------------------------------------------------ state and log
    def _absorb(self, ctx: RequestContext, state: OpsState) -> None:
        """Copy what the next record needs from state (ids, codes and names only)."""
        flow = state.get("flow")
        if type(flow) is str and flow in _FLOWS:
            ctx.flow = flow
        draft_id = state.get("draft_id")
        # The first draft id is the request's; a later change is caught by _check.
        if ctx.draft_id is None and type(draft_id) is str and _HEX32.fullmatch(draft_id):
            ctx.draft_id = draft_id
        if "ids" in state:
            ctx.entity_refs = _unique(state.get("ids"), _ENTITY, MAX_ENTITY_REFS)
        if "quarantine_flags" in state:
            ctx.quarantine_flags = _unique(state.get("quarantine_flags"), _FLAG)
        edit_round = state.get("edit_round")
        if type(edit_round) is int and 0 <= edit_round <= 2:
            ctx.edit_round = edit_round
        events = state.get("tool_events")
        if type(events) is list:
            ctx.tool_events = tuple(
                str(event.get("tool")) if type(event) is dict else "" for event in events
            )
        if self.module == "service" and "records" in state:
            ctx.account_ref = _account_ref(state, ctx.entity_refs)

    def _absorb_quietly(self, ctx: RequestContext) -> None:
        """Absorb the last checkpoint after a failure, when it can still be read."""
        try:
            values = dict(self.graph.get_state(ctx.config).values)
            self._absorb(ctx, values)  # type: ignore[arg-type]  # read field by field
        # Best effort: the failure being handled is the one to report.
        except Exception:
            return

    def _write(
        self,
        ctx: RequestContext,
        event: OpsEvent,
        *,
        reason: OpsRefusalCode | None = None,
        action: ProposedAction | None = None,
        decision: bool = False,
    ) -> None:
        """Build and append one record; raise :class:`AccessLogError` on any failure."""
        now = self._clock()
        new_events = ctx.tool_events[ctx.tool_events_seen :]
        attempts = self._attempts()
        model = None
        if not decision and attempts is not None and attempts > ctx.chat_seen:
            model = self._model()
        record = self.log.record(
            draft_id=None if event == "answered" else ctx.draft_id,
            channel=self.channel,
            flow=ctx.flow,
            event=event,
            reason=reason,
            tool_calls=tuple(name for name in new_events if name in _TOOLS),
            entity_refs=ctx.entity_refs,
            account_ref=ctx.account_ref,
            pi_types=ctx.pi_types,
            pi_count=ctx.pi_count,
            quarantine_flags=ctx.quarantine_flags,
            action_tool=action.tool if action is not None else None,
            # ``edit_submitted`` has no digest: ``reguard`` recomputes it (ADR 0014).
            action_args_sha256=(
                action.args_sha256 if action is not None and event != "edit_submitted" else None
            ),
            edit_round=ctx.edit_round,
            model=model,
            latency_ms=max(0, int((now - ctx.mark) * 1000)),
            approver="operator" if decision else None,
        )
        self.log.append(record)
        if event in _CLOSING_EVENTS:
            ctx.closed = True
        ctx.mark = now
        ctx.tool_events_seen = len(ctx.tool_events)
        if attempts is not None:
            ctx.chat_seen = attempts

    def _attempts(self) -> int | None:
        """The model attempt count so far, or None when it cannot be read."""
        try:
            value = self._chat_attempts()
        # A failing counter only leaves ``model`` empty; it is not a reason to stop.
        except Exception:
            return None
        return value if type(value) is int else None

    def _model(self) -> str | None:
        """The model name for the record; an unloggable name fails the write closed."""
        try:
            value = self._model_name()
        except Exception:
            return None
        return value if value is None or type(value) is str else None

    def _close_quietly(self, ctx: RequestContext) -> None:
        """An exception escapes a step: the request's ``error`` record, best effort.

        Written with the flow when it is known (an empty one before routing), unless a
        closing record (or an audit failure) already ended the request, or its draft is
        still pending. This covers what the step itself does not log: a
        LegacyTracingError, a KeyboardInterrupt, any other BaseException raised after the
        screen or after the decision record.
        """
        if ctx.closed or self._pending is ctx:
            return
        try:
            self._write(ctx, "error")
        # The exception being raised is the one to report; no other record is tried.
        except Exception:
            ctx.closed = True


def _account_ref(state: OpsState, entity_refs: tuple[str, ...]) -> str | None:
    """The account of the in-scope order record (3a), read from the ERP view in state."""
    records = state.get("records")
    if type(records) is not list:
        return None
    for record in records:
        if type(record) is not dict or record.get("order_id") not in entity_refs:
            continue
        account = record.get("account_id")
        if type(account) is str and _ACCOUNT.fullmatch(account) is not None:
            return account
    return None


def _trace(state: OpsState) -> tuple[dict[str, JsonValue], ...]:
    """The state's trace, scrubbed once more (idempotent on a node's own entries)."""
    entries = state.get("trace")
    if type(entries) is not list:
        return ()
    return tuple(
        scrub_entry(str(entry.get("node")), entry) for entry in entries if type(entry) is dict
    )
