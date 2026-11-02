"""Module 3 trace scrubbing and evaluation probes (I5 spec decisions 5 and 6).

``scrub_entry`` keeps only allowlisted, value-free details per node; the probes record
counts and an ordered timeline of log writes, executor calls, connector effects and
model calls, never a value. The simulated ERP and GMAO of ``data/eval/ops/`` are read
with a stub quarantine; no holdout is read. Assertion messages cite rule names only.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from pydantic import BaseModel

from alverno_lab.agents.ops import (
    CountingChat,
    CountingConnector,
    CountingExecutor,
    FailingLog,
    ProbeChat,
    Timeline,
    TimelineEvent,
    TimelineLog,
    scrub_entry,
)
from alverno_lab.agents.ops.scrub import MAX_COUNT, MAX_LIST, NODE_KEYS, UNKNOWN_NODE
from alverno_lab.connectors import (
    ConnectorError,
    QuarantinedText,
    SimErp,
    SimGmao,
    WorkRequest,
    WriteToolName,
)
from alverno_lab.llm import FakeChat
from alverno_lab.privacy.access_log import AccessLogError
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord
from alverno_lab.tools import (
    ApprovalAuthority,
    ApprovalToken,
    RecordReplyPreparedArgs,
    ToolExecutor,
    args_sha256,
)

REPO_ROOT = Path(__file__).parent.parent
OPS = REPO_ROOT / "data" / "eval" / "ops"
RECORD_REPLY: WriteToolName = "erp.record_reply_prepared"
CREATE_WORK_REQUEST: WriteToolName = "gmao.create_work_request"
ACTION_ID = "0123456789abcdef0123456789abcdef"
DIGEST = "a" * 64
NOON = dt.datetime(2026, 11, 2, 12, 30, tzinfo=dt.UTC)
SECRET = "Texte libre de la demande, avec une phrase entière"


def stub_quarantine(text: str) -> QuarantinedText:
    """Stub quarantine: a fixed placeholder, so no fixture text reaches a view."""
    return QuarantinedText(text="texte mis en quarantaine", flags=("stub",))


# --------------------------------------------------------------------------- scrub
def test_approval_node_allowlists_follow_the_spec() -> None:
    assert NODE_KEYS["approve"] == {"decision", "edit_round", "valid", "reason"}
    assert NODE_KEYS["reguard"] == {"ok", "reason", "edit_round"}
    assert NODE_KEYS["apply"] == {"tool", "ok", "error"}
    assert NODE_KEYS["handoff"] == {"handed_off"}


def test_scrub_keeps_allowlisted_codes_counts_and_flags() -> None:
    entry = scrub_entry(
        "approve",
        {
            "decision": "edit",
            "edit_round": 1,
            "valid": True,
            "reason": "invalid_decision",
            "edited_text": SECRET,
            "token": {"nonce": "f" * 32},
            "payload": {"draft_id": ACTION_ID},
        },
    )
    assert entry == {
        "node": "approve",
        "decision": "edit",
        "edit_round": 1,
        "valid": True,
        "reason": "invalid_decision",
    }


def test_scrub_keeps_tool_names_and_fixture_ids() -> None:
    entry = scrub_entry("fetch", {"tool": "erp.get_order", "ids": ["CMD-2026-0412"], "ok": True})
    assert entry == {"node": "fetch", "tool": "erp.get_order", "ids": ["CMD-2026-0412"], "ok": True}
    ids = ("EQ-PLI-01", "OT-2026-0008", "CMD-2026-0001")
    assert scrub_entry("route", {"ids": ids})["ids"] == list(ids)


@pytest.mark.parametrize(
    "value",
    [
        SECRET,
        "Code",
        "with space",
        "x" * 41,
        "",
        DIGEST,
        ACTION_ID,
        "CT-001",
        "CLI-003",
        "erp.send_mail",
        "CMD-2026-04120",
        "CMD-٢٠٢٦-0412",
        MAX_COUNT + 1,
        -1,
        2.5,
        None,
        b"bytes",
        {"tool": "erp.get_order"},
        ["ok", SECRET],
        ["ok"] * (MAX_LIST + 1),
        [1, 2],
        [["ok"]],
    ],
)
def test_scrub_drops_every_other_value(value: object) -> None:
    assert scrub_entry("apply", {"error": value}) == {"node": "apply"}


def test_scrub_drops_subclasses_of_primitives() -> None:
    class Text(str):
        pass

    class Items(list[str]):
        pass

    assert scrub_entry("apply", {"error": Text("ok")}) == {"node": "apply"}
    assert scrub_entry("route", {"ids": Items(["CMD-2026-0001"])}) == {"node": "route"}


def test_scrub_keeps_booleans_and_bounded_counts() -> None:
    assert scrub_entry("reguard", {"ok": False, "edit_round": 0}) == {
        "node": "reguard",
        "ok": False,
        "edit_round": 0,
    }
    assert scrub_entry("gate", {"record_count": MAX_COUNT})["record_count"] == MAX_COUNT
    assert scrub_entry("quarantine", {"flags": ("stub", "action_pattern")})["flags"] == [
        "stub",
        "action_pattern",
    ]
    assert scrub_entry("quarantine", {"flags": []})["flags"] == []


def test_scrub_drops_keys_outside_the_allowlist() -> None:
    assert scrub_entry("handoff", {"handed_off": True, "ok": True}) == {
        "node": "handoff",
        "handed_off": True,
    }


def test_unknown_node_keeps_no_detail() -> None:
    assert scrub_entry("custom_node", {"ok": True, "tool": "erp.get_order"}) == {
        "node": "custom_node"
    }
    assert scrub_entry(SECRET, {"ok": True}) == {"node": UNKNOWN_NODE}
    assert scrub_entry(42, {"ok": True}) == {"node": UNKNOWN_NODE}  # type: ignore[arg-type]


# --------------------------------------------------------------------------- timeline
def test_timeline_orders_events_and_is_append_only() -> None:
    timeline = Timeline()
    first = timeline.add("log", "approved", args_sha256=DIGEST)
    second = timeline.add("effect", RECORD_REPLY, action_id=ACTION_ID, args_sha256=DIGEST, ok=None)
    third = timeline.add("chat", "gemma4:e4b")
    assert [e.seq for e in (first, second, third)] == [0, 1, 2]
    assert len(timeline) == 3
    assert timeline.events == (first, second, third)
    assert timeline.of_kind("effect") == (second,)
    events = timeline.events
    timeline.add("call", RECORD_REPLY, ok=False, detail_code="no_token")
    assert len(events) == 3, "timeline:events_is_a_copy"


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "log", "name": SECRET},
        {"kind": "call", "name": "gemma4:e4b"},
        {"kind": "chat", "name": "model with spaces"},
        {"kind": "send", "name": "approved"},
        {"kind": "log", "name": "approved", "action_id": "x"},
        {"kind": "log", "name": "approved", "args_sha256": "abc"},
        {"kind": "log", "name": "approved", "ok": 1},
        {"kind": "log", "name": "approved", "detail_code": SECRET},
        {"kind": "log", "name": "approved", "seq": "0"},
    ],
)
def test_timeline_event_refuses_values(fields: dict[str, object]) -> None:
    values: dict[str, object] = {"seq": 0, **fields}
    with pytest.raises(ValueError, match=r"^timeline:invalid_event$"):
        TimelineEvent(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- logs
def _answered(log: TimelineLog) -> OpsAccessLogRecord:
    return log.record(channel="eval", flow="order_status", event="answered", latency_ms=1)


def test_timeline_log_records_after_each_write(tmp_path: Path) -> None:
    timeline = Timeline()
    log = TimelineLog(tmp_path, prefix="service", timeline=timeline, clock=lambda: NOON)
    rec = _answered(log)
    log.append(rec)
    assert log.records == [rec]
    assert timeline.events == (
        TimelineEvent(seq=0, kind="log", name="answered", args_sha256=None, detail_code=None),
    )
    assert log.read(NOON.date()) == [rec]
    drafted = log.record(
        channel="eval",
        flow="customer_reply",
        event="drafted",
        draft_id=ACTION_ID,
        action_tool=RECORD_REPLY,
        action_args_sha256=DIGEST,
        latency_ms=2,
    )
    log.append(drafted)
    assert timeline.events[-1].args_sha256 == DIGEST
    assert timeline.events[-1].action_id is None, "log:draft_id_is_not_an_action_id"


def test_timeline_log_records_nothing_when_the_write_fails(tmp_path: Path) -> None:
    service = TimelineLog(tmp_path, prefix="service", clock=lambda: NOON)
    maint = TimelineLog(tmp_path, prefix="maint", clock=lambda: NOON)
    with pytest.raises(AccessLogError):
        maint.append(_answered(service))
    assert maint.records == []
    assert len(maint.timeline) == 0


def test_failing_log_fails_after_n_writes(tmp_path: Path) -> None:
    timeline = Timeline()
    log = FailingLog(
        tmp_path, prefix="service", fail_after=1, timeline=timeline, clock=lambda: NOON
    )
    log.append(_answered(log))
    with pytest.raises(AccessLogError):
        log.append(_answered(log))
    assert len(log.records) == 1
    assert log.failures == 1
    assert len(timeline) == 1
    assert len(log.read(NOON.date())) == 1


def test_failing_log_fails_first_by_default(tmp_path: Path) -> None:
    log = FailingLog(tmp_path, prefix="maint", clock=lambda: NOON)
    with pytest.raises(AccessLogError):
        log.append(log.record(channel="eval", flow="diagnosis", event="answered", latency_ms=0))
    assert log.records == []
    assert not log.path_for(NOON.date()).exists()


@pytest.mark.parametrize("fail_after", [-1, True, 1.0])
def test_failing_log_bound(tmp_path: Path, fail_after: object) -> None:
    with pytest.raises(ValueError, match=r"^failing_log:fail_after$"):
        FailingLog(tmp_path, prefix="service", fail_after=fail_after)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- connectors
class Bench:
    """Counting probes over fresh simulated connectors and a real executor."""

    def __init__(self, *, erp: bool = True, gmao: bool = True) -> None:
        self.erp = SimErp.from_yaml(OPS / "erp.yaml", quarantine=stub_quarantine)
        self.gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=stub_quarantine)
        self.timeline = Timeline()
        self.connector = CountingConnector(
            timeline=self.timeline,
            erp=self.erp if erp else None,
            gmao=self.gmao if gmao else None,
        )
        self.authority = ApprovalAuthority()
        self.executor = CountingExecutor(
            ToolExecutor(self.connector, self.connector, self.authority.verifier), self.timeline
        )
        self.order_id = sorted(self.erp.order_ids)[0]
        self.asset_id = sorted(self.gmao.asset_ids)[0]

    def reply_args(self) -> dict[str, object]:
        return {"order_id": self.order_id, "draft_sha256": DIGEST}

    def mint(self, tool: WriteToolName, digest: str) -> ApprovalToken:
        return self.authority.mint(ACTION_ID, tool, digest)


def test_counting_connector_counts_reads_of_both_sides() -> None:
    bench = Bench()
    bench.connector.get_order(bench.order_id)
    bench.connector.get_order_contact(bench.order_id)
    bench.connector.get_asset(bench.asset_id)
    rows = bench.connector.list_work_orders(bench.asset_id, None, since_days=100_000, limit=12)
    catalogue = bench.connector.failure_catalog()
    assert catalogue, "catalogue:not_empty"
    assert bench.connector.reads == 5
    assert bench.connector.writes == 0
    assert bench.connector.total == 5
    assert bench.connector.calls == {
        "get_order": 1,
        "get_order_contact": 1,
        "get_asset": 1,
        "list_work_orders": 1,
        "failure_catalog": 1,
    }
    assert isinstance(rows, tuple)
    assert len(bench.timeline) == 0, "reads:no_event"


def test_effect_precedes_the_call_event_and_carries_the_token_action() -> None:
    bench = Bench()
    args = bench.reply_args()
    digest = args_sha256(RecordReplyPreparedArgs.model_validate(args))
    result = bench.executor.call(RECORD_REPLY, args, token=bench.mint(RECORD_REPLY, digest))
    assert result.ok
    effect, call = bench.timeline.events
    assert (effect.kind, effect.name, effect.action_id, effect.args_sha256) == (
        "effect",
        RECORD_REPLY,
        ACTION_ID,
        digest,
    )
    assert (call.kind, call.name, call.action_id, call.args_sha256, call.ok) == (
        "call",
        RECORD_REPLY,
        ACTION_ID,
        digest,
        True,
    )
    assert call.detail_code is None
    assert bench.connector.writes == 1
    assert bench.executor.calls == 1
    assert bench.executor.records[-1].executed is True
    assert bench.erp.executed[0].args_sha256 == digest


def test_work_request_effect_digest_matches_the_side_effect() -> None:
    bench = Bench()
    rows = bench.gmao.list_work_orders(bench.asset_id, None, since_days=100_000, limit=2)
    args: dict[str, object] = {
        "asset_id": bench.asset_id,
        "symptom_code": "SYM-FUITE",
        "failure_code": "INDETERMINE",
        "cited_wo_ids": [row.wo_id for row in rows],
    }
    digest = args_sha256(WorkRequest.model_validate(args))
    token = bench.mint(CREATE_WORK_REQUEST, digest)
    result = bench.executor.call(CREATE_WORK_REQUEST, args, token=token)
    assert result.ok
    assert bench.timeline.of_kind("effect")[0].args_sha256 == digest
    assert bench.gmao.executed[0].args_sha256 == digest
    assert bench.connector.calls == {"create_work_request": 1}


def test_refused_calls_are_timed_without_any_effect() -> None:
    bench = Bench()
    no_token = bench.executor.call(RECORD_REPLY, bench.reply_args())
    invalid = bench.executor.call(RECORD_REPLY, {"order_id": SECRET})
    unknown = bench.executor.call("erp.send", {})
    assert [r.error for r in (no_token, invalid, unknown)] == [
        "no_token",
        "invalid_args",
        "unknown_tool",
    ]
    calls = bench.timeline.of_kind("call")
    assert [c.name for c in calls] == [RECORD_REPLY, RECORD_REPLY, "unknown"]
    assert [c.detail_code for c in calls] == ["no_token", "invalid_args", "unknown_tool"]
    assert all(c.action_id is None for c in calls)
    assert calls[0].args_sha256 is not None
    assert calls[2].args_sha256 is not None
    assert bench.timeline.of_kind("effect") == ()
    assert bench.connector.writes == 0


def test_missing_side_is_a_connector_error() -> None:
    bench = Bench(erp=False, gmao=False)
    with pytest.raises(ConnectorError, match=r"^counting:no_erp$"):
        bench.connector.get_order(bench.order_id)
    with pytest.raises(ConnectorError, match=r"^counting:no_gmao$"):
        bench.connector.failure_catalog()
    result = bench.executor.call("gmao.get_asset", {"asset_id": bench.asset_id})
    assert result.error == "connector_error"
    assert bench.connector.reads == 3


def test_write_with_a_junk_token_records_an_effect_without_ids() -> None:
    class Raising:
        @property
        def action_id(self) -> str:
            raise RuntimeError(SECRET)

        tool = RECORD_REPLY
        args_sha256 = DIGEST

    bench = Bench()
    with pytest.raises(ConnectorError):
        bench.connector.record_reply_prepared(bench.order_id, "not a digest", token=Raising())
    effect = bench.timeline.events[-1]
    assert (effect.kind, effect.action_id, effect.args_sha256) == ("effect", None, None)
    assert bench.connector.writes == 1
    assert bench.erp.executed == []


def test_counting_executor_needs_an_executor() -> None:
    with pytest.raises(TypeError, match=r"^CountingExecutor:executor_required$"):
        CountingExecutor(object(), Timeline())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- chat
class Reply(BaseModel):
    """A tiny reply schema."""

    answer: str


def test_counting_chat_counts_before_delegating() -> None:
    timeline = Timeline()
    inner = FakeChat([{"answer": "oui"}], model="gemma4:e4b")
    chat = CountingChat(inner, timeline)
    assert chat.complete_json("système", "question", Reply).answer == "oui"
    assert chat.attempts == 1
    assert chat.model_name == "gemma4:e4b"
    assert chat.stats is inner.stats
    assert [(e.kind, e.name) for e in timeline.events] == [("chat", "gemma4:e4b")]

    failing = CountingChat(FakeChat(error=RuntimeError("down"), model="nom avec espaces"), timeline)
    with pytest.raises(RuntimeError):
        failing.complete_json("système", "question", Reply)
    assert failing.attempts == 1, "chat:counted_before_failure"
    assert timeline.events[-1].name == "chat"


def test_counting_chat_without_timeline() -> None:
    chat = CountingChat(FakeChat([{"answer": "oui"}]))
    chat.complete_json("s", "u", Reply)
    assert chat.attempts == 1


def test_probe_chat_keeps_prompts_out_of_repr() -> None:
    inner = FakeChat([{"answer": "oui"}])
    probe = ProbeChat(CountingChat(inner))
    assert probe.complete_json("système", SECRET, Reply).answer == "oui"
    assert probe.prompts == [("système", SECRET)]
    assert probe.model_name == inner.model_name
    assert probe.stats is inner.stats
    assert SECRET not in repr(probe)
    assert SECRET not in str(probe)
    assert repr(probe) == "ProbeChat(prompts=<1 hidden>)"
