"""Human approval nodes of the module 3 graphs (ADR 0013).

The shared tail of the two flows that write (``customer_reply`` in 3a, ``work_request``
in 3b) is ``approve ⇄ reguard → apply → handoff``; :func:`add_approval_nodes` wires it
into a graph whose ``propose`` node has set ``draft_id``, ``flow``, ``edit_round`` and
``proposed_action``.

- ``approve`` (:func:`make_approve_node`) calls ``interrupt`` with five ids and codes
  only (``draft_id``, ``flow``, ``action_id``, ``tool``, ``edit_round``): no text, no
  contact, no argument. On resume it validates the value with :class:`ApprovalDecision`;
  an invalid value, or an edit past the last round, becomes a rejection with
  ``invalid_decision``. LangGraph re-runs an interrupted node from its first line on
  every resume, so this node does no I/O, reads no clock, draws no uuid and logs
  nothing. It is an :class:`ApproveNode`, whose round limit the envelope reads back.
- ``reguard`` (:func:`make_reguard_node`) sends an edit back through the flow's own
  barriers, injected as ``check_edit``. A pass presents a new action, with a **new**
  ``action_id`` and the new digest, so that a token minted for one round can never run
  another round's arguments; ``edit_round`` grows by one. A reply action must carry the
  digest of the text to be shown (:func:`text_bound`). A failure ends the request
  ``withheld`` with ``output_pi``, ``cross_account`` or ``invalid_args``.
- ``apply`` (:func:`make_apply_node`) makes exactly one executor call, with the arguments
  of ``state.proposed_action`` (never the decision's) and the token of the decision.
  The executor redeems the token against the digest of those exact arguments.
- ``handoff`` (:func:`make_handoff_node`) marks the request handed off when, and only
  when, the write was executed.

Every node appends exactly one trace entry, built by
:func:`alverno_lab.agents.ops.scrub.scrub_entry`. Log writes and token minting belong to
the envelope (``runtime.py``), never to a node.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from typing import Literal, Protocol, get_args

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from alverno_lab.agents.ops.scrub import scrub_entry
from alverno_lab.agents.ops.state import OpsState, ProposedAction, text_sha256
from alverno_lab.connectors.types import RECORD_REPLY_TOOL, WriteToolName
from alverno_lab.privacy.ops_types import Flow
from alverno_lab.tools.authority import ApprovalToken
from alverno_lab.tools.executor import ToolResult

__all__ = [
    "APPLY_NODE",
    "APPROVE_NODE",
    "HANDOFF_NODE",
    "INTERRUPT_KEYS",
    "MAX_EDITED_TEXT_CHARS",
    "MAX_EDIT_ROUNDS",
    "REGUARD_NODE",
    "ApprovalDecision",
    "ApproveNode",
    "EditCheck",
    "EditChecker",
    "OpsNode",
    "ToolCaller",
    "add_approval_nodes",
    "approve_node_rounds",
    "check_edit_rounds",
    "interrupt_payload",
    "make_apply_node",
    "make_approve_node",
    "make_handoff_node",
    "make_reguard_node",
    "parse_decision",
    "route_after_approve",
    "route_after_reguard",
    "text_bound",
]

APPROVE_NODE = "approve"
REGUARD_NODE = "reguard"
APPLY_NODE = "apply"
HANDOFF_NODE = "handoff"

MAX_EDIT_ROUNDS = 2
MAX_EDITED_TEXT_CHARS = 1500
INTERRUPT_KEYS: tuple[str, ...] = ("draft_id", "flow", "action_id", "tool", "edit_round")

EditReason = Literal["output_pi", "cross_account", "invalid_args"]

_HEX32 = re.compile(r"[0-9a-f]{32}")
_FLOWS: frozenset[str] = frozenset(get_args(Flow))
_WRITE_TOOLS: frozenset[str] = frozenset(get_args(WriteToolName))
# Both connector codes of the executor become the « connector_unavailable » refusal.
_CONNECTOR_ERRORS: frozenset[str] = frozenset({"connector_error", "connector_unavailable"})
_INVALID_DECISION: dict[str, JsonValue] = {"decision": "reject", "reason": "invalid_decision"}


class OpsNode(Protocol):
    """A node of an ops graph (named ``state``, as LangGraph's node protocol requires)."""

    def __call__(self, state: OpsState) -> OpsState:
        """Read the state, return the update."""
        ...


class ApprovalDecision(BaseModel):
    """The operator's decision on one presented action.

    ``edit`` carries exactly one of ``edited_text`` (3a, a new draft body) and
    ``edited_args`` (3b, new arguments); ``approve`` and ``reject`` carry neither. Only
    ``approve`` may carry a token (the envelope mints it after logging ``approved``).
    Strict types: nothing is coerced. Errors never show the input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    decision: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(
        default=None, min_length=1, max_length=MAX_EDITED_TEXT_CHARS, repr=False
    )
    edited_args: dict[str, JsonValue] | None = Field(default=None, repr=False)
    token: dict[str, str] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _payload_rules(self) -> ApprovalDecision:
        has_text = self.edited_text is not None
        has_args = self.edited_args is not None
        if self.decision == "edit" and has_text == has_args:
            raise ValueError("decision:edit_needs_one_payload")
        if self.decision != "edit" and (has_text or has_args):
            raise ValueError("decision:payload_without_edit")
        if self.token is not None and self.decision != "approve":
            raise ValueError("decision:token_without_approve")
        return self


def parse_decision(raw: object) -> ApprovalDecision | None:
    """``raw`` as a valid :class:`ApprovalDecision`, or ``None``; never raises."""
    try:
        return ApprovalDecision.model_validate(raw)
    # Never raise or chain: the error would describe the rejected value.
    except Exception:
        return None


class EditCheck(BaseModel):
    """Verdict of a flow's edit check, injected into ``reguard``.

    A pass carries the action to present next (``proposed_action``, a
    ``ProposedAction`` dump) and, for a text edit, the new draft body; a failure carries
    its reason and nothing else.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    ok: bool
    reason: EditReason | None = None
    draft_body: str | None = Field(default=None, repr=False)
    proposed_action: dict[str, JsonValue] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _verdict_rules(self) -> EditCheck:
        if self.ok and (self.reason is not None or self.proposed_action is None):
            raise ValueError("edit_check:pass_needs_action")
        if not self.ok and (
            self.reason is None or self.draft_body is not None or self.proposed_action is not None
        ):
            raise ValueError("edit_check:failure_needs_reason_only")
        return self


EditChecker = Callable[[OpsState, ApprovalDecision], EditCheck]


class ToolCaller(Protocol):
    """What ``apply`` needs: :meth:`alverno_lab.tools.executor.ToolExecutor.call`."""

    def call(
        self, name: str, args: Mapping[str, object], *, token: ApprovalToken | None = None
    ) -> ToolResult:
        """Validate, authorise and run one tool call; never raises."""
        ...


def check_edit_rounds(max_edit_rounds: int) -> int:
    """``max_edit_rounds`` when it is 1 or 2 (``ops_max_edit_rounds``, ADR 0013)."""
    if type(max_edit_rounds) is not int or not 1 <= max_edit_rounds <= MAX_EDIT_ROUNDS:
        raise ValueError("approval:max_edit_rounds")
    return max_edit_rounds


def _hex32(value: object) -> str | None:
    return value if type(value) is str and _HEX32.fullmatch(value) is not None else None


def _edit_round(state: OpsState) -> int:
    value = state.get("edit_round", 0)
    return value if type(value) is int and 0 <= value <= MAX_EDIT_ROUNDS else 0


def interrupt_payload(state: OpsState) -> dict[str, JsonValue]:
    """The five ids and codes that ``approve`` interrupts with; a malformed one is None."""
    action = state.get("proposed_action") or {}
    flow = state.get("flow")
    tool = action.get("tool")
    return {
        "draft_id": _hex32(state.get("draft_id")),
        "flow": flow if type(flow) is str and flow in _FLOWS else None,
        "action_id": _hex32(action.get("action_id")),
        "tool": tool if type(tool) is str and tool in _WRITE_TOOLS else None,
        "edit_round": _edit_round(state),
    }


def _action(value: object) -> ProposedAction | None:
    """``value`` as a consistent :class:`ProposedAction`, or ``None``; never raises."""
    try:
        return ProposedAction.model_validate(value)
    except Exception:
        return None


def text_bound(action: ProposedAction, body: object) -> bool:
    """Whether ``action`` is bound to the text shown: a reply carries ``text_sha256(body)``.

    Only ``erp.record_reply_prepared`` records the digest of a text (ADR 0014: the
    action's ``draft_sha256`` is the digest of the approved text); another tool binds none.
    """
    if action.tool != RECORD_REPLY_TOOL:
        return True
    return type(body) is str and action.args.get("draft_sha256") == text_sha256(body)


def _token(value: object) -> ApprovalToken | None:
    """The decision's token as an :class:`ApprovalToken`, or ``None``; never raises."""
    if value is None:
        return None
    try:
        return ApprovalToken.model_validate(value)
    except Exception:
        return None


# --------------------------------------------------------------------------- approve
class ApproveNode:
    """The ``approve`` node; ``max_edit_rounds`` is readable, so that the envelope can
    refuse a graph whose limit differs from its own (:func:`approve_node_rounds`)."""

    __slots__ = ("max_edit_rounds",)

    def __init__(self, max_edit_rounds: int) -> None:
        self.max_edit_rounds = check_edit_rounds(max_edit_rounds)

    def __call__(self, state: OpsState) -> OpsState:
        """Interrupt, then put the validated decision in state."""
        return _approve(state, self.max_edit_rounds)


def make_approve_node(*, max_edit_rounds: int = MAX_EDIT_ROUNDS) -> ApproveNode:
    """The ``approve`` node: one interrupt, then the validated decision in state.

    A rejection (or an invalid decision) ends the request: ``outcome="rejected"`` and
    ``refusal`` ``approval_rejected`` or ``invalid_decision``.
    """
    return ApproveNode(max_edit_rounds)


def approve_node_rounds(node: object) -> int | None:
    """The edit-round limit of an ``approve`` node, or None when ``node`` hides it.

    ``node`` is the node itself or the runnable LangGraph wrapped it in (``.func``); a
    node wrapped by another function (a probe, a test variant) gives None.
    """
    for candidate in (node, getattr(node, "func", None)):
        if isinstance(candidate, ApproveNode):
            return candidate.max_edit_rounds
    return None


def _approve(state: OpsState, rounds: int) -> OpsState:
    # Re-run from here on every resume: no I/O, no clock, no uuid, no logging.
    edit_round = _edit_round(state)
    raw = interrupt(interrupt_payload(state))
    decision = parse_decision(raw)
    if decision is not None and decision.decision == "edit" and edit_round >= rounds:
        decision = None
    if decision is None:
        detail = {"decision": "reject", "edit_round": edit_round, "valid": False}
        return {
            "decision": dict(_INVALID_DECISION),
            "outcome": "rejected",
            "refusal": "invalid_decision",
            "trace": [scrub_entry(APPROVE_NODE, {**detail, "reason": "invalid_decision"})],
        }
    detail = {"decision": decision.decision, "edit_round": edit_round, "valid": True}
    stored = decision.model_dump(mode="json")
    if decision.decision == "reject":
        return {
            "decision": stored,
            "outcome": "rejected",
            "refusal": "approval_rejected",
            "trace": [scrub_entry(APPROVE_NODE, {**detail, "reason": "approval_rejected"})],
        }
    return {"decision": stored, "trace": [scrub_entry(APPROVE_NODE, detail)]}


def route_after_approve(state: OpsState) -> str:
    """``apply`` on approve, ``reguard`` on edit, the end otherwise."""
    decision = state.get("decision") or {}
    kind = decision.get("decision")
    if state.get("outcome") is None and kind == "approve":
        return APPLY_NODE
    if state.get("outcome") is None and kind == "edit":
        return REGUARD_NODE
    return END


# --------------------------------------------------------------------------- reguard
def _withheld(reason: str, edit_round: int) -> OpsState:
    return {
        "outcome": "withheld",
        "refusal": reason,
        "trace": [
            scrub_entry(REGUARD_NODE, {"ok": False, "reason": reason, "edit_round": edit_round})
        ],
    }


def make_reguard_node(check_edit: EditChecker) -> OpsNode:
    """The ``reguard`` node: the flow's barriers on an edit, then a new action or withheld.

    ``check_edit`` is the flow's own check (3a: quarantine, scope then output guard on the
    edited body; 3b: args model and closed codes on the edited arguments). The new
    action keeps the tool of the presented one and gets a new ``action_id``; a text edit
    also replaces ``draft_body`` and ``draft_sha256`` (:func:`text_sha256` of the body).
    """

    def reguard(state: OpsState) -> OpsState:
        edit_round = _edit_round(state)
        decision = parse_decision(state.get("decision"))
        if decision is None or decision.decision != "edit":
            # Unreachable through route_after_approve; fail closed all the same.
            return {
                "decision": dict(_INVALID_DECISION),
                "outcome": "rejected",
                "refusal": "invalid_decision",
                "trace": [
                    scrub_entry(
                        REGUARD_NODE,
                        {"ok": False, "reason": "invalid_decision", "edit_round": edit_round},
                    )
                ],
            }
        check = check_edit(state, decision)
        if type(check) is not EditCheck:
            raise TypeError("reguard:edit_check_type")
        if not check.ok:
            # The validator guarantees a reason on a failed check.
            return _withheld(check.reason or "invalid_args", edit_round)
        previous = state.get("proposed_action") or {}
        action = _action(check.proposed_action)
        if action is None or action.tool != previous.get("tool"):
            return _withheld("invalid_args", edit_round)
        body = check.draft_body if check.draft_body is not None else state.get("draft_body")
        if not text_bound(action, body):
            # The operator approves the text shown: the action must carry its digest.
            return _withheld("invalid_args", edit_round)
        # A new action id per presented action: a token of round n cannot run round n+1.
        fresh = action.model_copy(update={"action_id": uuid.uuid4().hex})
        update: OpsState = {
            "proposed_action": fresh.model_dump(mode="json"),
            "edit_round": edit_round + 1,
            "decision": None,
            "trace": [
                scrub_entry(REGUARD_NODE, {"ok": True, "edit_round": edit_round + 1}),
            ],
        }
        if check.draft_body is not None:
            update["draft_body"] = check.draft_body
            update["draft_sha256"] = text_sha256(check.draft_body)
        return update

    return reguard


def route_after_reguard(state: OpsState) -> str:
    """Back to ``approve`` after a passed edit, the end otherwise."""
    return APPROVE_NODE if state.get("outcome") is None else END


# --------------------------------------------------------------------------- apply
def make_apply_node(executor: ToolCaller) -> OpsNode:
    """The ``apply`` node: one executor call with the presented action and the token.

    Success → ``outcome="executed"``. A connector failure → ``outcome="error"`` and
    ``refusal="connector_unavailable"``. A refused token (or none) → ``outcome="error"``,
    no refusal, and the executor's error code in ``tool_events``. A missing or
    inconsistent action, or a decision that is not an approval, makes no call at all.
    """

    def apply(state: OpsState) -> OpsState:
        action = _action(state.get("proposed_action"))
        decision = parse_decision(state.get("decision"))
        if action is None or decision is None or decision.decision != "approve":
            return {
                "outcome": "error",
                "refusal": None,
                "trace": [scrub_entry(APPLY_NODE, {"ok": False, "error": "invalid_action"})],
            }
        # Arguments from the presented action, never from the decision.
        result = executor.call(action.tool, action.args, token=_token(decision.token))
        events = list(state.get("tool_events") or [])
        events.append({"tool": action.tool, "ok": result.ok, "error": result.error})
        update: OpsState = {
            "tool_events": events,
            # The token is spent (or refused): it does not stay in the checkpoint.
            "decision": {**decision.model_dump(mode="json"), "token": None},
            "trace": [
                scrub_entry(
                    APPLY_NODE, {"tool": action.tool, "ok": result.ok, "error": result.error}
                )
            ],
        }
        if result.ok:
            update["outcome"] = "executed"
            update["refusal"] = None
        elif result.error in _CONNECTOR_ERRORS:
            update["outcome"] = "error"
            update["refusal"] = "connector_unavailable"
        else:
            update["outcome"] = "error"
            update["refusal"] = None
        return update

    return apply


# --------------------------------------------------------------------------- handoff
def make_handoff_node() -> OpsNode:
    """The ``handoff`` node: ``handed_off`` is true only after an executed write."""

    def handoff(state: OpsState) -> OpsState:
        handed_off = state.get("outcome") == "executed"
        return {
            "handed_off": handed_off,
            "trace": [scrub_entry(HANDOFF_NODE, {"handed_off": handed_off})],
        }

    return handoff


# --------------------------------------------------------------------------- wiring
def add_approval_nodes(
    builder: StateGraph[OpsState, None, OpsState, OpsState],
    *,
    executor: ToolCaller,
    check_edit: EditChecker,
    max_edit_rounds: int = MAX_EDIT_ROUNDS,
) -> None:
    """Add ``approve ⇄ reguard → apply → handoff → END`` to ``builder``.

    The caller adds the edge into :data:`APPROVE_NODE` (from its ``propose`` node).
    """
    builder.add_node(APPROVE_NODE, make_approve_node(max_edit_rounds=max_edit_rounds))
    builder.add_node(REGUARD_NODE, make_reguard_node(check_edit))
    builder.add_node(APPLY_NODE, make_apply_node(executor))
    builder.add_node(HANDOFF_NODE, make_handoff_node())
    builder.add_conditional_edges(
        APPROVE_NODE,
        route_after_approve,
        {APPLY_NODE: APPLY_NODE, REGUARD_NODE: REGUARD_NODE, END: END},
    )
    builder.add_conditional_edges(
        REGUARD_NODE, route_after_reguard, {APPROVE_NODE: APPROVE_NODE, END: END}
    )
    builder.add_edge(APPLY_NODE, HANDOFF_NODE)
    builder.add_edge(HANDOFF_NODE, END)
