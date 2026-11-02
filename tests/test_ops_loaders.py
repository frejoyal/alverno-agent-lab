"""Module 3 gold-set loaders (I3 spec decision 11, ADR 0015 « Attentes écrites item par item »).

Both gold sets load against the real simulated ERP and GMAO (stub quarantine), their
preconditions equal the ``svc-*`` and ``maint-*`` profiles of ``evals/thresholds.yaml``,
and every loader rule fails on a temporary copy of a gold set with one mutated line,
with the message ``<file>:<line>:<rule>`` and no value in the message, the traceback or
the exception chain. Only the gold sets, the fixtures and synthetic strings are read,
never a holdout. Assertion messages cite ids and rule names, never a value.
"""

from __future__ import annotations

import ast
import copy
import datetime as dt
import json
import traceback
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any, Literal, NamedTuple

import pytest

from alverno_lab.connectors import ORDER_STATUSES, QuarantinedText, SimErp, SimGmao
from alverno_lab.connectors.sim import fixture_strings
from alverno_lab.evals.dataset import DatasetError
from alverno_lab.evals.maint import (
    EDITABLE_FIELDS,
    MAINT_ANSWERABLE_FAMILIES,
    MAINT_FAMILIES,
    MaintGoldItem,
    load_maint_gold,
    maint_preconditions,
)
from alverno_lab.evals.ops_gold import (
    APPROVED_TEXT,
    FROM_DIAGNOSIS,
    STATUS_RENDERINGS,
    CheckedCall,
    ToolCall,
    check_call,
    render_date,
)
from alverno_lab.evals.service import (
    SVC_ANSWERABLE_FAMILIES,
    SVC_FAMILIES,
    SvcDecision,
    SvcGoldItem,
    load_svc_gold,
    svc_preconditions,
)
from alverno_lab.evals.thresholds import load_profiles
from alverno_lab.tools.registry import CreateWorkRequestArgs, RecordReplyPreparedArgs

from .conftest import EVAL_DATA, REPO_ROOT

OPS = EVAL_DATA / "ops"
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"
LOADER_MODULES = tuple(
    REPO_ROOT / "src" / "alverno_lab" / "evals" / name
    for name in ("ops_gold.py", "service.py", "maint.py")
)

# Ids that the mutations below use as « absent from the fixtures » (checked by a test).
ABSENT_ORDER = "CMD-2026-0499"
ABSENT_ACCOUNT = "CLI-099"
ABSENT_ASSET = "EQ-PLI-08"
ABSENT_WORK_ORDER = "OT-2026-0999"
# Synthetic strings the mutations insert: none is a fixture value.
INVENTED = "valeur inventée hors des fixtures"
INVENTED_MARKER = "MARQUEUR INVENTÉ HORS DES FIXTURES"
EDITED = "Bonjour, voici le suivi de votre commande."

SetName = Literal["svc", "maint"]
Row = dict[str, Any]  # Any: a raw JSON line of a gold set
Mutation = Callable[[Row], None]


def stub_quarantine(text: str) -> QuarantinedText:
    """Stub of the I4 quarantine: every free text becomes one fixed placeholder."""
    return QuarantinedText(text="texte mis en quarantaine", flags=("stub",))


@cache
def erp() -> SimErp:
    return SimErp.from_yaml(OPS / "erp.yaml", quarantine=stub_quarantine)


@cache
def gmao() -> SimGmao:
    return SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=stub_quarantine)


@cache
def svc_items() -> tuple[SvcGoldItem, ...]:
    return tuple(load_svc_gold(SVC_GOLD, erp()))


@cache
def maint_items() -> tuple[MaintGoldItem, ...]:
    return tuple(load_maint_gold(MAINT_GOLD, gmao()))


@cache
def rows(name: SetName) -> tuple[str, ...]:
    """The gold set's lines, as text (the gold sets have no blank line)."""
    path = SVC_GOLD if name == "svc" else MAINT_GOLD
    return tuple(path.read_text(encoding="utf-8").splitlines())


def load(name: SetName, path: Path) -> list[SvcGoldItem] | list[MaintGoldItem]:
    if name == "svc":
        return load_svc_gold(path, erp())
    return load_maint_gold(path, gmao())


def line_of(name: SetName, item_id: str) -> int:
    for lineno, line in enumerate(rows(name), start=1):
        if json.loads(line)["id"] == item_id:
            return lineno
    raise AssertionError(f"{item_id}: not in the gold set")


def write_copy(tmp_path: Path, name: SetName, replace: dict[int, str]) -> Path:
    """A copy of the gold set with some lines (1-based) replaced."""
    lines = [replace.get(lineno, line) for lineno, line in enumerate(rows(name), start=1)]
    path = tmp_path / f"{name}_copy.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def mutated(name: SetName, item_id: str, mutate: Mutation) -> str:
    lineno = line_of(name, item_id)
    row: Row = copy.deepcopy(json.loads(rows(name)[lineno - 1]))
    mutate(row)
    return json.dumps(row, ensure_ascii=False)


def protected_texts() -> set[str]:
    """Requests, edited texts, canaries, contact canaries, forbidden values and markers of
    both sets, plus the synthetic strings the mutations insert."""
    texts = {INVENTED, INVENTED_MARKER, EDITED}
    for svc in svc_items():
        texts |= {svc.request, *svc.canaries, *svc.contact_canaries, *svc.injection_markers}
        texts |= set(svc.must_not_include)
        texts |= {step.edited_text for step in svc.approval_script if step.edited_text}
    for maint in maint_items():
        texts |= {maint.request, *maint.canaries, *maint.injection_markers}
        texts |= set(maint.must_not_include)
    return texts


def assert_no_value(error: DatasetError) -> None:
    assert error.__cause__ is None, "error.cause"
    assert error.__context__ is None, "error.context"
    shown = str(error) + "".join(traceback.format_exception(error))
    leaks = sum(text in shown for text in protected_texts())
    assert leaks == 0, f"error.value_in_message: {leaks}"


# --------------------------------------------------------------------------- mutations
def put(*keys: str | int, value: object) -> Mutation:
    def mutate(row: Row) -> None:
        target: Any = row  # Any: walks nested JSON
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return mutate


def drop(key: str) -> Mutation:
    def mutate(row: Row) -> None:
        del row[key]

    return mutate


def append(key: str, value: object) -> Mutation:
    def mutate(row: Row) -> None:
        row[key] = [*row[key], value]

    return mutate


class Case(NamedTuple):
    name: SetName
    target: str
    mutate: Mutation
    rule: str
    reported: str | None = None  # the item whose line is reported, if not the target


def get_order(order_id: str) -> dict[str, object]:
    return {"tool": "erp.get_order", "args": {"order_id": order_id}}


def get_asset(asset_id: str) -> dict[str, object]:
    return {"tool": "gmao.get_asset", "args": {"asset_id": asset_id}}


CASES = (
    # Schema (shared reader, service model).
    Case("svc", "s001", put("extra", value=1), "schema[extra: extra_forbidden]"),
    Case("svc", "s001", drop("notes"), "schema[notes: missing]"),
    Case("svc", "s001", put("id", value="s 001"), "schema[id: string_pattern_mismatch]"),
    Case("svc", "s001", put("family", value="inconnue"), "schema[family: literal_error]"),
    Case("svc", "s001", put("route", value="inconnue"), "schema[route: literal_error]"),
    Case(
        "svc",
        "s001",
        put("order_id", value="CMD-26-1"),
        "schema[order_id: string_pattern_mismatch]",
    ),
    Case(
        "svc",
        "s027",
        put("expected_reason", value="bidon"),
        "schema[expected_reason: literal_error]",
    ),
    Case("svc", "s001", put("flags", value=["envoi"]), "schema[flags.0: literal_error]"),
    Case("svc", "s001", append("canaries", ""), "schema[canaries.0: string_too_short]"),
    Case(
        "svc",
        "s001",
        put("expected_actions", 0, "tool", value="erp.send"),
        "schema[expected_actions.0.tool: literal_error]",
    ),
    Case(
        "svc", "s001", put("pair_id", value="paire 1"), "schema[pair_id: string_pattern_mismatch]"
    ),
    # Shared rules.
    Case("svc", "s002", put("id", value="s001"), "id.duplicate"),
    Case("svc", "s001", put("expected_reason", value="restricted_info"), "expected_reason.outcome"),
    Case("svc", "s027", put("expected_reason", value=None), "expected_reason.outcome"),
    Case("svc", "s002", put("pair_id", value=None), "pair_id.not_one_trap_one_twin", "s027"),
    Case("svc", "s001", put("pair_id", value="sp01"), "pair_id.not_one_trap_one_twin", "s027"),
    # Two members, but two twins or two traps.
    Case("svc", "s027", put("family", value="order_status"), "pair_id.not_one_trap_one_twin"),
    Case(
        "svc",
        "s002",
        put("family", value="cross_account"),
        "pair_id.not_one_trap_one_twin",
        "s027",
    ),
    # Service existence rules.
    Case("svc", "s001", put("order_id", value=ABSENT_ORDER), "order_id.unknown"),
    Case("svc", "s044", put("order_id", value="CMD-2026-0401"), "order_id.must_not_exist"),
    Case("svc", "s001", put("account_id", value=ABSENT_ACCOUNT), "account_id.unknown"),
    # Service expected calls and proposals.
    Case(
        "svc",
        "s001",
        put("expected_actions", 0, value=get_asset("EQ-PLI-01")),
        "expected_actions[0].tool_of_other_system",
    ),
    Case(
        "svc",
        "s001",
        put("expected_actions", 0, "args", "order_id", value=APPROVED_TEXT),
        "expected_actions[0].placeholder_misplaced",
    ),
    Case(
        "svc",
        "s001",
        put("expected_actions", 0, "args", "extra", value=1),
        "expected_actions[0].args_invalid",
    ),
    Case(
        "svc",
        "s001",
        put("expected_actions", 0, "args", "order_id", value=ABSENT_ORDER),
        "expected_actions[0].order_id.unknown",
    ),
    Case(
        "svc",
        "s044",
        put("expected_actions", 0, value=get_order("CMD-2026-0401")),
        "expected_actions[0].order_id.must_not_exist",
    ),
    Case(
        "svc",
        "s011",
        put("expected_actions", 1, "args", "draft_sha256", value=FROM_DIAGNOSIS),
        "expected_actions[1].placeholder_misplaced",
    ),
    # The order id of a write is checked like the one of a read.
    Case(
        "svc",
        "s011",
        put("expected_actions", 1, "args", "order_id", value=ABSENT_ORDER),
        "expected_actions[1].order_id.unknown",
    ),
    Case(
        "svc",
        "s011",
        put("expected_proposal", "args", "order_id", value=ABSENT_ORDER),
        "expected_proposal.order_id.unknown",
    ),
    Case(
        "svc",
        "s011",
        put("expected_proposal", "args", "draft_sha256", value="abc"),
        "expected_proposal.args_invalid",
    ),
    Case(
        "svc",
        "s011",
        put("expected_proposal", value=get_order("CMD-2026-0411")),
        "expected_proposal.not_a_write",
    ),
    Case("svc", "s011", put("approval_script", value=[]), "expected_proposal.without_script"),
    Case(
        "svc",
        "s001",
        put("approval_script", value=[{"decision": "approve"}]),
        "approval_script.without_proposal",
    ),
    Case(
        "svc",
        "s011",
        put("approval_script", value=[{"decision": "edit"}]),
        "approval_script[0].edit_without_text",
    ),
    Case(
        "svc",
        "s011",
        put("approval_script", value=[{"decision": "edit", "edited_text": " "}]),
        "approval_script[0].edit_without_text",
    ),
    Case(
        "svc",
        "s011",
        put("approval_script", value=[{"decision": "approve", "edited_text": EDITED}]),
        "approval_script[0].text_without_edit",
    ),
    # Service fixture values.
    Case("svc", "s001", append("must_not_include", INVENTED), "must_not_include.not_fixture_value"),
    Case(
        "svc",
        "s001",
        append("must_not_include", "2026-11-15 (15 nov. 2026)"),
        "must_not_include.not_fixture_value",
    ),
    Case("svc", "s007", append("canaries", INVENTED), "canaries.not_fixture_value"),
    Case(
        "svc",
        "s011",
        put("contact_canaries", 0, value=INVENTED),
        "contact_canaries.not_fixture_value",
    ),
    Case(
        "svc",
        "s017",
        put("injection_markers", 0, value=INVENTED_MARKER),
        "injection_markers.not_fixture_text",
    ),
    # Maintenance schema.
    Case("maint", "m001", put("frequency_trap", value="true"), "schema[frequency_trap: bool_type]"),
    Case(
        "maint",
        "m001",
        put("expected_code", value="HYD-BIDON"),
        "schema[expected_code: literal_error]",
    ),
    Case(
        "maint",
        "m001",
        put("symptom_code", value="SYM-BIDON"),
        "schema[symptom_code: literal_error]",
    ),
    Case(
        "maint", "m001", put("asset_id", value="EQ-1"), "schema[asset_id: string_pattern_mismatch]"
    ),
    Case(
        "maint",
        "m017",
        put("approval_script", 0, "comment", value=EDITED),
        "schema[approval_script.0.comment: extra_forbidden]",
    ),
    Case(
        "maint",
        "m039",
        put("expected_reason", value="bidon"),
        "schema[expected_reason: literal_error]",
    ),
    # Maintenance rules.
    Case("maint", "m002", put("id", value="m001"), "id.duplicate"),
    Case("maint", "m001", put("expected_reason", value="profiling"), "expected_reason.outcome"),
    Case("maint", "m023", put("pair_id", value=None), "pair_id.not_one_trap_one_twin", "m001"),
    Case("maint", "m023", put("family", value="diagnosis"), "pair_id.not_one_trap_one_twin"),
    Case(
        "maint",
        "m001",
        put("family", value="technician_identification"),
        "pair_id.not_one_trap_one_twin",
        "m023",
    ),
    Case("maint", "m001", put("asset_id", value=ABSENT_ASSET), "asset_id.unknown"),
    Case("maint", "m039", put("asset_id", value="EQ-PLI-01"), "asset_id.must_not_exist"),
    Case(
        "maint",
        "m039",
        put("expected_actions", 0, value=get_asset("EQ-PLI-01")),
        "expected_actions[0].asset_id.must_not_exist",
    ),
    Case(
        "maint",
        "m001",
        put("expected_actions", 1, "args", "asset_id", value=ABSENT_ASSET),
        "expected_actions[1].asset_id.unknown",
    ),
    Case(
        "maint",
        "m001",
        put("expected_actions", 0, value=get_order("CMD-2026-0401")),
        "expected_actions[0].tool_of_other_system",
    ),
    Case(
        "maint",
        "m001",
        put("expected_actions", 1, "args", "limit", value=13),
        "expected_actions[1].args_invalid",
    ),
    Case(
        "maint",
        "m001",
        put("expected_actions", 1, "args", "limit", value="8"),
        "expected_actions[1].args_invalid",
    ),
    Case(
        "maint",
        "m001",
        put("expected_actions", 2, "args", value={"code": "x"}),
        "expected_actions[2].args_invalid",
    ),
    Case(
        "maint",
        "m017",
        put("expected_actions", 3, "args", "asset_id", value=FROM_DIAGNOSIS),
        "expected_actions[3].placeholder_misplaced",
    ),
    Case(
        "maint",
        "m017",
        put("expected_actions", 3, "args", "failure_code", value=APPROVED_TEXT),
        "expected_actions[3].placeholder_misplaced",
    ),
    Case(
        "maint",
        "m017",
        put("expected_actions", 3, "args", "cited_wo_ids", value=[ABSENT_WORK_ORDER]),
        "expected_actions[3].cited_wo_ids.unknown",
    ),
    # The asset id of a write is checked like the one of a read.
    Case(
        "maint",
        "m017",
        put("expected_actions", 3, "args", "asset_id", value=ABSENT_ASSET),
        "expected_actions[3].asset_id.unknown",
    ),
    Case(
        "maint",
        "m017",
        put("expected_proposal", "args", "asset_id", value=ABSENT_ASSET),
        "expected_proposal.asset_id.unknown",
    ),
    Case(
        "maint",
        "m017",
        put("expected_proposal", "args", "failure_code", value="HYD-BIDON"),
        "expected_proposal.args_invalid",
    ),
    Case(
        "maint",
        "m017",
        put("expected_proposal", value=get_asset("EQ-PLI-01")),
        "expected_proposal.not_a_write",
    ),
    Case(
        "maint",
        "m021",
        put("approval_script", 0, value={"decision": "edit"}),
        "approval_script[0].edit_without_args",
    ),
    Case(
        "maint",
        "m021",
        put("approval_script", 0, "edited_args", value={}),
        "approval_script[0].edit_without_args",
    ),
    Case(
        "maint",
        "m017",
        put("approval_script", 0, "edited_args", value={"failure_code": "HYD-JOINT"}),
        "approval_script[0].args_without_edit",
    ),
    Case(
        "maint",
        "m021",
        put("approval_script", 0, "edited_args", value={"remark": "x"}),
        "approval_script[0].edited_args.unknown_field",
    ),
    Case(
        "maint", "m001", append("must_not_include", INVENTED), "must_not_include.not_fixture_value"
    ),
    Case("maint", "m001", append("canaries", INVENTED), "canaries.not_fixture_value"),
    Case(
        "maint",
        "m003",
        put("injection_markers", 0, value=INVENTED_MARKER),
        "injection_markers.not_fixture_text",
    ),
)


def case_id(case: Case) -> str:
    return f"{case.name}-{case.target}-{case.rule}"


# --------------------------------------------------------------------------- tests
def test_both_gold_sets_load_with_the_real_simulated_connectors() -> None:
    assert len(svc_items()) == 48, "svc.n_items"
    assert len(maint_items()) == 40, "maint.n_items"
    assert {item.family for item in svc_items()} == SVC_FAMILIES, "svc.families"
    assert {item.family for item in maint_items()} == MAINT_FAMILIES, "maint.families"
    assert sum(i.is_answerable for i in svc_items()) == 26, "svc.answerable"
    assert sum(i.is_answerable for i in maint_items()) == 22, "maint.answerable"
    assert SVC_ANSWERABLE_FAMILIES < SVC_FAMILIES
    assert MAINT_ANSWERABLE_FAMILIES < MAINT_FAMILIES


def test_items_keep_the_gold_key_order() -> None:
    for name, model in (("svc", SvcGoldItem), ("maint", MaintGoldItem)):
        keys = list(json.loads(rows(name)[0]))
        assert list(model.model_fields) == keys, f"{name}.key_order"


@pytest.mark.parametrize("name", ["svc", "maint"])
def test_preconditions_equal_the_threshold_profiles(name: SetName) -> None:
    found = svc_preconditions(svc_items()) if name == "svc" else maint_preconditions(maint_items())
    profiles = {k: v for k, v in load_profiles(THRESHOLDS).items() if k.startswith(f"{name}-")}
    assert len(profiles) == 3, f"{name}.profiles"
    for profile_name, profile in profiles.items():
        for side, bounds in (("min", profile.min), ("max", profile.max)):
            expected = {k: v for k, v in bounds.items() if k.startswith("n_")}
            assert found == expected, f"{profile_name}.{side}.preconditions"


@pytest.mark.parametrize("case", CASES, ids=case_id)
def test_every_rule_fails_on_a_mutated_copy(tmp_path: Path, case: Case) -> None:
    lineno = line_of(case.name, case.target)
    path = write_copy(tmp_path, case.name, {lineno: mutated(case.name, case.target, case.mutate)})
    with pytest.raises(DatasetError) as caught:
        load(case.name, path)
    reported = line_of(case.name, case.reported or case.target)
    assert str(caught.value) == f"{path}:{reported}:{case.rule}", case_id(case)
    assert_no_value(caught.value)


def test_mutation_ids_are_absent_from_the_fixtures() -> None:
    assert ABSENT_ORDER not in erp().order_ids
    assert ABSENT_ACCOUNT not in erp().account_ids
    assert ABSENT_ASSET not in gmao().asset_ids
    assert ABSENT_WORK_ORDER not in gmao().work_order_ids


@pytest.mark.parametrize("name", ["svc", "maint"])
def test_file_level_rules(tmp_path: Path, name: SetName) -> None:
    missing = tmp_path / "absent.jsonl"
    cases: list[tuple[Path, str]] = [(missing, f"{missing}:0:file.missing")]
    unreadable = tmp_path / "unreadable.jsonl"
    unreadable.write_bytes(b"\xff\xfe\xfa{}\n")
    cases.append((unreadable, f"{unreadable}:0:file.unreadable"))
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n  \n", encoding="utf-8")
    cases.append((empty, f"{empty}:0:set.empty"))
    broken = write_copy(tmp_path, name, {3: '{"id": "x",'})
    cases.append((broken, f"{broken}:3:json.invalid"))
    # json.loads raises a bare ValueError past the int digit limit, and RecursionError
    # for arrays nested too deep: both are json.invalid, with file and line.
    for folder, lineno, line in (
        ("huge", 2, '{"id": ' + "7" * 5000 + "}"),
        ("deep", 4, "[" * 200_000 + "]" * 200_000),
    ):
        (tmp_path / folder).mkdir()
        copied = write_copy(tmp_path / folder, name, {lineno: line})
        cases.append((copied, f"{copied}:{lineno}:json.invalid"))
    array = tmp_path / "array.jsonl"
    array.write_text("[1, 2]\n", encoding="utf-8")
    cases.append((array, f"{array}:1:schema[<root>: model_type]"))
    for path, message in cases:
        with pytest.raises(DatasetError) as caught:
            load(name, path)
        assert str(caught.value) == message, message.rsplit(":", 1)[-1]
        assert_no_value(caught.value)


def test_blank_lines_are_skipped_and_still_counted(tmp_path: Path) -> None:
    lines = list(rows("svc"))
    path = tmp_path / "blank.jsonl"
    path.write_text("\n".join(["", *lines[:10], "  ", *lines[10:]]) + "\n", encoding="utf-8")
    assert len(load_svc_gold(path, erp())) == 48
    lines[4] = "{"
    path.write_text("\n".join(["", *lines]) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match=r":6:json\.invalid$"):
        load_svc_gold(path, erp())


def test_existing_cited_work_orders_pass(tmp_path: Path) -> None:
    cited = sorted(gmao().work_order_ids)[:2]
    lineno = line_of("maint", "m017")
    mutation = put("expected_actions", 3, "args", "cited_wo_ids", value=cited)
    path = write_copy(tmp_path, "maint", {lineno: mutated("maint", "m017", mutation)})
    items = load_maint_gold(path, gmao())
    args = next(i for i in items if i.id == "m017").expected_actions[3].args
    assert args["cited_wo_ids"] == cited


def test_repr_holds_no_request_and_no_canary() -> None:
    for svc in svc_items():
        shown = repr(svc) + str(svc)
        hidden = {svc.request, *svc.canaries, *svc.contact_canaries, *svc.injection_markers}
        hidden |= {step.edited_text for step in svc.approval_script if step.edited_text}
        assert not any(text in shown for text in hidden), f"{svc.id}: repr.value"
    for maint in maint_items():
        shown = repr(maint) + str(maint)
        hidden = {maint.request, *maint.canaries, *maint.injection_markers}
        assert not any(text in shown for text in hidden), f"{maint.id}: repr.value"


HIDDEN_FIELDS = {
    "svc": (
        "request",
        "must_include",
        "must_not_include",
        "canaries",
        "contact_canaries",
        "injection_markers",
    ),
    "maint": ("request", "must_include", "must_not_include", "canaries", "injection_markers"),
}


@pytest.mark.parametrize("name", ["svc", "maint"])
def test_every_protected_field_is_out_of_repr(name: SetName) -> None:
    item = svc_items()[0] if name == "svc" else maint_items()[0]
    fields = HIDDEN_FIELDS[name]
    for field in fields:
        assert type(item).model_fields[field].repr is False, f"{name}.{field}"
    # Every protected field set to one synthetic marker: none shows in repr or str.
    update: dict[str, object] = {f: (INVENTED,) for f in fields}
    update["request"] = INVENTED
    marked = item.model_copy(update=update)
    assert INVENTED not in repr(marked) + str(marked), f"{name}: repr.value"
    assert SvcDecision.model_fields["edited_text"].repr is False


def test_a_quantity_is_not_a_forbidden_value(tmp_path: Path) -> None:
    texts = fixture_strings(erp())
    quantities = {
        str(q)
        for order_id in erp().order_ids
        if (view := erp().get_order(order_id)) is not None
        for q in (view.qty_ordered, view.qty_shipped)
    }
    candidates = sorted(q for q in quantities if q not in texts)
    assert candidates, "erp.quantities"
    lineno = line_of("svc", "s001")
    mutation = append("must_not_include", candidates[0])
    path = write_copy(tmp_path, "svc", {lineno: mutated("svc", "s001", mutation)})
    with pytest.raises(DatasetError) as caught:
        load_svc_gold(path, erp())
    assert str(caught.value) == f"{path}:{lineno}:must_not_include.not_fixture_value"


@pytest.mark.parametrize("name", ["svc", "maint"])
def test_a_subset_of_a_gold_set_loads(tmp_path: Path, name: SetName) -> None:
    # The holdouts have other counts: the loader checks none. Keep the unpaired items
    # and one whole pair.
    decoded = [json.loads(line) for line in rows(name)]
    unpaired = [line for line, row in zip(rows(name), decoded, strict=True) if not row["pair_id"]]
    first_pair = next(row["pair_id"] for row in decoded if row["pair_id"])
    pair = [
        line for line, row in zip(rows(name), decoded, strict=True) if row["pair_id"] == first_pair
    ]
    subset = [*unpaired[:5], *pair]
    path = tmp_path / f"{name}_subset.jsonl"
    path.write_text("\n".join(subset) + "\n", encoding="utf-8", newline="\n")
    assert len(load(name, path)) == len(subset) == 7


def _names(path: Path) -> Iterator[str]:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.alias):
            yield node.name
            if node.asname:
                yield node.asname
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


@pytest.mark.parametrize("path", LOADER_MODULES, ids=lambda p: p.name)
def test_loaders_never_reference_the_flow_plans(path: Path) -> None:
    assert path.is_file(), path.name
    hits = [name for name in _names(path) if "FLOW_PLANS" in name]
    assert hits == [], f"{path.name}: FLOW_PLANS referenced"


def test_status_and_date_renderings_follow_adr_0015() -> None:
    assert set(STATUS_RENDERINGS) == ORDER_STATUSES
    assert render_date(dt.date(2026, 11, 15)) == "2026-11-15 (15 novembre 2026)"
    assert render_date(dt.date(2027, 2, 1)) == "2027-02-01 (1er février 2027)"
    assert render_date(dt.date(2026, 8, 9)) == "2026-08-09 (9 août 2026)"


def test_check_call_accepts_placeholders_only_in_their_fields() -> None:
    reply = ToolCall(
        tool="erp.record_reply_prepared",
        args={"order_id": "CMD-2026-0401", "draft_sha256": APPROVED_TEXT},
    )
    checked = check_call(reply)
    assert isinstance(checked, CheckedCall)
    assert isinstance(checked.args, RecordReplyPreparedArgs)
    assert checked.placeholders == {"draft_sha256"}
    request = ToolCall(
        tool="gmao.create_work_request",
        args={
            "asset_id": "EQ-PLI-01",
            "symptom_code": "SYM-FUITE",
            "failure_code": FROM_DIAGNOSIS,
            "cited_wo_ids": FROM_DIAGNOSIS,
        },
    )
    checked = check_call(request)
    assert isinstance(checked, CheckedCall)
    assert isinstance(checked.args, CreateWorkRequestArgs)
    assert checked.placeholders == {"failure_code", "cited_wo_ids"}
    nested = ToolCall(
        tool="gmao.create_work_request",
        args={**request.args, "cited_wo_ids": [FROM_DIAGNOSIS]},
    )
    assert check_call(nested) == "args_invalid"
    wrong = ToolCall(tool="erp.get_order", args={"order_id": FROM_DIAGNOSIS})
    assert check_call(wrong) == "placeholder_misplaced"


def test_editable_fields_are_the_work_request_fields() -> None:
    assert {"asset_id", "symptom_code", "failure_code", "cited_wo_ids"} == EDITABLE_FIELDS
