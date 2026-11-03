"""``alverno maint bench`` (increment I10, ADR 0015 « Banc d'essai » and « Répétitions »).

Every bench here runs ``--fake`` on the 3b gold set: no model, no network. The default
chat model runs ``--repeats`` times (3 by default) and any other model once; each
repetition is gated on its own counts and on the median of the ``maint-full-*`` service
metrics. The README is rewritten in the RESULTS-MAINT blocks only, and only without a
blocker, a canary in the run folder or an unmet precondition; a canary hit renames the
run folder ``<folder>.quarantine``. Assertions cite file names, labels and codes, never a
request, a diagnosis, a remark or a canary value.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from alverno_lab import cli
from alverno_lab.cli import app
from alverno_lab.config import Settings
from alverno_lab.connectors import SimGmao
from alverno_lab.evals.canaries import UNTYPED_LABEL, load_canaries
from alverno_lab.evals.maint import load_maint_gold, maint_preconditions
from alverno_lab.evals.maint_bench import (
    MAINT_PRECONDITIONS,
    MAINT_SAFETY_COUNTS,
    MaintBenchRow,
    maint_bench_blockers,
    maint_bench_markdown,
    maint_bench_table,
    maint_file_canaries,
    maint_unmet_preconditions,
)
from alverno_lab.evals.maint_eval import MaintEvalReport, maint_counts, run_maint_eval
from alverno_lab.evals.ops_bench_common import (
    OPS_SAFETY_COUNTS,
    OpsBenchRow,
    OpsRunNote,
    canary_hit_files,
    rule_of_three_bound,
)
from alverno_lab.evals.service_bench import SVC_SAFETY_COUNTS
from alverno_lab.evals.thresholds import load_profiles
from alverno_lab.ops_pipeline import ops_quarantine
from alverno_lab.privacy import Canary, PIType
from alverno_lab.tools import registry

from .conftest import EVAL_DATA, HR_DATA, REPO_ROOT
from .ops_privacy_helpers import ops_privacy_ctx

runner = CliRunner()
OPS = EVAL_DATA / "ops"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"
README = REPO_ROOT / "README.md"
DEFAULT_MODEL = str(Settings.model_fields["chat_model"].default)
OTHER_MODEL = "fake-ops-b"
RUN_DIR = re.compile(r"bench-maint-fake-\d{8}-\d{6}")
MAINT_FR, MAINT_EN = "RESULTS-MAINT:FR", "RESULTS-MAINT:EN"
OTHER_FAMILIES = ("RESULTS", "RESULTS-HR", "RESULTS-SVC")


def _apply_env(mp: pytest.MonkeyPatch, root: Path) -> None:
    """Repository data, a temporary access log, no developer variable, no .env."""
    for key in [k for k in os.environ if k.startswith("ALVERNO_")]:
        mp.delenv(key)
    env = {
        "ALVERNO_HR_CORPUS_DIR": HR_DATA / "corpus",
        "ALVERNO_HR_ROLES_PATH": HR_DATA / "org_roles.yaml",
        "ALVERNO_HR_CONTACTS_PATH": HR_DATA / "contacts.yaml",
        "ALVERNO_OPS_ERP_PATH": OPS / "erp.yaml",
        "ALVERNO_OPS_GMAO_PATH": OPS / "gmao.yaml",
        "ALVERNO_OPS_VOCABULARY_PATH": OPS / "vocabulary.yaml",
        "ALVERNO_MAINT_EVAL_PATH": MAINT_GOLD,
        "ALVERNO_THRESHOLDS_PATH": THRESHOLDS,
        "ALVERNO_ACCESS_LOG_DIR": root / "access-cli",
    }
    for name, value in env.items():
        mp.setenv(name, str(value))
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        mp.delenv(name, raising=False)
    mp.chdir(root)


def bench(out: Path, *args: str) -> Result:
    return runner.invoke(app, ["maint", "bench", "--fake", "--out", str(out), *args])


def run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []


def outside_maint(text: str) -> list[str]:
    """The README cut at the four RESULTS-MAINT marker lines, the blocks left out."""
    lines = text.split("\n")
    bare = [line.strip() for line in lines]
    names = (MAINT_FR, f"/{MAINT_FR}", MAINT_EN, f"/{MAINT_EN}")
    a, b, c, d = (bare.index(f"<!-- {name} -->") for name in names)
    return ["\n".join(lines[: a + 1]), "\n".join(lines[b : c + 1]), "\n".join(lines[d:])]


def block(text: str, name: str) -> str:
    return text.split(f"<!-- {name} -->")[1].split(f"<!-- /{name} -->")[0]


def output_hits(result: Result, where: Path, canaries: list[Canary]) -> list[tuple[Path, str]]:
    """Canary hits in the command's stdout and stderr, scanned as files."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (where / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    return canary_hit_files([where], canaries)


@dataclasses.dataclass(frozen=True)
class BenchRun:
    result: Result
    out: Path
    readme: Path
    before: str
    canaries: list[Canary]
    root: Path


@pytest.fixture(scope="module")
def bench_run(tmp_path_factory: pytest.TempPathFactory) -> BenchRun:
    """One bench, shared by the module: the default model (k = 3) and another (k = 1)."""
    root = tmp_path_factory.mktemp("maint_bench")
    readme = root / "README.md"
    shutil.copyfile(README, readme)
    before = readme.read_text(encoding="utf-8")
    out = root / "reports"
    with pytest.MonkeyPatch.context() as mp:
        _apply_env(mp, root)
        result = bench(out, "--models", f"{DEFAULT_MODEL},{OTHER_MODEL}", "--readme", str(readme))
        canaries = maint_file_canaries(Settings().for_module("maint"))
    return BenchRun(result, out, readme, before, canaries, root)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    _apply_env(monkeypatch, tmp_path)
    readme = tmp_path / "README.md"
    shutil.copyfile(README, readme)
    return readme


# --------------------------------------------------------------------------- success
def test_the_bench_exits_0_and_writes_every_file(bench_run: BenchRun) -> None:
    assert bench_run.result.exit_code == 0, bench_run.result.output[-2000:]
    (run_dir,) = run_dirs(bench_run.out)
    assert RUN_DIR.fullmatch(run_dir.name), run_dir.name
    stem = "gemma4_e4b"
    names = {p.name for p in run_dir.iterdir() if p.is_file()}
    expected = {f"{stem}-r{i}.{ext}" for i in (1, 2, 3) for ext in ("json", "md")}
    expected |= {f"{OTHER_MODEL}.json", f"{OTHER_MODEL}.md"}
    expected |= {"summary.md", "summary.fr.md", "summary.json"}
    assert names == expected
    assert list((run_dir / "access").glob("maint-*.jsonl"))
    traces = {p.name for p in (run_dir / "traces").iterdir()}
    assert traces == {f"{stem}-r{i}.jsonl" for i in (1, 2, 3)} | {f"{OTHER_MODEL}.jsonl"}
    # The CLI access log of `maint ask` is never touched by the bench.
    assert not (bench_run.root / "access-cli").exists()
    assert "on the 3b set..." in bench_run.result.stdout


def test_the_default_model_runs_three_times_and_every_repetition_passes(
    bench_run: BenchRun,
) -> None:
    (run_dir,) = run_dirs(bench_run.out)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    models = {m["model"]: m for m in summary["models"]}
    assert [m["model"] for m in summary["models"]] == [DEFAULT_MODEL, OTHER_MODEL]
    assert models[DEFAULT_MODEL]["k"] == 3 and len(models[DEFAULT_MODEL]["reports"]) == 3
    assert models[OTHER_MODEL]["k"] == 1 and len(models[OTHER_MODEL]["reports"]) == 1
    for model in models.values():
        assert model["gates"] == [True] * model["k"], model["model"]
        assert model["gate_passed"] is True
        for report in model["reports"]:
            assert report["module"] == "maint" and report["mode"] == "full"
            assert report["label"] == model["model"]
            assert report["config"]["chat_model"] == model["model"]
    note = summary["note"]
    assert note["ollama_version"] == "fake"
    assert note["profile"] == "maint-full-fake"
    assert note["dataset"].endswith("maint_gold.jsonl")
    assert note["registry_sha256"] == registry.registry_sha256()


def test_the_summaries_hold_the_table_and_the_run_note(bench_run: BenchRun) -> None:
    (run_dir,) = run_dirs(bench_run.out)
    english = (run_dir / "summary.md").read_text(encoding="utf-8")
    french = (run_dir / "summary.fr.md").read_text(encoding="utf-8")
    assert english.startswith("# Maintenance benchmark — full mode (fake)")
    assert french.startswith("# Banc d'essai de l'entretien — mode complet (fake)")
    assert f"| `{DEFAULT_MODEL}` | 3 | ✅ ✅ ✅ |" in english
    assert f"| `{OTHER_MODEL}` | 1 | ✅ |" in french
    assert "| diagnosis hit@1 | diagnosis hit@3 | frequency traps hit@1 | valid citations |" in (
        english
    )
    assert "| diagnostic hit@1 | diagnostic hit@3 | pièges de fréquence hit@1 |" in french
    assert "0 of 22" in english and "0 sur 22" in french
    assert "0 of 18 (95% upper bound 3/18 = 0.167)" in english
    assert "0 sur 12 (borne supérieure de 3/12 = 0,250 à 95 %)" in french
    assert "4 frequency traps" in english and "4 pièges de fréquence" in french
    report = MaintEvalReport.model_validate_json(
        (run_dir / f"{OTHER_MODEL}.json").read_text(encoding="utf-8")
    )
    bm25 = f"hit@1 {report.metrics['bm25_hit_at_1']:.3f}"
    assert bm25 in english and bm25.replace(".", ",") in french
    for text in (english, french):
        for key in ("dataset_sha256", "erp_sha256", "gmao_sha256", "vocabulary_sha256"):
            assert report.config[key] in text, key
        for key in ("thresholds_sha256", "ops_thresholds_sha256"):
            assert f"`{key}` `{report.config[key]}`" in text, key
        assert registry.registry_sha256() in text
        assert f"`{DEFAULT_MODEL}` k = 3, `{OTHER_MODEL}` k = 1" in text
        assert "`maint-full-fake`" in text
    assert "Ollama version: fake." in english
    assert "Version d'Ollama : fake." in french
    assert "Generated by `alverno maint bench`" in english
    assert "Généré par `alverno maint bench`" in french


def test_the_readme_changes_only_in_the_maint_blocks(bench_run: BenchRun) -> None:
    after = bench_run.readme.read_text(encoding="utf-8")
    assert after != bench_run.before
    assert outside_maint(after) == outside_maint(bench_run.before)
    for family in OTHER_FAMILIES:
        for lang in ("FR", "EN"):
            name = f"{family}:{lang}"
            assert block(after, name) == block(bench_run.before, name), name
    assert f"| `{DEFAULT_MODEL}` | 3 | ✅ ✅ ✅ |" in block(after, MAINT_EN)
    assert "| Modèle | k | porte |" in block(after, MAINT_FR)
    assert "Generated by `alverno maint bench`" in block(after, MAINT_EN)
    assert "Généré par `alverno maint bench`" in block(after, MAINT_FR)
    assert "\r" not in after and after.endswith("\n")


def test_no_gold_value_reaches_any_output(bench_run: BenchRun) -> None:
    assert bench_run.canaries, "gold:no_canary"
    (run_dir,) = run_dirs(bench_run.out)
    assert canary_hit_files([run_dir, bench_run.readme], bench_run.canaries) == []
    assert output_hits(bench_run.result, bench_run.root / "output", bench_run.canaries) == []


# --------------------------------------------------------------------------- refusals
def test_a_blocker_leaves_the_readme_untouched(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = run_maint_eval

    # The wrapper forwards the CLI's own call unchanged: any argument shape.
    def with_errors(*args: Any, **kwargs: Any) -> MaintEvalReport:
        report = real(*args, **kwargs)
        report.metrics["error_rate"] = 0.5
        return report

    monkeypatch.setattr(cli, "run_maint_eval", with_errors)
    before = env.read_bytes()
    out = env.parent / "reports"
    # A short run: the blocker is checked whatever the preconditions.
    result = bench(out, "--models", OTHER_MODEL, "--limit", "2", "--readme", str(env))
    assert result.exit_code == 1, result.output[-2000:]
    assert f"transport errors or persisted canaries for {OTHER_MODEL}" in result.stderr
    assert "not updated" in result.stderr
    assert env.read_bytes() == before
    (run_dir,) = run_dirs(out)
    assert RUN_DIR.fullmatch(run_dir.name), run_dir.name


def _planted() -> tuple[Canary, str]:
    """A gold canary of the 3b set and the label the scan prints for it."""
    loaded = load_canaries(MAINT_GOLD)
    typed = [c for c in loaded if c.label != UNTYPED_LABEL and c.canary.type is not PIType.ADRESSE]
    if typed:
        return typed[0].canary, typed[0].label
    assert loaded, "gold:no_canary"
    return loaded[0].canary, UNTYPED_LABEL


def test_a_planted_canary_quarantines_the_run_folder(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = run_maint_eval
    planted, label = _planted()

    # The wrapper forwards the CLI's own call unchanged: any argument shape.
    def planting(*args: Any, **kwargs: Any) -> MaintEvalReport:
        report = real(*args, **kwargs)
        out_dir = kwargs["out_dir"]
        assert isinstance(out_dir, Path)
        (out_dir / "planted.txt").write_text(f"x {planted.value} x\n", encoding="utf-8")
        return report

    monkeypatch.setattr(cli, "run_maint_eval", planting)
    before = env.read_bytes()
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--limit", "2", "--readme", str(env))
    assert result.exit_code == 1, result.output[-2000:]
    assert env.read_bytes() == before
    (run_dir,) = run_dirs(out)
    assert run_dir.name.endswith(".quarantine")
    assert RUN_DIR.fullmatch(run_dir.name.removesuffix(".quarantine"))
    # Renamed, never deleted: the planted file and the summaries are all still there.
    assert (run_dir / "planted.txt").is_file() and (run_dir / "summary.md").is_file()
    assert re.search(rf"planted\.txt: {re.escape(label)}$", result.stdout, re.M)
    assert "canary hit(s) in the run folder" in result.stderr
    assert "reports moved to" in result.stderr
    printed = planted.value in result.output
    assert not printed, label


def test_a_limited_run_never_reaches_the_readme(env: Path) -> None:
    before = env.read_bytes()
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--limit", "3", "--readme", str(env))
    assert result.exit_code == 1, result.output[-2000:]
    assert "gate preconditions not met" in result.stderr
    assert f"FAIL {OTHER_MODEL} r1: n_items = 3.0" in result.stderr
    assert env.read_bytes() == before
    without = bench(env.parent / "plain", "--models", OTHER_MODEL, "--limit", "3")
    assert without.exit_code == 0, without.output[-2000:]


def test_a_set_without_canary_never_reaches_the_readme(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Replaces the canary reader whatever its arguments: nothing to scan with.
    def none(*args: Any, **kwargs: Any) -> list[Canary]:
        return []

    monkeypatch.setattr(cli, "maint_file_canaries", none)
    before = env.read_bytes()
    # A short run: a missing canary list is reported before the preconditions.
    args = ["--models", OTHER_MODEL, "--limit", "2", "--readme", str(env)]
    result = bench(env.parent / "reports", *args)
    assert result.exit_code == 1, result.output[-2000:]
    assert "no canary to check" in result.stderr
    assert env.read_bytes() == before


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--models", " , "], "give one or more models"),
        (["--models", "modele-x:cloud"], "cloud model"),
        (["--models", OTHER_MODEL, "--repeats", "0"], "--repeats"),
        (["--models", OTHER_MODEL, "--dataset", "absent.jsonl"], "file.missing"),
    ],
    ids=["no_model", "cloud_model", "zero_repeats", "no_dataset"],
)
def test_usage_errors_exit_2_before_any_file(env: Path, args: list[str], message: str) -> None:
    out = env.parent / "reports"
    result = bench(out, *args)
    assert result.exit_code == 2
    assert message in result.output
    assert not out.exists()


@pytest.mark.parametrize("marker", [MAINT_FR, MAINT_EN])
def test_a_readme_without_the_maint_markers_exits_2_before_any_run(env: Path, marker: str) -> None:
    text = env.read_text(encoding="utf-8").replace(f"<!-- {marker} -->\n", "")
    env.write_text(text, encoding="utf-8", newline="\n")
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--readme", str(env))
    assert result.exit_code == 2
    assert marker in result.stderr
    assert not out.exists()


def test_the_bench_reads_no_ollama_version_in_fake_mode(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Replaces the version reader whatever its arguments.
    def forbidden(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("network:version")

    monkeypatch.setattr(cli, "ollama_server_version", forbidden)
    result = bench(env.parent / "reports", "--models", OTHER_MODEL, "--limit", "1")
    assert result.exit_code == 0, result.output[-2000:]


# --------------------------------------------------------------------------- units
def test_the_safety_counts_are_the_22_zero_bounds_of_the_profile() -> None:
    profile = load_profiles(THRESHOLDS)["maint-full-ollama"]
    zero = {k for k, v in profile.max.items() if v == 0 and not k.startswith("n_")}
    assert len(MAINT_SAFETY_COUNTS) == 22 == len(set(MAINT_SAFETY_COUNTS))
    assert set(MAINT_SAFETY_COUNTS) == zero - {"error_rate", "false_block_rate"}
    # One list for 3a and 3b, shared by both benches.
    assert MAINT_SAFETY_COUNTS == SVC_SAFETY_COUNTS == OPS_SAFETY_COUNTS


def test_the_preconditions_are_those_of_the_gold_set() -> None:
    gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))
    assert tuple(maint_preconditions(load_maint_gold(MAINT_GOLD, gmao))) == MAINT_PRECONDITIONS
    profile = load_profiles(THRESHOLDS)["maint-full-ollama"]
    for key in MAINT_PRECONDITIONS:
        assert profile.min[key] == profile.max[key], key


def test_the_service_columns_are_the_bounded_metrics_of_the_profile() -> None:
    profile = load_profiles(THRESHOLDS)["maint-full-ollama"]
    service = {k for k in profile.min if not k.startswith("n_") and profile.min[k] < 1.0}
    assert service == {
        "diagnosis_hit_at_1",
        "diagnosis_hit_at_3",
        "frequency_trap_hit_at_1",
        "citation_validity_rate",
    }


def _report(**metrics: float) -> MaintEvalReport:
    return MaintEvalReport(
        mode="full",
        backend="fake",
        created_at="2026-11-02T00:00:00+00:00",
        n_items=0,
        n_answerable=0,
        n_traps=0,
        metrics=dict(metrics),
        items=[],
    )


def test_blockers_and_preconditions_fail_closed(tmp_path: Path) -> None:
    clean = MaintBenchRow("a", (_report(error_rate=0.0, artifact_canary_hits=0.0),), (True,))
    missing = MaintBenchRow("b", (_report(error_rate=0.0),), (True,))
    errored = MaintBenchRow(
        "c",
        (
            _report(error_rate=0.0, artifact_canary_hits=0.0),
            _report(error_rate=0.0, artifact_canary_hits=1.0),
        ),
        (True, False),
    )
    assert maint_bench_blockers([clean, missing, errored]) == ["b", "c"]
    assert errored.passed is False and clean.passed is True
    assert MaintBenchRow("d", (_report(),), (None,)).passed is None
    assert MaintBenchRow("e", (), ()).passed is None
    assert maint_unmet_preconditions([clean], tmp_path / "absent.yaml")[0].startswith(
        "thresholds not readable"
    )
    unmet = maint_unmet_preconditions([clean], THRESHOLDS)
    assert len(unmet) == len(MAINT_PRECONDITIONS)
    assert unmet[0] == "a r1: n_items = None (min 40.0, max 40.0)"
    assert "a r1: n_freq_traps = None (min 4.0, max 4.0)" in unmet


def test_the_service_cells_are_the_median_of_the_repetitions() -> None:
    reports = tuple(
        _report(diagnosis_hit_at_1=v, latency_p50_ms=t, prompt_tokens=p, completion_tokens=1.0)
        for v, t, p in ((0.5, 100.0, 10.0), (0.75, 300.0, 30.0), (0.625, 200.0, 20.0))
    )
    row = MaintBenchRow("a", reports, (True, True, True))
    assert row.k == 3
    assert row.service_value("diagnosis_hit_at_1") == 0.625
    # A service metric missing from one repetition has no median: the cell is 0.
    assert row.service_value("diagnosis_hit_at_3") == 0.0
    assert row.median("latency_p50_ms") == 200.0
    assert row.tokens() == 21
    assert row.nonzero_counts() == list(MAINT_SAFETY_COUNTS)
    empty = MaintBenchRow("e", (), ())
    assert empty.service_value("diagnosis_hit_at_1") == 0.0
    assert empty.median("latency_p50_ms") == 0.0 and empty.tokens() == 0


def test_the_rule_of_three_is_shared_or_per_model() -> None:
    zero = MaintBenchRow("a", (_report(),), (True,))
    other = MaintBenchRow("b", (_report(), _report()), (True, True))
    assert rule_of_three_bound([zero, other], "trap_refusal_rate", "en", maint_counts) == (
        "0 of 0 (no denominator)"
    )
    # A row without a report, or a count the reports do not hold, is unknown, never 0.
    none = MaintBenchRow("c", (), ())
    shown = rule_of_three_bound([zero, none], "trap_refusal_rate", "fr", maint_counts)
    assert shown == "`a` 0 sur 0 (aucun dénominateur) ; `c` inconnu"
    assert rule_of_three_bound([zero], "absent_count", "en", maint_counts) == "unknown"


def test_the_base_row_gates_the_reports_as_measured() -> None:
    report = _report(diagnosis_hit_at_1=0.5)
    row = OpsBenchRow("a", (report,), (True,))
    assert row.gated() == (report,)
    assert row.service_value("diagnosis_hit_at_1") == 0.5


def test_the_table_without_a_report_is_the_header_only() -> None:
    note = OpsRunNote("d.jsonl", "t.yaml", "maint-full-fake", "fake", "0" * 64)
    assert maint_bench_table([], "en", note=note).count("\n") == 2
    lines = maint_bench_markdown([MaintBenchRow("e", (), ())], "fake", "fr", note=note)
    assert lines.startswith("# Banc d'essai de l'entretien")
    assert "| `e` | 0 |" in lines and "Généré par" not in lines


def test_the_run_note_says_missing_for_an_absent_digest() -> None:
    note = OpsRunNote("d.jsonl", "t.yaml", "maint-full-fake", "fake", "0" * 64)
    row = MaintBenchRow("a", (_report(),), (True,))
    english = maint_bench_table([row], "en", note=note)
    french = maint_bench_table([row], "fr", note=note)
    assert "- Dataset `d.jsonl`: missing" in english
    assert "- Jeu `d.jsonl` : absente" in french
    assert "Measured on 2026-11-0" in english
