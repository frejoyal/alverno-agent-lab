"""The 3b evaluation (I9 spec decision 9; ADR 0015) on the maint_gold set, tools mode.

:func:`run_maint_eval` passes the pre-registered ``maint-tools-fake`` profile, reports
every metric of the profile, writes no canary to its folder, and fails the preconditions
with ``--limit``. The unit tests cover the content metrics (denominators, empty sets,
pairs, hits), the ``<from_diagnosis>`` arguments and the median of repetitions.
Assertions cite metric names and item ids, never a request or a canary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alverno_lab.evals.canaries import load_canaries
from alverno_lab.evals.maint_eval import (
    MAINT_SERVICE_METRICS,
    MaintEvalReport,
    MaintItemResult,
    maint_content_metrics,
    maint_counts,
    maint_report_markdown,
    run_maint_eval,
    with_maint_medians,
)
from alverno_lab.evals.thresholds import check_thresholds, load_profiles
from alverno_lab.privacy import scan_paths

from .conftest import EVAL_DATA, REPO_ROOT, hr_repo_settings

OPS = EVAL_DATA / "ops"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> tuple[MaintEvalReport, Path]:
    out = tmp_path_factory.mktemp("maint_eval")
    settings = hr_repo_settings(
        ops_erp_path=OPS / "erp.yaml",
        ops_gmao_path=OPS / "gmao.yaml",
        ops_vocabulary_path=OPS / "vocabulary.yaml",
        maint_eval_path=MAINT_GOLD,
    )
    return run_maint_eval(settings, mode="tools", fake=True, out_dir=out), out


def test_the_tools_run_passes_the_pre_registered_profile(
    report: tuple[MaintEvalReport, Path],
) -> None:
    result, _ = report
    assert result.profile == "maint-tools-fake"
    verdict = check_thresholds(result, THRESHOLDS)
    assert verdict.passed, verdict.failures


def test_every_profile_metric_is_reported(report: tuple[MaintEvalReport, Path]) -> None:
    result, _ = report
    profiles = load_profiles(THRESHOLDS)
    for name in ("maint-tools-fake", "maint-full-fake", "maint-full-ollama"):
        profile = profiles[name]
        keys = set(profile.min) | set(profile.max)
        assert keys <= set(result.metrics), name
    for key in ("bm25_hit_at_1", "bm25_hit_at_3", "unfetched_citations", "n_chat_calls"):
        assert key in result.metrics


def test_every_item_reaches_its_expected_end(report: tuple[MaintEvalReport, Path]) -> None:
    result, _ = report
    for item in result.items:
        assert (item.outcome, item.reason) == (item.expected_outcome, item.expected_reason), item.id
        assert item.safety_count == 0 and item.error is None, item.id
        if item.route is not None:
            assert item.actual_route == item.route, item.id
        assert item.n_matched_calls == item.n_expected_calls, item.id


def test_tools_mode_diagnosis_is_the_bm25_ranking(report: tuple[MaintEvalReport, Path]) -> None:
    result, _ = report
    m = result.metrics
    assert m["diagnosis_hit_at_1"] == m["bm25_hit_at_1"]
    assert m["diagnosis_hit_at_3"] == m["bm25_hit_at_3"]
    assert m["n_chat_calls"] == 0 and m["citation_validity_rate"] == 1.0
    for item in result.items:
        if item.family == "diagnosis":
            assert item.codes[:3] == item.bm25_codes[:3], item.id


def test_the_run_folder_holds_no_canary_and_no_text(report: tuple[MaintEvalReport, Path]) -> None:
    result, out = report
    canaries = [loaded.canary for loaded in load_canaries(MAINT_GOLD)]
    assert canaries and not scan_paths([out], canaries)
    assert result.metrics["artifact_canary_hits"] == 0
    payload = json.loads((out / "eval-maint-tools-fake.json").read_text(encoding="utf-8"))
    assert set(payload["items"][0]) == set(MaintItemResult.model_fields)
    assert (out / "traces" / "eval-maint-tools-fake.jsonl").is_file()
    assert any((out / "access").glob("maint-*.jsonl"))
    text = (out / "eval-maint-tools-fake.md").read_text(encoding="utf-8")
    assert "maint-tools-fake" in text and "## Counts" in text and "\r" not in text


def test_a_limit_fails_the_preconditions(tmp_path: Path) -> None:
    settings = hr_repo_settings(
        ops_erp_path=OPS / "erp.yaml",
        ops_gmao_path=OPS / "gmao.yaml",
        ops_vocabulary_path=OPS / "vocabulary.yaml",
        maint_eval_path=MAINT_GOLD,
    )
    result = run_maint_eval(settings, mode="tools", fake=True, out_dir=tmp_path, limit=3)
    verdict = check_thresholds(result, THRESHOLDS)
    assert not verdict.passed
    assert any("n_items" in line for line in verdict.failures)
    review = maint_report_markdown(result)
    assert "## Items to review" in review or result.n_items == 3


def test_a_full_mode_rig_that_cannot_be_built_refuses_before_any_file(tmp_path: Path) -> None:
    from alverno_lab.config import RemoteOllamaError

    settings = hr_repo_settings(
        ops_erp_path=OPS / "erp.yaml",
        ops_gmao_path=OPS / "gmao.yaml",
        ops_vocabulary_path=OPS / "vocabulary.yaml",
        maint_eval_path=MAINT_GOLD,
        allow_remote_ollama=True,
    )
    out = tmp_path / "out"
    with pytest.raises(RemoteOllamaError):
        run_maint_eval(settings, mode="full", fake=True, out_dir=out)
    assert not out.exists()


# --------------------------------------------------------------------------- units
def result_(**over: object) -> MaintItemResult:
    values: dict[str, object] = {
        "id": "x",
        "family": "diagnosis",
        "answerable": True,
        "expected_outcome": "answered",
        "outcome": "answered",
        "route": "diagnosis",
        "actual_route": "diagnosis",
        "expected_code": "HYD-FLEX",
        "codes": ["HYD-FLEX"],
        "bm25_codes": ["HYD-JOINT", "HYD-FLEX"],
        "valid_citations": 1,
    }
    values.update(over)
    return MaintItemResult.model_validate(values)


def test_empty_denominators_fail_the_gate() -> None:
    m = maint_content_metrics([])
    for key in (
        "trap_refusal_rate",
        "pair_accuracy",
        "diagnosis_hit_at_1",
        "citation_validity_rate",
    ):
        assert m[key] == 0.0, key
    assert m["false_block_rate"] == 1.0


def test_hits_count_indeterminate_as_a_miss() -> None:
    items = [
        result_(),
        result_(id="y", codes=["INDETERMINE", "HYD-FLEX"], frequency_trap=True),
        result_(id="z", expected_code="INDETERMINE", codes=["INDETERMINE"]),
    ]
    m = maint_content_metrics(items)
    assert m["diagnosis_hit_at_1"] == pytest.approx(1 / 3)
    assert m["diagnosis_hit_at_3"] == pytest.approx(2 / 3)
    assert m["frequency_trap_hit_at_1"] == 0.0
    assert m["bm25_hit_at_1"] == 0.0 and m["bm25_hit_at_3"] == pytest.approx(2 / 3)


def test_pairs_false_blocks_and_citations() -> None:
    trap = result_(
        id="t",
        family="technician_identification",
        answerable=False,
        pair_id="p",
        expected_outcome="refused",
        outcome="refused",
        expected_reason="person_targeted",
        reason="person_targeted",
        route=None,
        actual_route=None,
    )
    twin = result_(id="w", pair_id="p", valid_citations=3, dropped_citations=1)
    blocked = result_(id="b", actual_route="asset_not_identified", outcome="refused")
    m = maint_content_metrics([trap, twin, blocked])
    assert m["pair_accuracy"] == 1.0 and m["trap_refusal_rate"] == 1.0
    assert m["false_block_rate"] == pytest.approx(1 / 2)
    assert m["citation_validity_rate"] == pytest.approx(4 / 5)
    assert m["route_accuracy"] == pytest.approx(1 / 2)


def test_medians_replace_the_service_metrics_only(report: tuple[MaintEvalReport, Path]) -> None:
    base, _ = report
    low = base.model_copy(update={"metrics": {**base.metrics, "diagnosis_hit_at_1": 0.25}})
    high = base.model_copy(update={"metrics": {**base.metrics, "diagnosis_hit_at_1": 1.0}})
    first, *_ = with_maint_medians([low, base, high])
    assert first.metrics["diagnosis_hit_at_1"] == base.metrics["diagnosis_hit_at_1"]
    assert set(MAINT_SERVICE_METRICS) <= set(first.metrics)
    rows = maint_counts(base)
    assert {key for key, *_ in rows} >= {"trap_refusal_rate", "artifact_canary_hits"}


def test_expected_arguments_and_digests_fail_closed() -> None:
    from alverno_lab.evals import maint_eval
    from alverno_lab.evals.ops_gold import ToolCall

    wrong = ToolCall(tool="gmao.get_asset", args={"asset_id": "nope"})
    assert maint_eval._expected_args(wrong, None) is None
    proposal = ToolCall(
        tool="gmao.create_work_request",
        args={
            "asset_id": "EQ-PLI-01",
            "symptom_code": "SYM-FUITE",
            "failure_code": "<from_diagnosis>",
            "cited_wo_ids": "<from_diagnosis>",
        },
    )
    assert maint_eval._expected_args(proposal, None) is None
    filled = maint_eval._expected_args(proposal, ([], ["OT-2026-0001"], 0))
    assert filled is not None and filled["failure_code"] == "INDETERMINE"
    assert maint_eval._digest("gmao.get_asset", None) is None
    assert maint_eval._digest("gmao.get_asset", {"asset_id": "nope"}) is None


def test_issues_and_markdown_name_codes_only(report: tuple[MaintEvalReport, Path]) -> None:
    from alverno_lab.evals import maint_eval

    base = result_()
    assert maint_eval._issue(base) is None
    assert maint_eval._issue(result_(error="start:graph_error")) == "error (start:graph_error)"
    cases = {
        "ended": result_(outcome="refused"),
        "reason": result_(reason="ungrounded"),
        "routed": result_(actual_route="work_request"),
        "2 safety": result_(safety_count=2),
        "0 of 3": result_(n_expected_calls=3),
    }
    for start, item in cases.items():
        assert str(maint_eval._issue(item)).startswith(start), start
    assert maint_eval._issue(result_(codes=["HYD-JOINT"])) == "expected code not first after verify"
    result, _ = report
    labelled = result.model_copy(update={"label": "essai", "items": [result_(outcome="refused")]})
    text = maint_report_markdown(labelled)
    assert "- Label: essai" in text and "## Items to review" in text
    partial = with_maint_medians([result, result.model_copy(update={"metrics": {}})])
    assert partial[1].metrics == {}


def test_the_probes_read_nothing_for_an_unknown_asset(report: tuple[MaintEvalReport, Path]) -> None:
    from alverno_lab.connectors import SimErp, SimGmao
    from alverno_lab.evals import maint_eval
    from alverno_lab.evals.maint import load_maint_gold
    from alverno_lab.ops_pipeline import ops_quarantine, service_scope_index

    from .ops_privacy_helpers import ops_privacy_ctx

    quarantine = ops_quarantine(ops_privacy_ctx())
    erp = SimErp.from_yaml(OPS / "erp.yaml", quarantine=quarantine)
    gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=quarantine)
    settings = hr_repo_settings(maint_eval_path=MAINT_GOLD)
    probes = maint_eval._Probes(settings, erp, gmao, service_scope_index(erp))
    assert probes.fetched(None, None) == frozenset()
    assert probes.fetched("EQ-ZZZ-99", "SYM-FUITE") == frozenset()
    unknown = next(i for i in load_maint_gold(MAINT_GOLD, gmao) if i.family == "unknown_asset")
    assert probes.bm25(unknown) == []
