"""Access log of module 3 (spec I4 decision 7, ADR 0014): record schema, prefixes, purge.

The record has exactly the fields of the ADR 0014 table, in order; the synthetic example
of the ADR validates; every cross-field rule of « Événements » and « Précisions » fails
with an error that names the rule, never a value; no field carries free text. The day
files of each prefix are read, written and purged by its own instance only, and the HR
file names stay as they were, and so does the HR error text; ``alverno access purge``
purges the three prefixes. Assertion messages cite events and rules, never a value.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from alverno_lab.cli import app
from alverno_lab.privacy.access_log import (
    ACCESS_LOG_PREFIXES,
    PI_COUNT_CAP,
    AccessLog,
    AccessLogError,
    AccessLogRecord,
)
from alverno_lab.privacy.ops_access_log import (
    MAX_ENTITY_REFS,
    OPS_EVENTS,
    OpsAccessLogRecord,
    broken_rules,
)
from alverno_lab.privacy.ops_types import OPS_REFUSAL_CODES
from alverno_lab.privacy.types import PIType

from .conftest import REPO_ROOT

ADR = REPO_ROOT / "docs" / "adr" / "0014-ops-access-log.md"
NOW = dt.datetime(2026, 11, 1, 14, 37, 12, tzinfo=dt.UTC)
# The ADR 0014 table, in order.
FIELDS = (
    "v",
    "module",
    "event_id",
    "draft_id",
    "ts",
    "channel",
    "flow",
    "event",
    "reason",
    "tool_calls",
    "entity_refs",
    "account_ref",
    "pi_types",
    "pi_count",
    "quarantine_flags",
    "action_tool",
    "action_args_sha256",
    "executed_external_send",
    "edit_round",
    "model",
    "latency_ms",
    "approver",
    "actor",
)
DRAFT = "fedcba9876543210fedcba9876543210"
SHA = "0" * 64
Module = Literal["service", "maint"]
OpsLog = AccessLog[OpsAccessLogRecord]

# Valid fields of each event in 3a (service); maint() adapts them to 3b.
SERVICE_EVENTS: dict[str, dict[str, Any]] = {  # Any: heterogeneous record fields
    "answered": {"flow": "order_status", "tool_calls": ("erp.get_order",)},
    "refused": {"reason": "restricted_info"},
    "withheld": {"flow": "customer_reply", "draft_id": DRAFT, "reason": "output_pi"},
    "drafted": {
        "flow": "customer_reply",
        "draft_id": DRAFT,
        "action_tool": "erp.record_reply_prepared",
        "action_args_sha256": SHA,
        "model": "gemma4:e4b",
    },
    "approved": {
        "flow": "customer_reply",
        "draft_id": DRAFT,
        "approver": "operator",
        "action_tool": "erp.record_reply_prepared",
        "action_args_sha256": SHA,
    },
    "edit_submitted": {
        "flow": "customer_reply",
        "draft_id": DRAFT,
        "approver": "operator",
        "action_tool": "erp.record_reply_prepared",
    },
    "re_presented": {
        "flow": "customer_reply",
        "draft_id": DRAFT,
        "edit_round": 1,
        "action_tool": "erp.record_reply_prepared",
        "action_args_sha256": SHA,
    },
    "rejected": {
        "flow": "customer_reply",
        "draft_id": DRAFT,
        "approver": "operator",
        "reason": "approval_rejected",
    },
    "abandoned": {"flow": "customer_reply", "draft_id": DRAFT},
    "error": {"flow": "order_status", "reason": "connector_unavailable"},
}
MAINT_FLOWS = {"order_status": "diagnosis", "customer_reply": "work_request"}


def fields_of(module: Module, event: str, /, **over: Any) -> dict[str, Any]:  # Any: see above
    """Valid record fields of ``event`` in ``module``, with ``over`` applied."""
    base = dict(SERVICE_EVENTS[event])
    if module == "service":
        base.setdefault("entity_refs", ("CMD-2026-0412",))
        base["account_ref"] = "CLI-003"
    else:
        if "flow" in base:
            base["flow"] = MAINT_FLOWS[base["flow"]]
        if "action_tool" in base:
            base["action_tool"] = "gmao.create_work_request"
        base["entity_refs"] = ("EQ-PLI-01", "OT-2026-0008")
    return {"channel": "eval", "event": event, "latency_ms": 12, **base, **over}


class Clock:
    """A settable clock, injected into the log."""

    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


def ops_log(directory: Path, prefix: Module, clock: Clock | None = None) -> OpsLog:
    return AccessLog(
        directory, prefix=prefix, record_type=OpsAccessLogRecord, clock=clock or Clock(NOW)
    )


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "access"


@pytest.fixture
def service(directory: Path) -> OpsLog:
    return ops_log(directory, "service")


@pytest.fixture
def maint(directory: Path) -> OpsLog:
    return ops_log(directory, "maint")


def day_file(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- schema
def test_fields_are_the_adr_table_in_order() -> None:
    assert tuple(OpsAccessLogRecord.model_fields) == FIELDS
    config = OpsAccessLogRecord.model_config
    assert config.get("frozen") and config.get("extra") == "forbid"
    assert config.get("hide_input_in_errors")


def test_the_adr_synthetic_example_validates() -> None:
    text = ADR.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.startswith('{"v": 1, "module"')]
    assert len(lines) == 2
    records = [OpsAccessLogRecord.model_validate_json(line) for line in lines]
    assert [r.event for r in records] == ["drafted", "approved"]
    assert records[0].draft_id == records[1].draft_id
    assert records[0].day == dt.date(2026, 11, 1)
    for line, rec in zip(lines, records, strict=True):
        assert json.loads(rec.model_dump_json()) == json.loads(line)


@pytest.mark.parametrize("module", ["service", "maint"])
@pytest.mark.parametrize("event", sorted(SERVICE_EVENTS))
def test_every_event_has_a_valid_record(directory: Path, module: Module, event: str) -> None:
    log = ops_log(directory, module)
    rec = log.record(**fields_of(module, event))
    assert rec.module == module and rec.event == event
    assert rec.ts == "2026-11-01T14:00Z" and len(rec.event_id) == 32
    assert rec.executed_external_send is False and rec.actor is None
    assert list(broken_rules(rec)) == []


def test_the_cases_cover_every_event() -> None:
    assert set(SERVICE_EVENTS) == OPS_EVENTS
    assert {event for _, event, _, _ in RULE_CASES} == OPS_EVENTS


def test_module_defaults_to_the_prefix(service: OpsLog, maint: OpsLog) -> None:
    assert service.record(**fields_of("service", "answered")).module == "service"
    assert maint.record(**fields_of("maint", "answered")).module == "maint"


# Each case: module, event, overrides, the rule named in the error.
RULE_CASES: list[tuple[Module, str, dict[str, Any], str]] = [  # Any: record fields
    ("service", "answered", {"flow": "diagnosis"}, "module_flow"),
    ("maint", "answered", {"flow": "order_status"}, "module_flow"),
    ("service", "answered", {"flow": None}, "module_flow"),
    ("service", "drafted", {"flow": None}, "module_flow"),
    ("service", "error", {"flow": None}, "module_flow"),
    ("service", "answered", {"flow": "customer_reply"}, "event_flow"),
    ("maint", "answered", {"flow": "work_request"}, "event_flow"),
    ("service", "drafted", {"flow": "order_status"}, "event_flow"),
    ("maint", "abandoned", {"flow": "diagnosis"}, "event_flow"),
    ("maint", "answered", {"account_ref": "CLI-003"}, "account_ref"),
    ("maint", "refused", {"account_ref": "CLI-001"}, "account_ref"),
    ("service", "answered", {"approver": "operator"}, "approver"),
    ("service", "drafted", {"approver": "operator"}, "approver"),
    ("service", "approved", {"approver": None}, "approver"),
    ("service", "edit_submitted", {"approver": None}, "approver"),
    ("maint", "rejected", {"approver": None}, "approver"),
    ("service", "drafted", {"draft_id": None}, "draft_id.missing"),
    ("service", "approved", {"draft_id": None}, "draft_id.missing"),
    ("maint", "edit_submitted", {"draft_id": None}, "draft_id.missing"),
    ("maint", "re_presented", {"draft_id": None}, "draft_id.missing"),
    ("service", "rejected", {"draft_id": None}, "draft_id.missing"),
    ("service", "abandoned", {"draft_id": None}, "draft_id.missing"),
    ("service", "answered", {"draft_id": DRAFT}, "draft_id.not_empty"),
    ("service", "refused", {"reason": None}, "reason.refused"),
    ("service", "refused", {"reason": "output_pi"}, "reason.refused"),
    ("maint", "refused", {"reason": "approval_rejected"}, "reason.refused"),
    ("service", "refused", {"reason": "connector_unavailable"}, "reason.refused"),
    ("service", "withheld", {"reason": None}, "reason.withheld"),
    ("service", "withheld", {"reason": "restricted_info"}, "reason.withheld"),
    ("service", "rejected", {"reason": None}, "reason.rejected"),
    ("maint", "rejected", {"reason": "invalid_args"}, "reason.rejected"),
    ("service", "error", {"reason": "output_pi"}, "reason.error"),
    ("service", "answered", {"reason": "restricted_info"}, "reason.not_empty"),
    ("service", "drafted", {"reason": "ungrounded"}, "reason.not_empty"),
    ("maint", "approved", {"reason": "approval_rejected"}, "reason.not_empty"),
    ("service", "edit_submitted", {"reason": "invalid_args"}, "reason.not_empty"),
    ("service", "re_presented", {"reason": "output_pi"}, "reason.not_empty"),
    ("maint", "abandoned", {"reason": "approval_rejected"}, "reason.not_empty"),
    ("service", "refused", {"reason": "audit_unavailable"}, "reason.audit_unavailable"),
    ("service", "error", {"reason": "audit_unavailable"}, "reason.audit_unavailable"),
    ("maint", "withheld", {"reason": "audit_unavailable"}, "reason.audit_unavailable"),
    ("service", "drafted", {"action_tool": None}, "action.missing"),
    ("service", "drafted", {"action_args_sha256": None}, "action.missing"),
    ("maint", "approved", {"action_args_sha256": None}, "action.missing"),
    ("maint", "re_presented", {"action_tool": None}, "action.missing"),
    ("service", "edit_submitted", {"action_args_sha256": SHA}, "action.digest_on_edit"),
    ("service", "drafted", {"edit_round": 1}, "edit_round"),
    ("service", "approved", {"model": "gemma4:e4b"}, "model"),
    ("maint", "edit_submitted", {"model": "gemma4:e4b"}, "model"),
    ("service", "rejected", {"model": "gemma4:e4b"}, "model"),
]


@pytest.mark.parametrize(("module", "event", "over", "rule"), RULE_CASES)
def test_cross_field_rules_name_the_rule(
    directory: Path, module: Module, event: str, over: dict[str, Any], rule: str
) -> None:
    log = ops_log(directory, module)
    with pytest.raises(AccessLogError) as caught:
        log.record(**fields_of(module, event, **over))
    message = str(caught.value)
    assert message.endswith(f"<record>: ops_log.{rule})"), f"{event}: {rule}"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_reasons_of_a_refusal_are_the_early_codes() -> None:
    late = {"ungrounded", "output_pi", "invalid_args", "invalid_decision", "approval_rejected"}
    system = {"connector_unavailable", "audit_unavailable"}
    for code in sorted(OPS_REFUSAL_CODES):
        values = fields_of("service", "refused", reason=code)
        values |= {"event_id": "0" * 32, "ts": "2026-11-01T14:00Z", "module": "service"}
        rec = OpsAccessLogRecord.model_construct(**values)
        rules = list(broken_rules(rec))
        assert (rules == []) == (code not in late | system), code


def test_refused_records_need_no_flow_and_errors_may_carry_none() -> None:
    log = ops_log(Path("unused"), "service")
    assert log.record(**fields_of("service", "refused")).flow is None
    assert log.record(**fields_of("service", "error", reason=None)).reason is None
    assert log.record(**fields_of("service", "withheld", reason="cross_account")).reason
    assert log.record(**fields_of("service", "refused", reason="cross_account")).reason
    rejected = log.record(**fields_of("service", "rejected", reason="invalid_decision"))
    assert rejected.reason == "invalid_decision"


# --------------------------------------------------------------------------- no free text
# An order id written with full-width digits: the patterns accept ASCII digits only.
FULL_WIDTH_ORDER = "CMD-" + "".join(chr(0xFF10 + int(d)) for d in "2026") + "-0412"
FREE_TEXT: list[dict[str, Any]] = [  # Any: record fields
    {"model": "un modèle avec du texte libre"},
    {"quarantine_flags": ("texte retiré de la remarque",)},
    {"quarantine_flags": ("Injection",)},
    {"tool_calls": ("erp.get_order avec la commande",)},
    {"tool_calls": ("lire la commande",)},
    {"action_tool": "écrire la réponse"},
    {"entity_refs": ("CMD-2026-0412", "CT-006")},
    {"entity_refs": ("commande du client",)},
    {"entity_refs": (FULL_WIDTH_ORDER,)},
    {"entity_refs": ("CMD-2026-0412\n",)},
    {"account_ref": "compte du client"},
    {"draft_id": "brouillon-numero-un-de-la-demande"},
    {"action_args_sha256": "texte"},
    {"reason": "parce que le client a demandé"},
    {"flow": "vente"},
    {"event": "sent"},
    {"channel": "web"},
    {"request": "Où en est la commande CMD-2026-0412 ?"},
    {"draft": "Bonjour, votre commande est expédiée."},
    {"request_sha256": "ab" * 32},
    {"request_length": 42},
    {"thread_id": "abc"},
    {"pi_count": PI_COUNT_CAP + 1},
    {"pi_count": -1},
    {"pi_types": ("passeport",)},
    {"edit_round": 3},
    {"latency_ms": -1},
    {"executed_external_send": True},
    {"actor": "operateur-42"},
    {"v": 2},
]


@pytest.mark.parametrize("extra", FREE_TEXT)
def test_free_text_and_unknown_fields_are_rejected(service: OpsLog, extra: dict[str, Any]) -> None:
    with pytest.raises(AccessLogError) as caught:
        service.record(**fields_of("service", "answered", **extra))
    message = str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    for key, value in extra.items():
        assert key in message, f"{key}: the error names the field"
        if isinstance(value, str) and len(value) > 3:
            assert value not in message, f"{key}: value echoed"
        if isinstance(value, tuple):
            assert not any(v in message for v in value if len(v) > 3), f"{key}: value echoed"


def test_entity_refs_are_capped() -> None:
    refs = tuple(f"OT-2026-{n:04d}" for n in range(1, MAX_ENTITY_REFS + 2))
    log = ops_log(Path("unused"), "maint")
    assert len(log.record(**fields_of("maint", "answered", entity_refs=refs[:-1])).entity_refs)
    with pytest.raises(AccessLogError, match="entity_refs: too_long"):
        log.record(**fields_of("maint", "answered", entity_refs=refs))


def test_pydantic_errors_never_echo_the_input() -> None:
    sentence = "un modèle avec du texte libre"
    values = {"event_id": "0" * 32, "ts": "2026-11-01T14:00Z", **fields_of("service", "answered")}
    with pytest.raises(ValidationError) as caught:
        OpsAccessLogRecord.model_validate({**values, "module": "service", "model": sentence})
    assert sentence not in str(caught.value)


def test_records_are_frozen(service: OpsLog) -> None:
    rec = service.record(**fields_of("service", "answered"))
    with pytest.raises(ValidationError):
        rec.event = "refused"  # type: ignore[misc]


def test_the_log_sets_id_and_time_itself(service: OpsLog) -> None:
    with pytest.raises(AccessLogError):
        service.record(**fields_of("service", "answered"), ts="2026-11-01T14:00Z")
    with pytest.raises(AccessLogError):
        service.record(**fields_of("service", "answered"), event_id="0" * 32)


@pytest.mark.parametrize(
    "ts",
    [
        "2026-11-01T14:37Z",
        "2026-11-01T14:00:00Z",
        "2026-11-01T" + chr(0x0661) + chr(0x0664) + ":00Z",  # Arabic-Indic hour digits
    ],
)
def test_minute_or_free_timestamps_are_rejected(ts: str) -> None:
    values = {"event_id": "0" * 32, "module": "service", **fields_of("service", "answered")}
    with pytest.raises(ValidationError):
        OpsAccessLogRecord.model_validate({**values, "ts": ts})


# --------------------------------------------------------------------------- prefixes
def test_prefixes() -> None:
    assert ACCESS_LOG_PREFIXES == ("hr", "service", "maint")


def test_hr_defaults_are_unchanged(directory: Path) -> None:
    log = AccessLog(directory, clock=Clock(NOW))
    assert log.prefix == "hr" and log.record_type is AccessLogRecord
    assert log.path_for(NOW.date()).name == "hr-2026-11-01.jsonl"
    rec = log.record(channel="cli", decision="answered", latency_ms=3)
    assert isinstance(rec, AccessLogRecord) and rec.module == "hr"
    log.append(rec)
    assert [p.name for p in directory.iterdir()] == ["hr-2026-11-01.jsonl"]


@pytest.mark.parametrize("module", ["service", "maint"])
def test_ops_records_go_to_their_prefix_files(directory: Path, module: Module) -> None:
    clock = Clock(NOW)
    log = ops_log(directory, module, clock)
    log.append(log.record(**fields_of(module, "drafted")))
    clock.now = dt.datetime(2026, 11, 1, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    log.append(log.record(**fields_of(module, "approved")))
    names = sorted(p.name for p in directory.iterdir())
    assert names == [f"{module}-2026-11-01.jsonl", f"{module}-2026-11-02.jsonl"]
    first = log.read(dt.date(2026, 11, 1))
    assert [r.event for r in first] == ["drafted"]
    assert all(isinstance(r, OpsAccessLogRecord) for r in first)
    assert log.read(dt.date(2026, 11, 2))[0].ts == "2026-11-02T04:00Z"
    assert log.read(dt.date(2026, 10, 1)) == []
    line = log.path_for(dt.date(2026, 11, 1)).read_text(encoding="utf-8").splitlines()[0]
    assert tuple(json.loads(line)) == FIELDS


@pytest.mark.parametrize(
    ("prefix", "record_type"),
    [
        ("service", AccessLogRecord),
        ("maint", AccessLogRecord),
        ("hr", OpsAccessLogRecord),
        ("ops", OpsAccessLogRecord),
        ("service", dict),
    ],
)
def test_an_incompatible_prefix_and_record_type_fail_at_construction(
    directory: Path, prefix: str, record_type: type
) -> None:
    with pytest.raises(AccessLogError, match="prefix and record type"):
        AccessLog(directory, prefix=prefix, record_type=record_type)  # type: ignore[call-overload]


def test_ops_retention_is_90_days_at_most(directory: Path) -> None:
    with pytest.raises(AccessLogError, match="retention"):
        AccessLog(directory, prefix="service", record_type=OpsAccessLogRecord, retention_days=91)


def test_a_record_of_another_module_is_refused(service: OpsLog, maint: OpsLog) -> None:
    with pytest.raises(AccessLogError, match="module does not match"):
        service.record(**fields_of("maint", "answered", module="maint"))
    other = maint.record(**fields_of("maint", "answered"))
    with pytest.raises(AccessLogError, match="module does not match"):
        service.append(other)
    assert not service.directory.exists()


def test_an_hr_record_never_enters_an_ops_log(directory: Path, service: OpsLog) -> None:
    hr = AccessLog(directory, clock=Clock(NOW)).record(
        channel="cli", decision="answered", latency_ms=3
    )
    with pytest.raises(AccessLogError, match="invalid access log record"):
        service.append(hr)  # type: ignore[arg-type]


def test_read_refuses_a_line_of_another_module(service: OpsLog, maint: OpsLog) -> None:
    rec = maint.record(**fields_of("maint", "answered"))
    path = service.path_for(NOW.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rec.model_dump_json() + "\n\n", encoding="utf-8")
    with pytest.raises(AccessLogError, match="another module") as caught:
        service.read(NOW.date())
    assert f"{path}:1" in str(caught.value)


def test_read_rejects_a_tampered_line_without_echoing_it(service: OpsLog) -> None:
    path = day_file(service.directory, "service-2026-11-01.jsonl")
    path.write_text('{"v": 1, "request": "texte de la demande"}\n', encoding="utf-8")
    with pytest.raises(AccessLogError) as caught:
        service.read(NOW.date())
    assert "texte de la demande" not in str(caught.value) and f"{path}:1" in str(caught.value)


def test_append_rejects_a_record_built_without_validation(service: OpsLog) -> None:
    forged = OpsAccessLogRecord.model_construct(
        **{**fields_of("service", "answered"), "module": "service", "event_id": "0" * 32},
        ts="2026-11-01T14:00Z",
        approver="operator",
    )
    with pytest.raises(AccessLogError, match=r"ops_log\.approver"):
        service.append(forged)
    assert not service.directory.exists()


# --------------------------------------------------------------------------- purge
OLD, RECENT = NOW.date() - dt.timedelta(days=120), NOW.date() - dt.timedelta(days=3)


def files_of_every_prefix(directory: Path) -> dict[str, tuple[Path, Path]]:
    return {
        prefix: (
            day_file(directory, f"{prefix}-{OLD.isoformat()}.jsonl"),
            day_file(directory, f"{prefix}-{RECENT.isoformat()}.jsonl"),
        )
        for prefix in ACCESS_LOG_PREFIXES
    }


@pytest.mark.parametrize("prefix", ACCESS_LOG_PREFIXES)
def test_each_instance_purges_only_its_own_prefix(directory: Path, prefix: str) -> None:
    files = files_of_every_prefix(directory)
    log: AccessLog[Any]  # Any: the record type depends on the prefix
    if prefix == "hr":
        log = AccessLog(directory, clock=Clock(NOW))
    else:
        log = ops_log(directory, "service" if prefix == "service" else "maint")
    assert log.purge(dry_run=True) == [files[prefix][0]]
    assert log.purge() == [files[prefix][0]]
    for other, (old, recent) in files.items():
        assert recent.exists()
        assert old.exists() == (other != prefix), f"{prefix} purged {other}"


def test_append_purges_only_its_own_prefix(directory: Path, service: OpsLog) -> None:
    files = files_of_every_prefix(directory)
    service.append(service.record(**fields_of("service", "answered")))
    assert not files["service"][0].exists()
    assert files["hr"][0].exists() and files["maint"][0].exists()


def test_purge_skips_odd_names(directory: Path, maint: OpsLog) -> None:
    odd = [
        day_file(directory, "maint-2026-13-45.jsonl"),
        day_file(directory, "maintenance-2020-01-01.jsonl"),
        day_file(directory, "maint-2020-01-01.json"),
        day_file(directory, "notes.txt"),
    ]
    assert maint.purge() == []
    assert all(p.exists() for p in odd)


def test_pi_types_are_types_only(service: OpsLog) -> None:
    rec = service.record(
        **fields_of("service", "refused", pi_types=(PIType.COURRIEL,), pi_count=PI_COUNT_CAP)
    )
    assert rec.pi_types == (PIType.COURRIEL,) and rec.pi_count == PI_COUNT_CAP


# --------------------------------------------------------------------------- review additions
@pytest.mark.parametrize("module", ["service", "maint"])
@pytest.mark.parametrize("event", sorted(OPS_EVENTS - {"refused"}))
def test_only_a_refusal_may_have_no_flow(directory: Path, module: Module, event: str) -> None:
    log = ops_log(directory, module)
    with pytest.raises(AccessLogError) as caught:
        log.record(**fields_of(module, event, flow=None))
    assert "<record>: ops_log.module_flow" in str(caught.value), event


ARABIC_INDIC = "".join(chr(0x0660 + n % 10) for n in range(64))
FULL_WIDTH = "".join(chr(0xFF10 + n % 10) for n in range(64))


@pytest.mark.parametrize(
    ("field", "value", "event"),
    [
        ("draft_id", ARABIC_INDIC[:32], "drafted"),
        ("draft_id", FULL_WIDTH[:32], "drafted"),
        ("action_args_sha256", FULL_WIDTH, "drafted"),
        ("action_args_sha256", ARABIC_INDIC, "drafted"),
        ("account_ref", "CLI-" + FULL_WIDTH[:3], "answered"),
        ("account_ref", "CLI-" + ARABIC_INDIC[:3], "answered"),
    ],
)
def test_patterns_accept_ascii_digits_only(
    service: OpsLog, field: str, value: str, event: str
) -> None:
    with pytest.raises(AccessLogError) as caught:
        service.record(**fields_of("service", event, **{field: value}))
    message = str(caught.value)
    assert f"{field}: string_pattern_mismatch" in message, field
    assert value not in message


def test_hr_error_text_is_unchanged_for_a_line_without_location(directory: Path) -> None:
    """A corrupt HR line keeps its module 2 error text (an empty location)."""
    log = AccessLog(directory, clock=Clock(NOW))
    path = day_file(directory, "hr-2026-11-01.jsonl")
    for line, kind in (("not json", "json_invalid"), ("[1]", "model_type")):
        path.write_text(line + "\n", encoding="utf-8")
        with pytest.raises(AccessLogError) as caught:
            log.read(NOW.date())
        assert str(caught.value).endswith(f":1: invalid access log record (: {kind})"), kind


def test_ops_error_text_names_a_line_without_location(service: OpsLog, directory: Path) -> None:
    path = day_file(directory, "service-2026-11-01.jsonl")
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(AccessLogError) as caught:
        service.read(NOW.date())
    assert str(caught.value).endswith(":1: invalid access log record (<record>: json_invalid)")


def test_cli_access_purge_purges_the_three_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "access"
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_DIR", str(directory))
    monkeypatch.delenv("ALVERNO_ACCESS_LOG_RETENTION_DAYS", raising=False)
    today = dt.datetime.now(dt.UTC).date()
    planted: dict[str, tuple[Path, Path]] = {}
    for prefix in ACCESS_LOG_PREFIXES:
        old = day_file(directory, f"{prefix}-{(today - dt.timedelta(days=120)).isoformat()}.jsonl")
        recent = day_file(directory, f"{prefix}-{(today - dt.timedelta(days=3)).isoformat()}.jsonl")
        planted[prefix] = (old, recent)
    runner = CliRunner()
    dry = runner.invoke(app, ["access", "purge", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "Would remove 1 day file(s) older than 90 days in " in dry.output
    assert "Would remove 1 service day file(s) older than 90 days in " in dry.output
    assert "Would remove 1 maint day file(s) older than 90 days in " in dry.output
    assert all(old.exists() and recent.exists() for old, recent in planted.values())
    done = runner.invoke(app, ["access", "purge"])
    assert done.exit_code == 0, done.output
    for kind in ("", "service ", "maint "):
        assert f"Removed 1 {kind}day file(s) older than 90 days in " in done.output, kind
    for name, (old, recent) in planted.items():
        assert not old.exists() and recent.exists(), name
    again = runner.invoke(app, ["access", "purge"])
    assert again.exit_code == 0 and again.output.count("Removed 0 ") == 3
