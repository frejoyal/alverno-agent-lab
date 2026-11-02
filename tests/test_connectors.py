"""Module 3 connectors (I3 spec decisions 1 to 7, ADR 0012).

Typed identifiers and closed codes, the purpose-bound projections, the quarantine hook,
the simulated ERP and GMAO over ``data/eval/ops/`` and the module 3 refusal codes. The
fixtures hold canaries (prices, costs, margins, internal notes, phones, addresses,
technician identities, safety notes): assertion messages cite record ids, field names
and rule names, never a value. Only the fixtures and synthetic strings are read here,
never a holdout.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import datetime as dt
import hashlib
import inspect
import json
import re
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import BaseModel, TypeAdapter, ValidationError

from alverno_lab.connectors import (
    CATALOGUE_CODES,
    CREATE_WORK_REQUEST_TOOL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    FAILURE_CATEGORIES,
    FAILURE_CODES,
    INDETERMINATE_CODE,
    MAX_WORK_ORDER_LIMIT,
    ORDER_STATUSES,
    RECORD_REPLY_TOOL,
    SYMPTOM_CODES,
    WORK_ORDER_KINDS,
    AccountId,
    AssetId,
    AssetView,
    Civility,
    ConnectorError,
    ContactRef,
    ContactView,
    ErpConnector,
    FailureCategory,
    FailureCode,
    FailureCodeView,
    GmaoConnector,
    OrderId,
    OrderStatus,
    OrderView,
    QuarantinedText,
    SideEffectRecord,
    SimErp,
    SimGmao,
    SymptomCode,
    WorkOrderId,
    WorkOrderKind,
    WorkOrderView,
    WorkRequest,
    WriteReceipt,
    canonical_json,
    project_contact,
    project_order,
    project_work_order,
)
from alverno_lab.connectors.sim import (
    AccountRecord,
    ContactRecord,
    ErpFixture,
    GmaoFixture,
    OrderRecord,
    WorkOrderRecord,
    fixture_strings,
)
from alverno_lab.connectors.types import ActionId, ReplyPrepared, Sha256Hex
from alverno_lab.privacy import PrivacyContext, detect_pi
from alverno_lab.privacy.detect import Mode
from alverno_lab.privacy.ops_types import OPS_REFUSAL_CODES, Flow, OpsRefusalCode

from .conftest import EVAL_DATA, REPO_ROOT

OPS = EVAL_DATA / "ops"
ERP_PATH = OPS / "erp.yaml"
GMAO_PATH = OPS / "gmao.yaml"
EFVP = REPO_ROOT / "docs" / "efvp" / "ops-module.md"
SRC = REPO_ROOT / "src" / "alverno_lab"
AS_OF = dt.date(2026, 10, 30)
PLACEHOLDER = "texte mis en quarantaine"
ACTION_ID = "0123456789abcdef0123456789abcdef"
DRAFT_SHA = hashlib.sha256(b"brouillon approuve").hexdigest()

# YAML documents are untyped trees: ``Any`` is the honest type of a parsed fixture.
Raw = dict[str, Any]


@cache
def _erp_text() -> str:
    return ERP_PATH.read_text(encoding="utf-8")


@cache
def _gmao_text() -> str:
    return GMAO_PATH.read_text(encoding="utf-8")


def erp_raw() -> Raw:
    """A fresh copy of the parsed ERP fixture (callers may mutate it)."""
    data: Raw = yaml.safe_load(_erp_text())
    return data


def gmao_raw() -> Raw:
    """A fresh copy of the parsed GMAO fixture (callers may mutate it)."""
    data: Raw = yaml.safe_load(_gmao_text())
    return data


@cache
def erp_fixture() -> ErpFixture:
    return ErpFixture.model_validate(erp_raw())


@cache
def gmao_fixture() -> GmaoFixture:
    return GmaoFixture.model_validate(gmao_raw())


@cache
def erp_records() -> Raw:
    return erp_raw()


@cache
def gmao_records() -> Raw:
    return gmao_raw()


class RecordingQuarantine:
    """Stub quarantine: records every text it receives.

    By default it returns a fixed placeholder, so that a view's JSON shows only what the
    projection itself copied; ``keep=True`` returns the text unchanged.
    """

    def __init__(self, *, keep: bool = False) -> None:
        self.calls: list[str] = []
        self.keep = keep

    def __call__(self, text: str) -> QuarantinedText:
        self.calls.append(text)
        return QuarantinedText(text=text if self.keep else PLACEHOLDER, flags=("stub",))


@dataclasses.dataclass(frozen=True)
class StubToken:
    """Satisfies the ``WriteToken`` protocol; the simulated connectors never verify it."""

    action_id: str = ACTION_ID
    tool: str = RECORD_REPLY_TOOL
    args_sha256: str = "0" * 64


def make_erp(quarantine: Callable[[str], QuarantinedText] | None = None) -> SimErp:
    return SimErp(erp_fixture(), quarantine=quarantine or RecordingQuarantine())


def make_gmao(quarantine: Callable[[str], QuarantinedText] | None = None) -> SimGmao:
    return SimGmao(gmao_fixture(), quarantine=quarantine or RecordingQuarantine())


def contains_word(text: str, value: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text) is not None


def assert_unchained(error: BaseException) -> None:
    """No cause and no context at all: neither a traceback nor code walking the chain
    reaches a pydantic or YAML exception (raised outside any except block)."""
    assert error.__cause__ is None
    assert error.__context__ is None


# --------------------------------------------------------------------------- codes
def test_literals_equal_the_fixture_code_sets() -> None:
    erp, gmao = erp_records(), gmao_records()
    catalogue = {f["code"] for f in gmao["failure_codes"]}
    assert set(get_args(FailureCode)) == FAILURE_CODES == catalogue | {INDETERMINATE_CODE}
    assert len(catalogue) == 16 and catalogue == CATALOGUE_CODES
    assert gmao["indeterminate_code"] == INDETERMINATE_CODE
    assert set(get_args(SymptomCode)) == SYMPTOM_CODES == {s["code"] for s in gmao["symptom_codes"]}
    assert len(SYMPTOM_CODES) == 8
    assert set(get_args(OrderStatus)) == ORDER_STATUSES == {o["status"] for o in erp["orders"]}
    assert len(ORDER_STATUSES) == 7
    assert (
        set(get_args(WorkOrderKind)) == WORK_ORDER_KINDS == {w["kind"] for w in gmao["work_orders"]}
    )
    categories = {f["category"] for f in gmao["failure_codes"]}
    assert set(get_args(FailureCategory)) == FAILURE_CATEGORIES == categories
    assert set(get_args(Civility)) == {c["civility"] for c in erp["contacts"]}


def efvp_refusal_codes() -> list[str]:
    """Codes of the « Politique de refus » table of the module 3 assessment, in order."""
    text = EFVP.read_text(encoding="utf-8")
    section = text.split("### Politique de refus", 1)[1].split("\n### ", 1)[0]
    return re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE)


def test_ops_refusal_codes_follow_the_efvp_table_in_order() -> None:
    codes = efvp_refusal_codes()
    assert len(codes) == 24
    assert list(get_args(OpsRefusalCode)) == codes
    assert frozenset(codes) == OPS_REFUSAL_CODES


def test_flows_are_the_four_purposes() -> None:
    assert get_args(Flow) == ("order_status", "customer_reply", "diagnosis", "work_request")


# SHA-256 of privacy/__init__.py (LF line endings) as committed at the end of
# module 2: the HR package API stays byte-identical in module 3 (I3 spec decisions 1
# and 12). A deliberate change of that API updates this pin in the same commit.
PRIVACY_INIT_SHA256 = "305fbf7d8f97772eeb18baa80a5c089b0618fe2d17c5736a8929ccca6184bf56"


def test_ops_types_stays_outside_the_hr_package_api() -> None:
    path = SRC / "privacy" / "ops_types.py"
    imported = _imports(path)
    assert not [m for m in imported if m.startswith(("alverno_lab", "."))], imported
    init = (SRC / "privacy" / "__init__.py").read_bytes().replace(b"\r\n", b"\n")
    assert b"ops_types" not in init
    assert hashlib.sha256(init).hexdigest() == PRIVACY_INIT_SHA256, "privacy/__init__.py changed"


# --------------------------------------------------------------------------- identifiers
@pytest.mark.parametrize(
    ("kind", "good", "bad"),
    [
        (
            OrderId,
            "CMD-2026-0401",
            ["CMD-2026-04011", "cmd-2026-0401", "2026-0412", "CMD-2026-0401\n"],
        ),
        # Arabic-Indic digits: Unicode \d would accept them, [0-9] does not.
        (AccountId, "CLI-001", ["CLI-01", "CLI-0001", "cli-001", "CLI-00" + chr(0x661)]),
        (AssetId, "EQ-PLI-01", ["EQ-PL-01", "EQ-pli-01", "EQ-PLI-1", "EQ-PLI-01 "]),
        (
            WorkOrderId,
            "OT-2026-0042",
            ["OT-26-0042", "OT-2026-" + "".join(map(chr, (0x660, 0x660, 0x664, 0x662)))],
        ),
        (ContactRef, "CT-014", ["CT-14", "CT-0140", "ct-014"]),
        (ActionId, "0" * 32, ["0" * 31, "0" * 33, "A" * 32, "0" * 32 + "\n"]),
        (Sha256Hex, "a" * 64, ["a" * 63, "a" * 65, "A" * 64, "g" * 64]),
    ],
    ids=["order", "account", "asset", "work_order", "contact", "action", "sha256"],
)
def test_identifier_patterns_are_anchored_and_ascii(kind: Any, good: str, bad: list[str]) -> None:
    adapter: TypeAdapter[str] = TypeAdapter(kind)
    assert adapter.validate_python(good) == good
    # Pydantic matches a pattern with search semantics: only « ^ » refuses leading junk,
    # only « $ » refuses trailing junk.
    for value in [*bad, f"x{good}", f" {good}", f"{good}x", f"{good}0"]:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


# --------------------------------------------------------------------------- views: fields
VIEW_FIELDS: dict[type[BaseModel], set[str]] = {
    OrderView: {
        "order_id",
        "account_id",
        "part_ref",
        "qty_ordered",
        "qty_shipped",
        "status",
        "promised_date",
        "revised_date",
        "carrier_code",
        "tracking_ref",
        "delay_reason_code",
        "customer_visible_note",
    },
    ContactView: {
        "contact_ref",
        "account_id",
        "account_label",
        "natural_person",
        "function",
        "civility",
        "surname",
        "given_name",
        "business_email",
    },
    AssetView: {"asset_id", "name", "kind", "line"},
    WorkOrderView: {
        "wo_id",
        "asset_id",
        "opened_on",
        "kind",
        "symptom_code",
        "failure_code",
        "action_code",
        "remark",
    },
    FailureCodeView: {"code", "category", "label"},
    WriteReceipt: {"action_id", "tool", "args_sha256"},
    SideEffectRecord: {"action_id", "tool", "args_sha256"},
    # Not a view, but what a view holds for tool text: as strict, and frozen after quarantine.
    QuarantinedText: {"text", "flags"},
}
ORDER_EXCLUDED = {"price_unit", "unit_cost", "margin_pct", "internal_note", "contact_ref"}
CONTACT_EXCLUDED = {"cell_phone", "home_address"}
WORK_ORDER_EXCLUDED = {
    "technician_name",
    "matricule",
    "initials",
    "login",
    "shift",
    "opened_time",
    "downtime_min",
    "safety",
}


@pytest.mark.parametrize("view", list(VIEW_FIELDS), ids=lambda v: v.__name__)
def test_views_hold_exactly_the_adr_fields(view: type[BaseModel]) -> None:
    assert set(view.model_fields) == VIEW_FIELDS[view]
    assert view.model_config.get("extra") == "forbid"
    assert view.model_config.get("frozen") is True


def test_quarantined_text_is_frozen_and_hidden_from_repr() -> None:
    assert QuarantinedText.model_fields["text"].repr is False
    quarantined = QuarantinedText(text="texte synthetique du test", flags=("stub",))
    assert "texte synthetique du test" not in repr(quarantined)
    with pytest.raises(ValidationError):
        quarantined.__setattr__("text", "autre texte")
    with pytest.raises(ValidationError):
        QuarantinedText.model_validate({"text": "x", "flags": [], "extra": 1})


def test_views_drop_every_excluded_field_name() -> None:
    assert not ORDER_EXCLUDED & set(OrderView.model_fields)
    assert "label" not in OrderView.model_fields
    assert not CONTACT_EXCLUDED & set(ContactView.model_fields)
    assert not WORK_ORDER_EXCLUDED & set(WorkOrderView.model_fields)
    assert set(OrderRecord.model_fields) >= ORDER_EXCLUDED
    assert set(ContactRecord.model_fields) >= CONTACT_EXCLUDED
    assert set(WorkOrderRecord.model_fields) >= WORK_ORDER_EXCLUDED


# --------------------------------------------------------------------------- views: values
def _leaks(dump: str, values: dict[str, object]) -> list[str]:
    return [field for field, value in values.items() if value is not None and str(value) in dump]


def test_order_views_leak_no_excluded_value() -> None:
    erp = erp_records()
    accounts = {a["account_id"]: a for a in erp["accounts"]}
    contacts = {c["contact_ref"]: c for c in erp["contacts"]}
    sim = make_erp()
    for order in erp["orders"]:
        view = sim.get_order(order["order_id"])
        assert view is not None
        dump = view.model_dump_json()
        contact = contacts[order["contact_ref"]]
        values: dict[str, object] = {
            **{f: order[f] for f in ("price_unit", "unit_cost", "margin_pct", "internal_note")},
            "contact_ref": order["contact_ref"],
            "account.label": accounts[order["account_id"]]["label"],
            "account.generic_mailbox": accounts[order["account_id"]]["generic_mailbox"],
            **{
                f"contact.{f}": contact[f]
                for f in ("surname", "business_email", "cell_phone", "home_address", "function")
            },
        }
        leaks = _leaks(dump, values)
        assert not leaks, f"{order['order_id']}: projection.leak {leaks}"
    assert len(erp["orders"]) == 40


def test_contact_views_leak_no_phone_or_address() -> None:
    erp = erp_records()
    raw_accounts = {a["account_id"]: a for a in erp["accounts"]}
    accounts = {k: AccountRecord.model_validate(v) for k, v in raw_accounts.items()}
    for raw in erp["contacts"]:
        record = ContactRecord.model_validate(raw)
        view = project_contact(record, accounts[record.account_id])
        values: dict[str, object] = {
            "cell_phone": raw["cell_phone"],
            "home_address": raw["home_address"],
            "account.generic_mailbox": raw_accounts[record.account_id]["generic_mailbox"],
        }
        leaks = _leaks(view.model_dump_json(), values)
        assert not leaks, f"{raw['contact_ref']}: projection.leak {leaks}"
        assert view.natural_person is accounts[record.account_id].natural_person
        assert view.account_label == accounts[record.account_id].label
    assert len(erp["contacts"]) == 14


def test_work_order_views_leak_no_technician_shift_time_or_downtime() -> None:
    gmao = gmao_records()
    quarantine = RecordingQuarantine()
    for raw in gmao["work_orders"]:
        view = project_work_order(WorkOrderRecord.model_validate(raw), quarantine)
        dump = view.model_dump_json()
        leaks = _leaks(
            dump, {f: raw[f] for f in ("technician_name", "matricule", "login", "opened_time")}
        )
        leaks += [f for f in ("initials", "shift") if contains_word(dump, raw[f])]
        if raw["remark"] is not None:
            leaks += _leaks(dump, {"remark": raw["remark"]})
        assert not leaks, f"{raw['wo_id']}: projection.leak {leaks}"
        assert "downtime" not in dump and "shift" not in dump
        parsed = json.loads(dump)
        assert not [v for v in parsed.values() if isinstance(v, int)], raw["wo_id"]
    assert len(gmao["work_orders"]) == 120


def test_safety_remarks_are_dropped_before_quarantine() -> None:
    quarantine = RecordingQuarantine(keep=True)
    safety = [w for w in gmao_records()["work_orders"] if w["safety"]]
    assert len(safety) == 4
    for raw in safety:
        assert raw["remark"] is not None
        view = project_work_order(WorkOrderRecord.model_validate(raw), quarantine)
        assert view.remark is None, raw["wo_id"]
        assert raw["remark"] not in view.model_dump_json(), raw["wo_id"]
    assert quarantine.calls == []
    # The line itself stays in the history.
    sim = make_gmao(RecordingQuarantine(keep=True))
    for raw in safety:
        rows = sim.list_work_orders(
            raw["asset_id"], raw["symptom_code"], since_days=10_000, limit=12
        )
        hit = [v for v in rows if v.wo_id == raw["wo_id"]]
        assert all(v.remark is None for v in hit), raw["wo_id"]


def test_quarantine_receives_every_projected_free_text() -> None:
    erp, gmao = erp_records(), gmao_records()
    quarantine = RecordingQuarantine(keep=True)
    sim = make_erp(quarantine)
    for order in erp["orders"]:
        view = sim.get_order(order["order_id"])
        assert view is not None
        note = view.customer_visible_note
        if order["customer_visible_note"] is None:
            assert note is None, order["order_id"]
        else:
            assert note is not None and note.text == order["customer_visible_note"]
            assert note.flags == ("stub",)
    notes = [o["customer_visible_note"] for o in erp["orders"] if o["customer_visible_note"]]
    assert quarantine.calls == notes and len(notes) == 10

    quarantine = RecordingQuarantine()
    projected = [
        project_work_order(WorkOrderRecord.model_validate(w), quarantine)
        for w in gmao["work_orders"]
    ]
    remarks = [w["remark"] for w in gmao["work_orders"] if w["remark"] and not w["safety"]]
    assert quarantine.calls == remarks and len(remarks) == 12
    for raw, wo_view in zip(gmao["work_orders"], projected, strict=True):
        expected = raw["remark"] is not None and not raw["safety"]
        assert (wo_view.remark is not None) is expected, raw["wo_id"]
        assert wo_view.remark is None or wo_view.remark.text == PLACEHOLDER


def test_list_work_orders_quarantines_the_remarks_it_returns() -> None:
    quarantine = RecordingQuarantine()
    sim = make_gmao(quarantine)
    for asset in sim.asset_ids:
        for view in sim.list_work_orders(asset, None, since_days=10_000, limit=12):
            assert view.remark is None or view.remark.text == PLACEHOLDER
    assert quarantine.calls, "no remark reached the quarantine"
    assert set(quarantine.calls) <= {w["remark"] for w in gmao_records()["work_orders"]}


def test_a_quarantine_that_returns_no_quarantined_text_fails_closed() -> None:
    def broken(text: str) -> QuarantinedText:
        return text  # type: ignore[return-value]

    order = next(o for o in erp_records()["orders"] if o["customer_visible_note"])
    with pytest.raises(ConnectorError, match=r"^quarantine:invalid_result$"):
        project_order(OrderRecord.model_validate(order), broken)


def test_simulated_connectors_require_a_quarantine() -> None:
    with pytest.raises(ConnectorError, match=r"^SimErp:quarantine_required$"):
        SimErp(erp_fixture(), quarantine=None)  # type: ignore[arg-type]
    with pytest.raises(ConnectorError, match=r"^SimGmao:quarantine_required$"):
        SimGmao(gmao_fixture(), quarantine=None)  # type: ignore[arg-type]
    for method in (SimErp.__init__, SimErp.from_yaml, SimGmao.__init__, SimGmao.from_yaml):
        parameter = inspect.signature(method).parameters["quarantine"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test_quarantined_text_and_contact_values_stay_out_of_repr() -> None:
    text = QuarantinedText(text="note fictive du client", flags=("stub",))
    assert "note fictive" not in repr(text)
    sim = make_erp()
    order = erp_records()["orders"][0]
    view = sim.get_order_contact(order["order_id"])
    assert view is not None
    shown = repr(view)
    for field in ("surname", "given_name", "business_email", "account_label", "function"):
        assert str(getattr(view, field)) not in shown, field
    record = OrderRecord.model_validate(order)
    for field in ("price_unit", "unit_cost", "margin_pct"):
        assert str(getattr(record, field)) not in repr(record), field


def test_project_contact_refuses_a_contact_of_another_account() -> None:
    erp = erp_records()
    contact = ContactRecord.model_validate(erp["contacts"][0])
    other = next(a for a in erp["accounts"] if a["account_id"] != contact.account_id)
    with pytest.raises(ConnectorError, match=r"^project_contact:account_mismatch$"):
        project_contact(contact, AccountRecord.model_validate(other))


def test_get_order_contact_returns_the_contact_of_the_order() -> None:
    erp = erp_records()
    sim = make_erp()
    for order in erp["orders"]:
        view = sim.get_order_contact(order["order_id"])
        assert view is not None
        assert view.contact_ref == order["contact_ref"]
        assert view.account_id == order["account_id"]
    natural = [a["account_id"] for a in erp["accounts"] if a["natural_person"]]
    assert natural == ["CLI-008"]
    order = next(o for o in erp["orders"] if o["account_id"] == "CLI-008")
    view = sim.get_order_contact(order["order_id"])
    assert view is not None and view.natural_person


# --------------------------------------------------------------------------- reads
def test_loading_from_yaml_reads_as_of_and_every_record() -> None:
    erp = SimErp.from_yaml(ERP_PATH, quarantine=RecordingQuarantine())
    gmao = SimGmao.from_yaml(GMAO_PATH, quarantine=RecordingQuarantine())
    assert erp.as_of == gmao.as_of == AS_OF
    assert len(erp.account_ids) == 8 and len(erp.order_ids) == 40
    assert len(gmao.asset_ids) == 10 and len(gmao.work_order_ids) == 120
    markers = [m["marker"] for m in erp_records()["planted_markers"]]
    assert set(markers) <= fixture_strings(erp)
    assert erp_records()["orders"][0]["price_unit"] in fixture_strings(erp)
    assert {m["marker"] for m in gmao_records()["planted_markers"]} <= fixture_strings(gmao)
    # The connector objects themselves expose no fixture value.
    for sim in (erp, gmao):
        assert not hasattr(sim, "fixture_strings")
    assert erp.executed == [] and gmao.executed == []


def test_get_asset_and_failure_catalog() -> None:
    sim = make_gmao()
    for raw in gmao_records()["assets"]:
        assert sim.get_asset(raw["asset_id"]) == AssetView(**raw)
    catalogue = sim.failure_catalog()
    assert [c.code for c in catalogue] == [f["code"] for f in gmao_records()["failure_codes"]]
    assert {c.code for c in catalogue} == CATALOGUE_CODES
    assert INDETERMINATE_CODE not in {c.code for c in catalogue}


def test_unknown_or_ill_formed_ids_give_nothing() -> None:
    erp, gmao = make_erp(), make_gmao()
    for order_id in ("CMD-2026-0497", "commande 2026-0412", "", "CMD-2026-0401 "):
        assert erp.get_order(order_id) is None
        assert erp.get_order_contact(order_id) is None
    for asset_id in ("EQ-PLI-09", "EQ-pli-01", ""):
        assert gmao.get_asset(asset_id) is None
        assert gmao.list_work_orders(asset_id, None) == ()


def expected_history(asset: str, symptom: str | None, since_days: int, limit: int) -> list[str]:
    """History lines recomputed from the raw YAML (spec §2 and §4)."""
    start = AS_OF - dt.timedelta(days=since_days)
    rows = [
        w
        for w in gmao_records()["work_orders"]
        if w["asset_id"] == asset
        and (symptom is None or w["symptom_code"] == symptom)
        and dt.date.fromisoformat(w["opened_on"]) >= start
    ]
    rows.sort(key=lambda w: (w["opened_on"], w["wo_id"]), reverse=True)
    return [w["wo_id"] for w in rows[:limit]]


def _asset_symptom_cases() -> Iterator[tuple[str, str | None]]:
    for asset in gmao_records()["assets"]:
        for symptom in [None, *sorted(SYMPTOM_CODES)]:
            yield asset["asset_id"], symptom


def test_list_work_orders_matches_the_history_rule_on_every_asset_and_symptom() -> None:
    sim = make_gmao()
    assert DEFAULT_HISTORY_DAYS == 548 and DEFAULT_WORK_ORDER_LIMIT == 8
    checked = 0
    for asset, symptom in _asset_symptom_cases():
        rows = sim.list_work_orders(asset, symptom)  # type: ignore[arg-type]
        expected = expected_history(asset, symptom, 548, 8)
        assert [v.wo_id for v in rows] == expected, f"{asset}/{symptom}: history.mismatch"
        assert all(v.asset_id == asset for v in rows)
        assert symptom is None or all(v.symptom_code == symptom for v in rows)
        keys = [(v.opened_on, v.wo_id) for v in rows]
        assert keys == sorted(keys, reverse=True)
        checked += len(rows)
    assert checked > 0
    # The 6 older work orders sit outside the default window.
    old = [w["wo_id"] for w in gmao_records()["work_orders"] if w["opened_on"] < "2025-04-30"]
    assert len(old) == 6
    served = {
        v.wo_id
        for asset in sim.asset_ids
        for v in sim.list_work_orders(asset, None, limit=MAX_WORK_ORDER_LIMIT)
    }
    assert not served & set(old)


def _window_fixture() -> GmaoFixture:
    """The real catalogue with a handful of work orders on one asset, around the window."""
    data = gmao_raw()
    template = next(w for w in data["work_orders"] if w["asset_id"] == "EQ-PLI-01")
    dated = [
        ("OT-2025-0001", "2025-04-29", "SYM-FUITE"),  # day before the window: out
        ("OT-2025-0002", "2025-04-30", "SYM-FUITE"),  # first day of the window: in
        ("OT-2026-0001", "2026-01-10", "SYM-BRUIT"),  # same day as the next one
        ("OT-2026-0002", "2026-01-10", "SYM-FUITE"),
        ("OT-2026-0003", "2026-10-29", "SYM-FUITE"),  # as_of - 1
    ]
    data["work_orders"] = [
        {**template, "wo_id": wo_id, "opened_on": day, "symptom_code": symptom}
        for wo_id, day, symptom in dated
    ]
    data["planted_markers"] = []
    data["special_remarks"] = {"initials_or_login": [], "small_role_fault": []}
    return GmaoFixture.model_validate(data)


def test_list_work_orders_window_includes_its_first_day() -> None:
    sim = SimGmao(_window_fixture(), quarantine=RecordingQuarantine())
    ids = [v.wo_id for v in sim.list_work_orders("EQ-PLI-01", None)]
    # Newest first; a tie on the date is broken by wo_id descending.
    assert ids == ["OT-2026-0003", "OT-2026-0002", "OT-2026-0001", "OT-2025-0002"]
    assert [v.wo_id for v in sim.list_work_orders("EQ-PLI-01", "SYM-FUITE")] == [
        "OT-2026-0003",
        "OT-2026-0002",
        "OT-2025-0002",
    ]
    assert [v.wo_id for v in sim.list_work_orders("EQ-PLI-01", None, since_days=549)][-1] == (
        "OT-2025-0001"
    )
    assert [v.wo_id for v in sim.list_work_orders("EQ-PLI-01", None, since_days=1)] == [
        "OT-2026-0003"
    ]
    assert [v.wo_id for v in sim.list_work_orders("EQ-PLI-01", None, limit=2)] == [
        "OT-2026-0003",
        "OT-2026-0002",
    ]
    assert len(sim.list_work_orders("EQ-PLI-01", None, since_days=10**9, limit=12)) == 5
    assert sim.list_work_orders("EQ-PLI-01", "SYM-ARRET") == ()


@pytest.mark.parametrize(
    ("kwargs", "rule"),
    [
        ({"since_days": 0}, "since_days"),
        ({"since_days": -548}, "since_days"),
        ({"since_days": True}, "since_days"),
        ({"since_days": 548.0}, "since_days"),
        ({"since_days": "548"}, "since_days"),
        ({"limit": 0}, "limit"),
        ({"limit": MAX_WORK_ORDER_LIMIT + 1}, "limit"),
        ({"limit": True}, "limit"),
        ({"limit": "8"}, "limit"),
    ],
)
def test_list_work_orders_validates_its_bounds(kwargs: dict[str, Any], rule: str) -> None:
    sim = make_gmao()
    with pytest.raises(ConnectorError, match=rf"^gmao\.list_work_orders:{rule}$"):
        sim.list_work_orders("EQ-PLI-01", None, **kwargs)


def test_list_work_orders_refuses_a_symptom_outside_the_closed_list() -> None:
    with pytest.raises(ConnectorError, match=r"^gmao\.list_work_orders:symptom_code$"):
        make_gmao().list_work_orders("EQ-PLI-01", "SYM-BIDON")  # type: ignore[arg-type]
    assert len(make_gmao().list_work_orders("EQ-PLI-01", None, limit=12)) == 12


# --------------------------------------------------------------------------- writes
def test_side_effect_records_and_receipts_hold_no_text() -> None:
    for model in (SideEffectRecord, WriteReceipt):
        good = {"action_id": ACTION_ID, "tool": RECORD_REPLY_TOOL, "args_sha256": DRAFT_SHA}
        assert model.model_validate(good).model_dump() == good
        for field, text in (
            ("action_id", "brouillon de réponse au client"),
            ("tool", "courriel"),
            ("args_sha256", "Bonjour, votre commande est prête."),
        ):
            with pytest.raises(ValidationError):
                model.model_validate({**good, field: text})
        with pytest.raises(ValidationError):
            model.model_validate({**good, "draft": "texte"})


def _expected_digest(payload: dict[str, object]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_record_reply_prepared_records_a_digest_only() -> None:
    sim = make_erp()
    erp: ErpConnector = sim
    receipt = erp.record_reply_prepared("CMD-2026-0401", DRAFT_SHA, token=StubToken())
    digest = _expected_digest({"order_id": "CMD-2026-0401", "draft_sha256": DRAFT_SHA})
    assert receipt == WriteReceipt(action_id=ACTION_ID, tool=RECORD_REPLY_TOOL, args_sha256=digest)
    assert sim.executed == [
        SideEffectRecord(action_id=ACTION_ID, tool=RECORD_REPLY_TOOL, args_sha256=digest)
    ]
    payload = ReplyPrepared(order_id="CMD-2026-0401", draft_sha256=DRAFT_SHA)
    assert canonical_json(payload) == (
        f'{{"draft_sha256":"{DRAFT_SHA}","order_id":"CMD-2026-0401"}}'
    )


@pytest.mark.parametrize(
    ("order_id", "draft", "token", "rule"),
    [
        ("CMD-2026-0401", DRAFT_SHA, None, "no_token"),
        ("CMD-2026-0401", "brouillon en clair", StubToken(), "invalid_args"),
        ("commande 2026-0401", DRAFT_SHA, StubToken(), "invalid_args"),
        ("CMD-2026-0497", DRAFT_SHA, StubToken(), "unknown_order"),
        ("CMD-2026-0401", DRAFT_SHA, StubToken(action_id="action libre"), "invalid_token"),
    ],
    ids=["no_token", "text_digest", "bad_id", "unknown_order", "bad_action_id"],
)
def test_record_reply_prepared_refusals_execute_nothing(
    order_id: str, draft: str, token: StubToken | None, rule: str
) -> None:
    sim = make_erp()
    with pytest.raises(ConnectorError, match=rf"^erp\.record_reply_prepared:{rule}$") as info:
        sim.record_reply_prepared(order_id, draft, token=token)  # type: ignore[arg-type]
    assert_unchained(info.value)
    assert sim.executed == []


BASE_REQUEST = {"asset_id": "EQ-PLI-01", "symptom_code": "SYM-FUITE", "failure_code": "HYD-JOINT"}


def _history_ids(asset: str, count: int) -> tuple[str, ...]:
    rows = [w["wo_id"] for w in gmao_records()["work_orders"] if w["asset_id"] == asset]
    return tuple(rows[:count])


def test_create_work_request_records_a_digest_only() -> None:
    sim = make_gmao()
    gmao: GmaoConnector = sim
    req = WorkRequest.model_validate({**BASE_REQUEST, "cited_wo_ids": _history_ids("EQ-PLI-01", 2)})
    token = StubToken(tool=CREATE_WORK_REQUEST_TOOL)
    receipt = gmao.create_work_request(req, token=token)
    digest = _expected_digest(req.model_dump(mode="json"))
    assert receipt.args_sha256 == digest and receipt.tool == CREATE_WORK_REQUEST_TOOL
    assert sim.executed == [
        SideEffectRecord(action_id=ACTION_ID, tool=CREATE_WORK_REQUEST_TOOL, args_sha256=digest)
    ]


def test_create_work_request_refusals_execute_nothing() -> None:
    sim = make_gmao()
    token = StubToken(tool=CREATE_WORK_REQUEST_TOOL)
    own = _history_ids("EQ-PLI-01", 1)
    foreign = _history_ids("EQ-LAS-01", 1)

    def request(**changes: object) -> WorkRequest:
        return WorkRequest.model_validate({**BASE_REQUEST, "cited_wo_ids": own, **changes})

    cases: list[tuple[object, object, str]] = [
        (request(), None, "no_token"),
        ({**BASE_REQUEST, "cited_wo_ids": own}, token, "invalid_args"),
        (request(asset_id="EQ-PLI-09"), token, "unknown_asset"),
        (request(cited_wo_ids=("OT-2026-0999",)), token, "unknown_work_order"),
        (request(cited_wo_ids=(*own, *foreign)), token, "foreign_work_order"),
    ]
    for req, tok, rule in cases:
        with pytest.raises(ConnectorError, match=rf"^gmao\.create_work_request:{rule}$"):
            sim.create_work_request(req, token=tok)  # type: ignore[arg-type]
    assert sim.executed == []


def test_work_request_accepts_only_closed_codes_and_unique_citations() -> None:
    base: dict[str, object] = dict(BASE_REQUEST)
    ids = ("OT-2026-0001", "OT-2026-0002", "OT-2026-0003", "OT-2026-0004", "OT-2026-0005")
    indeterminate = {**base, "failure_code": INDETERMINATE_CODE, "cited_wo_ids": ids[:1]}
    assert WorkRequest.model_validate(indeterminate).failure_code == INDETERMINATE_CODE
    assert WorkRequest.model_validate({**base, "cited_wo_ids": ids[:4]}).cited_wo_ids == ids[:4]
    for bad in (
        {**base, "cited_wo_ids": ()},
        {**base, "cited_wo_ids": ids},
        {**base, "cited_wo_ids": (ids[0], ids[0])},
        {**base, "failure_code": "HYD-BIDON", "cited_wo_ids": ids[:1]},
        {**base, "symptom_code": None, "cited_wo_ids": ids[:1]},
        {**base, "cited_wo_ids": ids[:1], "description": "texte libre"},
    ):
        with pytest.raises(ValidationError):
            WorkRequest.model_validate(bad)


def test_simulated_classes_match_their_protocols() -> None:
    for protocol, cls in ((ErpConnector, SimErp), (GmaoConnector, SimGmao)):
        methods = [
            name
            for name, member in vars(protocol).items()
            if callable(member) and not name.startswith("_")
        ]
        assert methods
        for name in methods:
            expected = inspect.signature(getattr(protocol, name))
            actual = inspect.signature(getattr(cls, name))
            assert list(actual.parameters.values()) == list(expected.parameters.values()), name
    assert "get_order_contact" in vars(ErpConnector)


# --------------------------------------------------------------------------- fixture errors
Mutation = Callable[[Raw], None]


def _set(section: str, index: int, field: str, value: object) -> Mutation:
    def mutate(data: Raw) -> None:
        data[section][index][field] = value

    return mutate


def _other_account_contact(data: Raw) -> None:
    order = data["orders"][0]
    other = next(c for c in data["contacts"] if c["account_id"] != order["account_id"])
    order["contact_ref"] = other["contact_ref"]


def _duplicate_last(section: str, field: str) -> Mutation:
    def mutate(data: Raw) -> None:
        data[section][-1][field] = data[section][0][field]

    return mutate


def _shipped_index() -> int:
    return next(i for i, o in enumerate(erp_records()["orders"]) if o["carrier_code"])


def _drop_first(section: str) -> Mutation:
    def mutate(data: Raw) -> None:
        del data[section][0]

    return mutate


def _replace(key: str, value: object) -> Mutation:
    def mutate(data: Raw) -> None:
        data[key] = value

    return mutate


ERP_MUTATIONS: list[tuple[str, Mutation, str]] = [
    ("status", _set("orders", 0, "status", "livree"), "schema[orders.0.status: literal_error]"),
    ("extra_key", _set("orders", 0, "secret", "x"), "schema[orders.0.secret: extra_forbidden]"),
    ("price_float", _set("orders", 0, "price_unit", 12.5), "orders.0.price_unit: string_type"),
    ("qty_bool", _set("orders", 0, "qty_ordered", True), "orders.0.qty_ordered: int_type"),
    ("order_id", _set("orders", 0, "order_id", "CMD-26-0401"), "orders.0.order_id: string_pattern"),
    ("version", _replace("version", 2), "schema[version: literal_error]"),
    ("root", lambda data: data.clear(), "schema[version: missing"),
    (
        "contact_account",
        _set("contacts", 0, "account_id", "CLI-099"),
        "contacts[0].account_id: unknown_account",
    ),
    (
        "order_account",
        _set("orders", 0, "account_id", "CLI-099"),
        "orders[0].account_id: unknown_account",
    ),
    (
        "order_contact",
        _set("orders", 0, "contact_ref", "CT-099"),
        "orders[0].contact_ref: unknown_contact",
    ),
    ("other_account", _other_account_contact, "orders[0].contact_ref: other_account"),
    ("duplicate_order", _duplicate_last("orders", "order_id"), "orders[39].order_id: duplicate"),
    (
        "duplicate_contact",
        _duplicate_last("contacts", "contact_ref"),
        "contacts[13].contact_ref: duplicate",
    ),
    (
        "carrier",
        _set("orders", 0, "carrier_code", "TRP-Z"),
        "orders[0].carrier_code: unknown_carrier",
    ),
    (
        "delay",
        _set("orders", 0, "delay_reason_code", "RET-ZZZ"),
        "orders[0].delay_reason_code: unknown_delay_reason",
    ),
    ("qty", _set("orders", 0, "qty_shipped", 10_000), "orders[0].qty_shipped: exceeds_qty_ordered"),
    (
        "marker_text",
        _set("planted_markers", 0, "marker", "ABSENT"),
        "planted_markers[0].marker: not_in_note",
    ),
    (
        "marker_order",
        _set("planted_markers", 0, "order_id", "CMD-2026-0499"),
        "planted_markers[0].order_id: unknown_order",
    ),
]
GMAO_MUTATIONS: list[tuple[str, Mutation, str]] = [
    ("catalogue", _drop_first("failure_codes"), "failure_codes: catalogue_mismatch"),
    ("symptoms", _drop_first("symptom_codes"), "symptom_codes: catalogue_mismatch"),
    (
        "category",
        _set("failure_codes", 0, "category", "erreur humaine"),
        "failure_codes.0.category: literal_error",
    ),
    (
        "indeterminate",
        _set("work_orders", 1, "failure_code", "INDETERMINE"),
        "work_orders[1].failure_code: not_in_catalogue",
    ),
    (
        "asset",
        _set("work_orders", 0, "asset_id", "EQ-ZZZ-99"),
        "work_orders[0].asset_id: unknown_asset",
    ),
    (
        "action",
        _set("work_orders", 0, "action_code", "ACT-ZZZ"),
        "work_orders[0].action_code: unknown_action",
    ),
    ("duplicate_wo", _duplicate_last("work_orders", "wo_id"), "work_orders[119].wo_id: duplicate"),
    ("duplicate_asset", _duplicate_last("assets", "asset_id"), "assets[9].asset_id: duplicate"),
    (
        "time",
        _set("work_orders", 0, "opened_time", "9 h"),
        "work_orders.0.opened_time: string_pattern_mismatch",
    ),
    ("shift", _set("work_orders", 0, "shift", "matin"), "work_orders.0.shift: literal_error"),
    ("safety", _set("work_orders", 0, "safety", "non"), "work_orders.0.safety: bool_type"),
    (
        "marker_text",
        _set("planted_markers", 0, "marker", "ABSENT"),
        "planted_markers[0].marker: not_in_remark",
    ),
    (
        "marker_wo",
        _set("planted_markers", 0, "wo_id", "OT-2026-0999"),
        "planted_markers[0].wo_id: unknown_work_order",
    ),
    (
        "special",
        _replace(
            "special_remarks", {"initials_or_login": ["OT-2026-0999"], "small_role_fault": []}
        ),
        "special_remarks.initials_or_login[0]: unknown_work_order",
    ),
    (
        "indeterminate_key",
        _replace("indeterminate_code", "INCONNU"),
        "schema[indeterminate_code: literal_error]",
    ),
]


def _strings_of(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings_of(item)


def _assert_no_value(message: str, *trees: object) -> None:
    leaked = sorted(
        {s for tree in trees for s in _strings_of(tree) if len(s) >= 4 and s in message}
    )
    # Only rule-shaped words may coincide with a short fixture string; never a record value.
    assert not [s for s in leaked if not re.fullmatch(r"[a-z_]+", s)], "error.leaks_value"


def _load_mutated(
    tmp_path: Path, name: str, data: Raw, loader: Callable[[Path], object]
) -> ConnectorError:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConnectorError) as info:
        loader(path)
    message = str(info.value)
    assert message.startswith(f"{path}:"), "error.no_file_prefix"
    assert_unchained(info.value)
    return info.value


def _erp_loader(path: Path) -> object:
    return SimErp.from_yaml(path, quarantine=RecordingQuarantine())


def _gmao_loader(path: Path) -> object:
    return SimGmao.from_yaml(path, quarantine=RecordingQuarantine())


@pytest.mark.parametrize(
    ("mutation", "rule"),
    [(m, r) for _, m, r in ERP_MUTATIONS],
    ids=[n for n, _, _ in ERP_MUTATIONS],
)
def test_erp_fixture_errors_name_the_file_and_rule_only(
    tmp_path: Path, mutation: Mutation, rule: str
) -> None:
    data = erp_raw()
    original = copy.deepcopy(data)
    mutation(data)
    error = _load_mutated(tmp_path, "erp.yaml", data, _erp_loader)
    assert rule in str(error), "error.rule_missing"
    _assert_no_value(str(error), original, data)


@pytest.mark.parametrize(
    ("mutation", "rule"),
    [(m, r) for _, m, r in GMAO_MUTATIONS],
    ids=[n for n, _, _ in GMAO_MUTATIONS],
)
def test_gmao_fixture_errors_name_the_file_and_rule_only(
    tmp_path: Path, mutation: Mutation, rule: str
) -> None:
    data = gmao_raw()
    original = copy.deepcopy(data)
    mutation(data)
    error = _load_mutated(tmp_path, "gmao.yaml", data, _gmao_loader)
    assert rule in str(error), "error.rule_missing"
    _assert_no_value(str(error), original, data)


def test_shipped_order_carrier_mutation_targets_a_shipped_order(tmp_path: Path) -> None:
    data = erp_raw()
    index = _shipped_index()
    data["orders"][index]["carrier_code"] = "TRP-Z"
    error = _load_mutated(tmp_path, "erp.yaml", data, _erp_loader)
    assert f"orders[{index}].carrier_code: unknown_carrier" in str(error)


@pytest.mark.parametrize("loader", [_erp_loader, _gmao_loader], ids=["erp", "gmao"])
def test_unreadable_fixtures_fail_with_a_rule(
    tmp_path: Path, loader: Callable[[Path], object]
) -> None:
    missing = tmp_path / "absent.yaml"
    with pytest.raises(ConnectorError, match=r":file\.missing$"):
        loader(missing)
    broken = tmp_path / "broken.yaml"
    broken.write_text("orders: [\n  - {order_id: CMD-2026-0401, note: 'ouverte\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match=r":yaml\.invalid$") as info:
        loader(broken)
    assert "CMD-2026-0401" not in str(info.value)
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"version: 1\nas_of: \xff\xfe\n")
    with pytest.raises(ConnectorError, match=r":file\.unreadable$"):
        loader(binary)
    listing = tmp_path / "list.yaml"
    listing.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ConnectorError, match=r":schema\[<root>: model_type\]$"):
        loader(listing)


def test_the_repository_fixtures_load_without_error() -> None:
    assert SimErp(erp_fixture(), quarantine=RecordingQuarantine()).order_ids
    assert SimGmao(gmao_fixture(), quarantine=RecordingQuarantine()).asset_ids


# --------------------------------------------------------------------------- detector, imports
MODES: tuple[Mode, ...] = ("document", "question")


def view_ids() -> dict[str, str]:
    """Every identifier or code a view can carry, mapped to where it comes from."""
    erp, gmao = make_erp(), make_gmao()
    found: dict[str, str] = {}
    views: list[BaseModel] = []
    for order_id in sorted(erp.order_ids):
        order, contact = erp.get_order(order_id), erp.get_order_contact(order_id)
        assert order is not None and contact is not None
        views += [order, contact]
    for asset_id in sorted(gmao.asset_ids):
        asset = gmao.get_asset(asset_id)
        assert asset is not None
        views.append(asset)
    views += [
        project_work_order(WorkOrderRecord.model_validate(w), RecordingQuarantine())
        for w in gmao_records()["work_orders"]
    ]
    views += list(gmao.failure_catalog())
    id_fields = {
        "order_id",
        "account_id",
        "part_ref",
        "carrier_code",
        "tracking_ref",
        "delay_reason_code",
        "contact_ref",
        "asset_id",
        "wo_id",
        "symptom_code",
        "failure_code",
        "action_code",
        "code",
    }
    for view in views:
        for field in id_fields & set(type(view).model_fields):
            value = getattr(view, field)
            if value is not None:
                found.setdefault(str(value), f"{type(view).__name__}.{field}")
    return found


def test_the_detector_finds_nothing_in_any_view_id(privacy_ctx: PrivacyContext) -> None:
    prefix = "Référence "
    violations: list[str] = []
    ids = view_ids()
    for value, where in ids.items():
        text = f"{prefix}{value} au dossier."
        start, end = len(prefix), len(prefix) + len(value)
        violations += [
            f"{where}: {mode}: {span.type.value} ({span.rule})"
            for mode in MODES
            for span in detect_pi(text, privacy_ctx, mode=mode)
            if span.start < end and start < span.end
        ]
    assert len(ids) > 200
    assert not violations, violations


NETWORK_MODULES = ("httpx", "smtplib", "socket", "urllib", "requests", "http", "ollama")
# The only package modules each package may import (an allow-list: llm, ollama,
# embeddings, retrieval, index, the pipelines, agents, evals and cli all reach httpx or
# sit above).
MAY_IMPORT = {
    "connectors": ("alverno_lab.connectors",),
    "tools": ("alverno_lab.connectors", "alverno_lab.tools", "alverno_lab.privacy.ops_types"),
}


def _within(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in packages)


def _imports(path: Path) -> set[str]:
    """Imported modules, with ``from a import b`` recorded as both ``a`` and ``a.b``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            modules.add(base)
            sep = "" if base.endswith(".") else "."
            modules.update(f"{base}{sep}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in {"__import__", "import_module"}:
                modules.add("<dynamic import>")
    return modules


def _is_network(module: str) -> bool:
    parts = module.split(".")
    return parts[0] in NETWORK_MODULES or module == "alverno_lab.ollama"


@pytest.mark.parametrize("package", ["connectors", "tools"])
def test_connectors_and_tools_import_no_network_module(package: str) -> None:
    sources = sorted((SRC / package).glob("*.py"))
    if package == "connectors":
        assert len(sources) == 5
    for path in sources:
        modules = _imports(path)
        bad = sorted(m for m in modules if _is_network(m) or m == "<dynamic import>")
        assert not bad, f"{package}/{path.name} imports {bad}"
        assert not [m for m in modules if m.startswith(".")], path.name
        internal = [m for m in modules if _within(m, ("alverno_lab",))]
        outside = sorted(m for m in internal if not _within(m, MAY_IMPORT[package]))
        assert not outside, f"{package}/{path.name} imports {outside}"


def test_the_network_check_catches_a_planted_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import socket\nfrom urllib.request import urlopen\nfrom http import client\n"
        "import importlib\nimportlib.import_module('smtplib')\n",
        encoding="utf-8",
    )
    modules = _imports(sample)
    assert {"socket", "urllib.request", "http", "<dynamic import>"} <= modules
    assert all(_is_network(m) for m in ("socket", "urllib.request", "http", "ollama"))
    assert not _is_network("alverno_lab.connectors.types")


def test_the_import_scan_sees_names_imported_from_the_package(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from alverno_lab import ollama\nimport alverno_lab.llm\nfrom . import types\n",
        encoding="utf-8",
    )
    modules = _imports(sample)
    assert {"alverno_lab.ollama", "alverno_lab.llm", ".types"} <= modules
    for package, allowed in MAY_IMPORT.items():
        assert not _within("alverno_lab.ollama", allowed), package
        assert not _within("alverno_lab.llm", allowed), package
        assert _within("alverno_lab.connectors.types.OrderView", allowed), package


def _src_modules() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


FIXTURE_STRINGS_USERS = {"connectors/sim.py", "evals/service.py", "evals/maint.py"}


def test_only_the_gold_loaders_read_the_fixture_strings() -> None:
    users: set[str] = set()
    for rel, tree in _src_modules():
        for node in ast.walk(tree):
            name = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else node.name
                if isinstance(node, ast.alias | ast.FunctionDef)
                else None
            )
            if name == "fixture_strings":
                users.add(rel)
    assert users == FIXTURE_STRINGS_USERS


def test_no_module_outside_connectors_imports_a_fixture_record() -> None:
    sim = SRC / "connectors" / "sim.py"
    records = {
        node.name
        for node in ast.parse(sim.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name.endswith(("Record", "Fixture"))
    }
    assert {"OrderRecord", "WorkOrderRecord", "ErpFixture", "GmaoFixture"} <= records
    import alverno_lab.connectors.sim as sim_module

    assert not records & set(sim_module.__all__)
    offenders: list[str] = []
    for rel, tree in _src_modules():
        if rel.startswith("connectors/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "alverno_lab.connectors"
            ):
                offenders += [f"{rel}:{a.name}" for a in node.names if a.name in records]
            if isinstance(node, ast.Attribute) and node.attr in records:
                offenders.append(f"{rel}:{node.attr}")
    assert offenders == []
