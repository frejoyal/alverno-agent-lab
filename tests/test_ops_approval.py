"""Module 3 approval nodes and state (I5 spec decisions 2 and 3, ADR 0013).

The nodes run inside a tiny LangGraph graph (``propose → approve ⇄ reguard → apply →
handoff``) with an in-memory checkpointer, so that ``interrupt`` and ``Command(resume=
...)`` are exercised for real; the full envelope is tested elsewhere. The write goes
through the real executor and the simulated ERP of ``data/eval/ops/``; only fixture ids
picked by rule are used, never a holdout. Assertion messages cite node, rule and key
names, never a fixture value or a token field.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import JsonValue, ValidationError

from alverno_lab.agents.ops import (
    APPLY_NODE,
    APPROVE_NODE,
    HANDOFF_NODE,
    INTERRUPT_KEYS,
    REGUARD_NODE,
    ApprovalDecision,
    CountingConnector,
    CountingExecutor,
    EditCheck,
    OpsState,
    ProposedAction,
    Timeline,
    add_approval_nodes,
    is_json_value,
    make_apply_node,
    make_approve_node,
    make_handoff_node,
    make_reguard_node,
    parse_decision,
    route_after_approve,
    route_after_reguard,
    scrub_entry,
    text_sha256,
)
from alverno_lab.agents.ops.approval import (
    ApproveNode,
    OpsNode,
    approve_node_rounds,
    check_edit_rounds,
    interrupt_payload,
    text_bound,
)
from alverno_lab.connectors import QuarantinedText, SimErp, SimGmao, WriteToolName
from alverno_lab.tools import (
    ApprovalAuthority,
    ApprovalToken,
    CreateWorkRequestArgs,
    RecordReplyPreparedArgs,
    ToolExecutor,
)

REPO_ROOT = Path(__file__).parent.parent
OPS = REPO_ROOT / "data" / "eval" / "ops"
RECORD_REPLY: WriteToolName = "erp.record_reply_prepared"
HEX32 = "0123456789abcdef0123456789abcdef"
# Fictional template text, no fixture value.
BODY = "Bonjour, voici le suivi de votre commande {order}. Merci de votre confiance."
EDITED = "Bonjour, votre commande {order} est en cours. Bonne journée."
EDITED_AGAIN = "Bonjour, la commande {order} avance comme prévu."
SECRET_TEXT = "texte modifie par la personne qui approuve"

CheckEdit = Callable[[OpsState, ApprovalDecision], EditCheck]
Graph = CompiledStateGraph[OpsState, None, OpsState, OpsState]


def stub_quarantine(text: str) -> QuarantinedText:
    """Stub quarantine: a fixed placeholder, so no fixture text reaches a view."""
    return QuarantinedText(text="texte mis en quarantaine", flags=("stub",))


def strict_saver() -> InMemorySaver:
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=None)
    return InMemorySaver(serde=serde)


def reply_action(order_id: str, body: str) -> ProposedAction:
    args = RecordReplyPreparedArgs(order_id=order_id, draft_sha256=text_sha256(body))
    return ProposedAction.build(RECORD_REPLY, args, summary_fr="Réponse préparée")


class Rig:
    """A tiny graph over the real executor, with counting probes."""

    def __init__(
        self,
        *,
        check_edit: CheckEdit | None = None,
        max_edit_rounds: int = 2,
        with_erp: bool = True,
        approve_wrapper: Callable[[OpsNode], OpsNode] | None = None,
    ) -> None:
        self.erp = SimErp.from_yaml(OPS / "erp.yaml", quarantine=stub_quarantine)
        self.gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=stub_quarantine)
        self.order_id = sorted(self.erp.order_ids)[0]
        self.timeline = Timeline()
        self.connector = CountingConnector(
            timeline=self.timeline, erp=self.erp if with_erp else None, gmao=self.gmao
        )
        self.authority = ApprovalAuthority()
        self.executor = CountingExecutor(
            ToolExecutor(self.connector, self.connector, self.authority.verifier), self.timeline
        )
        self.saver = strict_saver()
        self.thread_id = uuid.uuid4().hex
        self.config: RunnableConfig = {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": 25,
        }
        self.graph = self._build(check_edit or self.text_check, max_edit_rounds, approve_wrapper)

    def body(self, template: str) -> str:
        return template.format(order=self.order_id)

    def text_check(self, state: OpsState, decision: ApprovalDecision) -> EditCheck:
        """A passing 3a-like check: the edited body becomes the new action's digest."""
        assert decision.edited_text is not None, "edit:edited_text"
        action = reply_action(self.order_id, decision.edited_text)
        return EditCheck(
            ok=True,
            draft_body=decision.edited_text,
            proposed_action=action.model_dump(mode="json"),
        )

    def propose(self, state: OpsState) -> OpsState:
        body = self.body(BODY)
        action = reply_action(self.order_id, body)
        return {
            "flow": "customer_reply",
            "ids": [self.order_id],
            "draft_id": uuid.uuid4().hex,
            "draft_body": body,
            "draft_sha256": text_sha256(body),
            "draft_citations": [self.order_id],
            "edit_round": 0,
            "proposed_action": action.model_dump(mode="json"),
            "outcome": None,
            "trace": [scrub_entry("propose", {"tool": RECORD_REPLY, "edit_round": 0})],
        }

    def _build(
        self,
        check_edit: CheckEdit,
        max_edit_rounds: int,
        approve_wrapper: Callable[[OpsNode], OpsNode] | None,
    ) -> Graph:
        builder: StateGraph[OpsState, None, OpsState, OpsState] = StateGraph(OpsState)
        builder.add_node("propose", self.propose)
        builder.add_edge(START, "propose")
        builder.add_edge("propose", APPROVE_NODE)
        if approve_wrapper is None:
            add_approval_nodes(
                builder,
                executor=self.executor,
                check_edit=check_edit,
                max_edit_rounds=max_edit_rounds,
            )
        else:
            # The same wiring by hand, with the approve node wrapped.
            approve = approve_wrapper(make_approve_node(max_edit_rounds=max_edit_rounds))
            builder.add_node(APPROVE_NODE, approve)
            builder.add_node(REGUARD_NODE, make_reguard_node(check_edit))
            builder.add_node(APPLY_NODE, make_apply_node(self.executor))
            builder.add_node(HANDOFF_NODE, make_handoff_node())
            builder.add_conditional_edges(APPROVE_NODE, route_after_approve)
            builder.add_conditional_edges(REGUARD_NODE, route_after_reguard)
            builder.add_edge(APPLY_NODE, HANDOFF_NODE)
            builder.add_edge(HANDOFF_NODE, END)
        return builder.compile(checkpointer=self.saver)

    # ------------------------------------------------------------------ driving
    def start(self) -> dict[str, Any]:
        return dict(self.graph.invoke({"request_redacted": "demande"}, self.config))

    def resume(self, value: object) -> dict[str, Any]:
        return dict(self.graph.invoke(Command(resume=value), self.config))

    def state(self) -> OpsState:
        values: OpsState = self.graph.get_state(self.config).values  # type: ignore[assignment]
        return values

    def action(self) -> ProposedAction:
        return ProposedAction.model_validate(self.state().get("proposed_action"))

    def mint(self, action: ProposedAction | None = None) -> ApprovalToken:
        action = action or self.action()
        return self.authority.mint(action.action_id, action.tool, action.args_sha256)

    def approve_value(self, token: ApprovalToken | None = None) -> dict[str, object]:
        value: dict[str, object] = {"decision": "approve"}
        if token is not None:
            value["token"] = token.model_dump()
        return value

    def edit_value(self, template: str) -> dict[str, object]:
        return {"decision": "edit", "edited_text": self.body(template)}


def nodes_of(state: Mapping[str, Any]) -> list[str]:
    return [str(entry["node"]) for entry in state.get("trace", [])]


def payload_of(result: Mapping[str, Any]) -> dict[str, Any]:
    interrupts = result["__interrupt__"]
    assert len(interrupts) == 1, "interrupt:one_per_round"
    value = interrupts[0].value
    assert isinstance(value, dict), "interrupt:payload_type"
    return value


# --------------------------------------------------------------------------- approve
def test_interrupt_payload_holds_the_five_ids_and_codes_only() -> None:
    rig = Rig()
    payload = payload_of(rig.start())
    assert tuple(payload) == INTERRUPT_KEYS
    action = rig.action()
    state = rig.state()
    assert payload == {
        "draft_id": state.get("draft_id"),
        "flow": "customer_reply",
        "action_id": action.action_id,
        "tool": RECORD_REPLY,
        "edit_round": 0,
    }
    text = json.dumps(payload)
    assert rig.body(BODY) not in text, "interrupt:no_text"
    assert action.args_sha256 not in text, "interrupt:no_digest"
    assert rig.order_id not in text, "interrupt:no_args"


def test_interrupt_payload_drops_malformed_values() -> None:
    state: OpsState = {
        "draft_id": "not an id",
        "flow": "ailleurs",
        "proposed_action": {"action_id": "x", "tool": "erp.get_order"},
        "edit_round": 7,
    }
    assert interrupt_payload(state) == {
        "draft_id": None,
        "flow": None,
        "action_id": None,
        "tool": None,
        "edit_round": 0,
    }


def test_approve_executes_once_with_the_presented_args() -> None:
    rig = Rig()
    rig.start()
    action = rig.action()
    assert rig.connector.writes == 0, "effect:before_decision"
    final = rig.resume(rig.approve_value(rig.mint(action)))
    assert "__interrupt__" not in final
    assert final["outcome"] == "executed"
    assert final["refusal"] is None
    assert final["handed_off"] is True
    assert len(rig.erp.executed) == 1, "effect:exactly_once"
    effect = rig.erp.executed[0]
    assert effect.args_sha256 == action.args_sha256, "effect:args_sha256"
    assert effect.action_id == action.action_id, "effect:action_id"
    assert final["tool_events"] == [{"tool": RECORD_REPLY, "ok": True, "error": None}]
    assert final["decision"]["token"] is None, "decision:token_not_kept"
    assert nodes_of(final) == ["propose", APPROVE_NODE, APPLY_NODE, HANDOFF_NODE]
    kinds = [event.kind for event in rig.timeline.events]
    assert kinds == ["effect", "call"]


def test_approve_node_reruns_from_its_first_line_but_traces_once() -> None:
    runs: list[str] = []

    def wrap(node: OpsNode) -> OpsNode:
        def counted(state: OpsState) -> OpsState:
            runs.append("approve")
            return node(state)

        return counted

    rig = Rig(approve_wrapper=wrap)
    rig.start()
    assert runs == ["approve"]
    final = rig.resume(rig.approve_value(rig.mint()))
    assert runs == ["approve", "approve"], "approve:rerun_on_resume"
    assert nodes_of(final).count(APPROVE_NODE) == 1, "approve:one_trace_entry"
    assert rig.executor.calls == 1
    assert len(rig.erp.executed) == 1


def test_reject_ends_without_any_call() -> None:
    rig = Rig()
    rig.start()
    final = rig.resume({"decision": "reject"})
    assert final["outcome"] == "rejected"
    assert final["refusal"] == "approval_rejected"
    assert "handed_off" not in final
    assert rig.executor.calls == 0
    assert rig.connector.writes == 0
    assert nodes_of(final) == ["propose", APPROVE_NODE]
    assert final["trace"][-1] == {
        "node": APPROVE_NODE,
        "decision": "reject",
        "edit_round": 0,
        "reason": "approval_rejected",
        "valid": True,
    }


# Not resumed as is: ``Command(resume=None)`` is not a resume at all, and an empty dict
# (like any dict whose keys are all interrupt ids) is read by LangGraph as a map of
# resume values per interrupt id. The envelope never resumes with a raw operator value.
NOT_RESUMABLE: list[object] = [None, {}]
INVALID_DECISIONS: list[object] = [
    "approve",
    42,
    [],
    {"decision": "maybe"},
    {"decision": "APPROVE"},
    {"decision": "approve", "edited_text": "texte"},
    {"decision": "reject", "token": {"nonce": "x"}},
    {"decision": "edit", "token": {"nonce": "x"}, "edited_text": "texte"},
    {"decision": "edit"},
    {"decision": "edit", "edited_text": "texte", "edited_args": {}},
    {"decision": "edit", "edited_text": ""},
    {"decision": "edit", "edited_text": "x" * 1501},
    {"decision": "edit", "edited_text": 12},
    {"decision": "approve", "token": {"nonce": 1}},
    {"decision": "approve", "extra": True},
]


@pytest.mark.parametrize("raw", INVALID_DECISIONS)
def test_invalid_decision_becomes_a_rejection(raw: object) -> None:
    rig = Rig()
    rig.start()
    final = rig.resume(raw)
    assert final["outcome"] == "rejected"
    assert final["refusal"] == "invalid_decision"
    assert final["decision"] == {"decision": "reject", "reason": "invalid_decision"}
    assert rig.executor.calls == 0
    assert rig.connector.writes == 0
    assert final["trace"][-1]["valid"] is False


def test_approve_without_token_is_refused_by_the_executor() -> None:
    rig = Rig()
    rig.start()
    final = rig.resume(rig.approve_value())
    assert final["outcome"] == "error"
    assert final["refusal"] is None
    assert final["tool_events"] == [{"tool": RECORD_REPLY, "ok": False, "error": "no_token"}]
    assert final["handed_off"] is False
    assert rig.connector.writes == 0
    assert rig.executor.records[-1].refusal == "no_token"


def test_malformed_token_is_passed_as_no_token() -> None:
    rig = Rig()
    rig.start()
    final = rig.resume({"decision": "approve", "token": {"action_id": HEX32}})
    assert final["tool_events"][-1]["error"] == "no_token"
    assert rig.connector.writes == 0


def test_forged_token_is_refused() -> None:
    rig = Rig()
    rig.start()
    action = rig.action()
    forged = ApprovalAuthority().mint(action.action_id, action.tool, action.args_sha256)
    final = rig.resume(rig.approve_value(forged))
    assert final["outcome"] == "error"
    assert final["refusal"] is None
    assert final["tool_events"][-1]["error"] == "bad_token"
    assert rig.erp.executed == []


def test_connector_failure_is_connector_unavailable() -> None:
    rig = Rig(with_erp=False)
    rig.start()
    final = rig.resume(rig.approve_value(rig.mint()))
    assert final["outcome"] == "error"
    assert final["refusal"] == "connector_unavailable"
    assert final["tool_events"][-1]["error"] == "connector_error"
    assert final["handed_off"] is False


# --------------------------------------------------------------------------- edit
def test_edit_rounds_present_new_actions_then_refuse_a_third_edit() -> None:
    rig = Rig()
    first = payload_of(rig.start())
    first_action = rig.action()

    second = payload_of(rig.resume(rig.edit_value(EDITED)))
    state = rig.state()
    second_action = rig.action()
    assert second["edit_round"] == 1
    assert second["action_id"] == second_action.action_id
    assert second_action.action_id != first_action.action_id, "reguard:new_action_id"
    assert second_action.args_sha256 != first_action.args_sha256, "reguard:new_digest"
    assert second["draft_id"] == first["draft_id"], "reguard:same_draft_id"
    assert state.get("draft_body") == rig.body(EDITED)
    assert state.get("draft_sha256") == text_sha256(rig.body(EDITED))
    assert state.get("decision") is None

    third = payload_of(rig.resume(rig.edit_value(EDITED_AGAIN)))
    third_action = rig.action()
    assert third["edit_round"] == 2
    assert third_action.action_id not in {first_action.action_id, second_action.action_id}

    final = rig.resume(rig.edit_value(EDITED))
    assert final["outcome"] == "rejected"
    assert final["refusal"] == "invalid_decision"
    assert rig.executor.calls == 0
    assert nodes_of(final) == [
        "propose",
        APPROVE_NODE,
        REGUARD_NODE,
        APPROVE_NODE,
        REGUARD_NODE,
        APPROVE_NODE,
    ]


def test_edit_then_approve_executes_the_edited_action() -> None:
    rig = Rig()
    rig.start()
    rig.resume(rig.edit_value(EDITED))
    edited = rig.action()
    final = rig.resume(rig.approve_value(rig.mint(edited)))
    assert final["outcome"] == "executed"
    assert [e.args_sha256 for e in rig.erp.executed] == [edited.args_sha256]
    assert edited.args["draft_sha256"] == text_sha256(rig.body(EDITED))


def test_token_of_an_earlier_round_cannot_run_the_edited_args() -> None:
    rig = Rig()
    rig.start()
    stale = rig.mint()
    rig.resume(rig.edit_value(EDITED))
    final = rig.resume(rig.approve_value(stale))
    assert final["outcome"] == "error"
    assert final["refusal"] is None
    assert final["tool_events"][-1]["error"] == "wrong_args"
    assert rig.erp.executed == []


def test_one_edit_round_when_configured() -> None:
    rig = Rig(max_edit_rounds=1)
    rig.start()
    payload = payload_of(rig.resume(rig.edit_value(EDITED)))
    assert payload["edit_round"] == 1
    final = rig.resume(rig.edit_value(EDITED_AGAIN))
    assert final["refusal"] == "invalid_decision"


@pytest.mark.parametrize("reason", ["output_pi", "cross_account", "invalid_args"])
def test_failed_reguard_is_withheld(reason: str) -> None:
    def failing(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        return EditCheck.model_validate({"ok": False, "reason": reason})

    rig = Rig(check_edit=failing)
    rig.start()
    final = rig.resume(rig.edit_value(EDITED))
    assert final["outcome"] == "withheld"
    assert final["refusal"] == reason
    assert rig.executor.calls == 0
    assert final["trace"][-1] == {
        "node": REGUARD_NODE,
        "ok": False,
        "reason": reason,
        "edit_round": 0,
    }


def test_reguard_refuses_an_action_of_another_tool() -> None:
    def other_tool(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=stub_quarantine)
        asset = sorted(gmao.asset_ids)[0]
        rows = gmao.list_work_orders(asset, None, since_days=100_000, limit=1)
        args = CreateWorkRequestArgs(
            asset_id=asset,
            symptom_code="SYM-FUITE",
            failure_code="INDETERMINE",
            cited_wo_ids=tuple(row.wo_id for row in rows) or ("OT-2026-0001",),
        )
        action = ProposedAction.build("gmao.create_work_request", args)
        return EditCheck(ok=True, proposed_action=action.model_dump(mode="json"))

    rig = Rig(check_edit=other_tool)
    rig.start()
    final = rig.resume({"decision": "edit", "edited_args": {"failure_code": "INDETERMINE"}})
    assert final["outcome"] == "withheld"
    assert final["refusal"] == "invalid_args"


def test_reguard_refuses_an_inconsistent_action() -> None:
    rig_holder: list[Rig] = []

    def tampered(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        dump = reply_action(rig_holder[0].order_id, "corps").model_dump(mode="json")
        dump["args_sha256"] = "0" * 64
        return EditCheck(ok=True, proposed_action=dump)

    rig = Rig(check_edit=tampered)
    rig_holder.append(rig)
    rig.start()
    final = rig.resume(rig.edit_value(EDITED))
    assert final["refusal"] == "invalid_args"


def test_reguard_raises_on_a_check_of_the_wrong_type() -> None:
    def wrong(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        return {"ok": True}  # type: ignore[return-value]

    rig = Rig(check_edit=wrong)
    rig.start()
    with pytest.raises(TypeError) as info:
        rig.resume(rig.edit_value(EDITED))
    assert str(info.value) == "reguard:edit_check_type"


# --------------------------------------------------------------------------- direct calls
class _NoCall:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, name: str, args: Mapping[str, object], *, token: Any = None) -> Any:
        self.calls += 1
        raise AssertionError("apply:unexpected_call")


def test_apply_without_a_consistent_approved_action_makes_no_call() -> None:
    executor = _NoCall()
    apply = make_apply_node(executor)
    action = reply_action("CMD-2026-0001", BODY).model_dump(mode="json")
    cases: list[OpsState] = [
        {},
        {"proposed_action": action},
        {"proposed_action": action, "decision": {"decision": "reject"}},
        {
            "proposed_action": {**action, "args_sha256": "1" * 64},
            "decision": {"decision": "approve"},
        },
    ]
    for state in cases:
        update = apply(state)
        assert update.get("outcome") == "error"
        assert update.get("refusal") is None
        assert update.get("trace") == [{"node": APPLY_NODE, "ok": False, "error": "invalid_action"}]
    assert executor.calls == 0


def test_reguard_without_an_edit_decision_fails_closed() -> None:
    reguard = make_reguard_node(lambda state, decision: EditCheck(ok=False, reason="output_pi"))
    decisions: list[dict[str, JsonValue] | None] = [
        None,
        {"decision": "approve"},
        {"decision": "edit"},
    ]
    for decision in decisions:
        update = reguard({"decision": decision, "edit_round": 1})
        assert update.get("outcome") == "rejected"
        assert update.get("refusal") == "invalid_decision"


def test_handoff_only_after_an_executed_write() -> None:
    handoff = make_handoff_node()
    for outcome, expected in (("executed", True), ("error", False), (None, False)):
        update = handoff({"outcome": outcome})
        assert update.get("handed_off") is expected
        assert update.get("trace") == [{"node": HANDOFF_NODE, "handed_off": expected}]


def test_routes() -> None:
    assert route_after_approve({"decision": {"decision": "approve"}}) == APPLY_NODE
    assert route_after_approve({"decision": {"decision": "edit"}}) == REGUARD_NODE
    assert route_after_approve({"decision": {"decision": "reject"}, "outcome": "rejected"}) == END
    assert route_after_approve({"decision": {"decision": "approve"}, "outcome": "x"}) == END
    assert route_after_approve({"decision": {"decision": "edit"}, "outcome": "x"}) == END
    assert route_after_approve({}) == END
    assert route_after_reguard({}) == APPROVE_NODE
    assert route_after_reguard({"outcome": "withheld"}) == END


@pytest.mark.parametrize("rounds", [0, 3, -1, True, 2.0, "2"])
def test_edit_rounds_are_bounded(rounds: object) -> None:
    with pytest.raises(ValueError, match=r"^approval:max_edit_rounds$"):
        check_edit_rounds(rounds)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"^approval:max_edit_rounds$"):
        make_approve_node(max_edit_rounds=rounds)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- decision model
def test_decision_rules() -> None:
    assert parse_decision({"decision": "approve"}) is not None
    assert parse_decision({"decision": "reject"}) is not None
    assert parse_decision({"decision": "edit", "edited_text": "x"}) is not None
    assert parse_decision({"decision": "edit", "edited_args": {"a": [1, "b", None]}}) is not None
    assert parse_decision({"decision": "approve", "token": {"k": "v"}}) is not None
    assert parse_decision(object()) is None
    for raw in [*NOT_RESUMABLE, *INVALID_DECISIONS]:
        assert parse_decision(raw) is None


def test_decision_repr_and_errors_hold_no_text_or_token() -> None:
    decision = ApprovalDecision(decision="edit", edited_text=SECRET_TEXT)
    assert SECRET_TEXT not in repr(decision)
    approved = ApprovalDecision(decision="approve", token={"nonce": "f" * 32})
    assert "f" * 32 not in repr(approved)
    with pytest.raises(ValidationError) as info:
        ApprovalDecision.model_validate({"decision": "reject", "edited_text": SECRET_TEXT})
    assert SECRET_TEXT not in str(info.value)
    assert info.value.__context__ is None or SECRET_TEXT not in str(info.value.__context__)


def test_edit_check_rules() -> None:
    action = reply_action("CMD-2026-0001", BODY).model_dump(mode="json")
    EditCheck(ok=True, proposed_action=action)
    EditCheck(ok=False, reason="cross_account")
    bad: list[dict[str, object]] = [
        {"ok": True},
        {"ok": True, "reason": "output_pi", "proposed_action": action},
        {"ok": False},
        {"ok": False, "reason": "output_pi", "draft_body": "corps"},
        {"ok": False, "reason": "output_pi", "proposed_action": action},
        {"ok": False, "reason": "ungrounded"},
    ]
    for values in bad:
        with pytest.raises(ValidationError):
            EditCheck.model_validate(values)
    hidden = EditCheck(ok=True, draft_body=SECRET_TEXT, proposed_action=action)
    assert SECRET_TEXT not in repr(hidden)


# --------------------------------------------------------------------------- state
def test_proposed_action_build_and_consistency() -> None:
    action = reply_action("CMD-2026-0001", BODY)
    assert len(action.action_id) == 32
    assert action.args == {"order_id": "CMD-2026-0001", "draft_sha256": text_sha256(BODY)}
    other = reply_action("CMD-2026-0001", BODY)
    assert other.action_id != action.action_id, "build:fresh_action_id"
    assert other.args_sha256 == action.args_sha256
    assert "CMD-2026-0001" not in repr(action)
    assert "Réponse" not in repr(action)
    assert ProposedAction.model_validate(action.model_dump(mode="json")) == action


def test_proposed_action_refuses_changed_args_without_showing_them() -> None:
    dump = reply_action("CMD-2026-0001", BODY).model_dump(mode="json")
    changed = {**dump, "args": {"order_id": "CMD-2026-0002", "draft_sha256": text_sha256(BODY)}}
    with pytest.raises(ValidationError) as info:
        ProposedAction.model_validate(changed)
    assert "proposed_action:args_sha256" in str(info.value)
    assert "CMD-2026-0002" not in str(info.value)
    invalid = {**dump, "args": {"order_id": "CMD-26-2", "draft_sha256": SECRET_TEXT}}
    with pytest.raises(ValidationError) as info:
        ProposedAction.model_validate(invalid)
    assert "proposed_action:invalid_args" in str(info.value)
    assert SECRET_TEXT not in str(info.value)
    extra = {**dump, "args": {**dump["args"], "note": "x"}}
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(extra)


def test_proposed_action_build_needs_the_tools_args_model() -> None:
    args = RecordReplyPreparedArgs(order_id="CMD-2026-0001", draft_sha256=text_sha256(BODY))
    with pytest.raises(ValueError, match=r"^proposed_action:args_model$"):
        ProposedAction.build("gmao.create_work_request", args)
    with pytest.raises(ValueError, match=r"^proposed_action:args_model$"):
        ProposedAction.build("erp.get_order", args)  # type: ignore[arg-type]


def test_text_sha256() -> None:
    assert text_sha256("é") == hashlib.sha256("é".encode()).hexdigest()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ({"a": [1, 2.5, "x", True, None, {"b": []}]}, True),
        ((1, 2), False),
        (b"x", False),
        ({1: "a"}, False),
        (float("nan"), False),
        (float("inf"), False),
        ({"a": {1, 2}}, False),
        (ProposedAction, False),
        ([object()], False),
    ],
)
def test_is_json_value(value: object, expected: bool) -> None:
    assert is_json_value(value) is expected


def test_is_json_value_refuses_subclasses_and_models() -> None:
    class Text(str):
        pass

    class Items(list[int]):
        pass

    assert is_json_value(Text("a")) is False
    assert is_json_value(Items([1])) is False
    assert is_json_value(reply_action("CMD-2026-0001", BODY)) is False


def test_every_checkpoint_of_a_run_holds_json_primitives_only() -> None:
    rig = Rig()
    rig.start()
    rig.resume(rig.edit_value(EDITED))
    rig.resume(rig.approve_value(rig.mint()))
    fields = set(OpsState.__annotations__)
    checkpoints = list(rig.saver.list(rig.config))
    assert len(checkpoints) >= 6
    seen: set[str] = set()
    for checkpoint in checkpoints:
        values = checkpoint.checkpoint["channel_values"]
        for key, value in values.items():
            if key in fields:
                seen.add(key)
                assert is_json_value(value), key
    assert {"proposed_action", "trace", "tool_events", "decision", "outcome"} <= seen
    rig.saver.delete_thread(rig.thread_id)
    assert list(rig.saver.list(rig.config)) == []


def test_args_edit_keeps_the_draft_body() -> None:
    holder: list[Rig] = []

    def args_check(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        # A structured edit of a reply keeps the shown text, so its digest too.
        action = reply_action(holder[0].order_id, str(state.get("draft_body")))
        return EditCheck(ok=True, proposed_action=action.model_dump(mode="json"))

    rig = Rig(check_edit=args_check)
    holder.append(rig)
    rig.start()
    before = rig.state()
    first = rig.action()
    payload = payload_of(rig.resume({"decision": "edit", "edited_args": {"draft_sha256": "b"}}))
    after = rig.state()
    assert payload["edit_round"] == 1
    assert after.get("draft_body") == before.get("draft_body")
    assert after.get("draft_sha256") == before.get("draft_sha256")
    assert rig.action().args["draft_sha256"] == text_sha256(rig.body(BODY))
    assert rig.action().action_id != first.action_id


def test_reply_action_must_carry_the_digest_of_the_text_shown() -> None:
    holder: list[Rig] = []

    def other_body(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        # The edited text is shown, but the action binds another text.
        action = reply_action(holder[0].order_id, "autre corps")
        return EditCheck(
            ok=True, draft_body=decision.edited_text, proposed_action=action.model_dump(mode="json")
        )

    def args_only(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        # No new text: the shown text stays, and the action binds another one.
        action = reply_action(holder[0].order_id, "autre corps")
        return EditCheck(ok=True, proposed_action=action.model_dump(mode="json"))

    for check in (other_body, args_only):
        holder.clear()
        rig = Rig(check_edit=check)
        holder.append(rig)
        rig.start()
        final = rig.resume(rig.edit_value(EDITED))
        assert final["outcome"] == "withheld", check.__name__
        assert final["refusal"] == "invalid_args"
        assert final["trace"][-1] == {
            "node": REGUARD_NODE,
            "ok": False,
            "reason": "invalid_args",
            "edit_round": 0,
        }
        assert rig.executor.calls == 0


def test_text_bound() -> None:
    action = reply_action("CMD-2026-0001", BODY)
    assert text_bound(action, BODY)
    assert not text_bound(action, BODY + " ")
    assert not text_bound(action, None)
    gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=stub_quarantine)
    asset = sorted(gmao.asset_ids)[0]
    rows = gmao.list_work_orders(asset, None, since_days=100_000, limit=1)
    args = CreateWorkRequestArgs(
        asset_id=asset,
        symptom_code="SYM-FUITE",
        failure_code="INDETERMINE",
        cited_wo_ids=tuple(row.wo_id for row in rows) or ("OT-2026-0001",),
    )
    work = ProposedAction.build("gmao.create_work_request", args)
    assert text_bound(work, None), "text_bound:no_text_for_a_work_request"


def test_a_failing_second_edit_is_withheld() -> None:
    def check(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        text = decision.edited_text or ""
        if "[courriel]" in text:
            return EditCheck(ok=False, reason="output_pi")
        holder = state.get("ids") or [""]
        action = reply_action(holder[0], text)
        return EditCheck(ok=True, draft_body=text, proposed_action=action.model_dump(mode="json"))

    rig = Rig(check_edit=check)
    rig.start()
    assert payload_of(rig.resume(rig.edit_value(EDITED)))["edit_round"] == 1
    final = rig.resume({"decision": "edit", "edited_text": "Écrivez à [courriel]."})
    assert final["outcome"] == "withheld" and final["refusal"] == "output_pi"
    assert final["trace"][-1] == {
        "node": REGUARD_NODE,
        "ok": False,
        "reason": "output_pi",
        "edit_round": 1,
    }
    assert rig.executor.calls == 0 and rig.erp.executed == []


def test_approve_node_exposes_its_round_limit() -> None:
    node = make_approve_node(max_edit_rounds=1)
    assert isinstance(node, ApproveNode) and node.max_edit_rounds == 1
    assert approve_node_rounds(node) == 1

    class Wrapper:
        func = node

    assert approve_node_rounds(Wrapper()) == 1, "rounds:langgraph_runnable"

    def wrapped(state: OpsState) -> OpsState:
        return node(state)

    assert approve_node_rounds(wrapped) is None
    assert approve_node_rounds(None) is None
    rig = Rig(max_edit_rounds=1)
    spec = rig.graph.builder.nodes[APPROVE_NODE]
    assert approve_node_rounds(spec.runnable) == 1, "rounds:compiled_graph"
