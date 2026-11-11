"""Evaluation of the management orchestrator (profiles dir-*, ADR 0020) and its negative
controls. Each control turns one barrier off and must make its metric fail."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from alverno_lab import direction_pipeline
from alverno_lab.agents.direction import graph as dir_graph
from alverno_lab.cli import app
from alverno_lab.config import Settings
from alverno_lab.connectors import sim
from alverno_lab.connectors.types import LateOrderRow, MaintenanceOverview
from alverno_lab.evals.direction_eval import DirEvalReport, run_direction_eval
from alverno_lab.evals.thresholds import check_thresholds

from .conftest import EVAL_DATA, REPO_ROOT

THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"
GOLD = EVAL_DATA / "dir_gold.jsonl"


def _run(out: Path, *, mode: str = "tools", dataset: Path = GOLD) -> DirEvalReport:
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)  # relative settings paths
        return run_direction_eval(
            Settings(), mode=mode, fake=True, dataset=dataset, out_dir=out, stem=f"eval-{mode}"
        )


@pytest.fixture(scope="module")
def tools_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[DirEvalReport, Path]:
    out = tmp_path_factory.mktemp("dir-tools")
    return _run(out), out


@pytest.fixture(scope="module")
def full_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[DirEvalReport, Path]:
    out = tmp_path_factory.mktemp("dir-full")
    return _run(out, mode="full"), out


def _subset(tmp_path: Path, keep: set[str]) -> Path:
    """A copy of the chosen items, without pairs (a trap may come without its twin)."""
    items = [json.loads(ln) for ln in GOLD.read_text(encoding="utf-8").splitlines()]
    kept = [{**i, "pair_id": None} for i in items if i["id"] in keep]
    path = tmp_path / "subset.jsonl"
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in kept), "utf-8")
    return path


# --------------------------------------------------------------------------- runs
@pytest.mark.parametrize("run", ["tools_run", "full_run"])
def test_the_fake_runs_pass_their_gate(run: str, request: pytest.FixtureRequest) -> None:
    report, _ = request.getfixturevalue(run)
    result = check_thresholds(report, THRESHOLDS)
    assert result.passed, result.failures
    assert report.metrics["count_fidelity"] == 1.0
    assert report.metrics["route_accuracy"] == 1.0


def test_the_full_run_names_its_fake_model(full_run: tuple[DirEvalReport, Path]) -> None:
    assert full_run[0].model == direction_pipeline.FAKE_DIRECTION_MODEL
    assert full_run[0].profile == "dir-full-fake"


def test_no_question_reaches_a_report_or_a_log(tools_run: tuple[DirEvalReport, Path]) -> None:
    _, out = tools_run
    text = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        assert json.loads(line)["question"] not in text


def test_one_direction_log_line_per_item(tools_run: tuple[DirEvalReport, Path]) -> None:
    _, out = tools_run
    lines = [
        json.loads(line)
        for path in (out / "access").glob("direction-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(lines) == 40
    assert {line["channel"] for line in lines} == {"eval"}


# --------------------------------------------------------------------------- controls
def test_an_allow_all_screen_lets_traps_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dir_graph, "screen", lambda question: None)
    report = _run(tmp_path, dataset=_subset(tmp_path, {"d030", "d031", "d037", "d039", "d021"}))
    assert report.metrics["trap_refusal_rate"] < 1


def test_a_leaky_maintenance_projection_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sim.SimGmao.maintenance_overview

    def leaky(self: Any, asset_id: Any, start: dt.date, end: dt.date) -> MaintenanceOverview:
        view = original(self, asset_id, start, end)
        rows = [w for w in self._work_orders.values() if w.kind == "correctif"][:2]
        extra = tuple((w.asset_id, f"{w.technician_name}, {w.downtime_min} min", 1) for w in rows)
        return view.model_copy(update={"by_asset": view.by_asset + extra})

    monkeypatch.setattr(sim.SimGmao, "maintenance_overview", leaky)
    report = _run(tmp_path, dataset=_subset(tmp_path, {"d020", "d021"}))
    assert report.metrics["technician_identity_exposure"] > 0
    assert report.metrics["duration_exposure"] > 0


def test_a_projection_that_names_the_person_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sim.SimErp.orders_overview

    def leaky(self: Any, account_id: Any) -> Any:
        view = original(self, account_id)
        rows = tuple(
            LateOrderRow(**{**r.model_dump(), "account_label": self._accounts[r.account_id].label})
            for r in view.late
        )
        return view.model_copy(update={"late": rows})

    monkeypatch.setattr(sim.SimErp, "orders_overview", leaky)
    report = _run(tmp_path, dataset=_subset(tmp_path, {"d016"}))
    assert report.metrics["natural_person_label_exposure"] > 0
    assert report.metrics["must_not_include_hits"] > 0


def test_a_linking_summary_without_verify_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def linking(system: str, user: str, schema: Any) -> dict[str, Any]:
        del system, schema
        if "Synthèse des commandes" in user:
            return {
                "summary": "Le retard vient de la panne de EQ-PLI-01. Commandes au total : 999."
            }
        return {"summary": ""}

    monkeypatch.setattr(direction_pipeline, "summary_echo", linking)
    monkeypatch.setattr(dir_graph, "verify_summary", lambda summary, block: (summary, 0))
    report = _run(tmp_path, mode="full", dataset=_subset(tmp_path, {"d013", "d015"}))
    assert report.metrics["cross_section_claims"] > 0
    assert report.metrics["count_fidelity"] < 1


def test_with_verify_the_same_summary_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def linking(system: str, user: str, schema: Any) -> dict[str, Any]:
        del system, schema
        return {"summary": "Le retard vient de la panne de EQ-PLI-01. Commandes au total : 999."}

    monkeypatch.setattr(direction_pipeline, "summary_echo", linking)
    report = _run(tmp_path, mode="full", dataset=_subset(tmp_path, {"d013", "d015"}))
    assert report.metrics["cross_section_claims"] == 0
    assert report.metrics["count_fidelity"] == 1


# --------------------------------------------------------------------------- cli
def test_the_tools_eval_needs_fake() -> None:
    result = CliRunner().invoke(app, ["direction", "eval", "--mode", "tools"])
    assert result.exit_code == 2
    assert "--fake" in result.output


def test_ci_runs_both_direction_gates_before_the_scan() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    tools = "uv run alverno direction eval --mode tools --fake --out reports/ci"
    full = "uv run alverno direction eval --mode full --fake --out reports/ci"
    assert workflow.index(tools) < workflow.index(full) < workflow.index("privacy scan")
    assert "--dataset data/eval/dir_gold.jsonl" in workflow
