"""``alverno service bench`` (increment I8, ADR 0015 « Banc d'essai » and « Répétitions »).

Every bench here runs ``--fake`` on the 3a gold set: no model, no network. The default
chat model runs ``--repeats`` times (3 by default) and any other model once; each
repetition is gated on its own counts and on the median of the service metrics. The
README is rewritten in the RESULTS-SVC blocks only, and only without a blocker, a canary
in the run folder or an unmet precondition; a canary hit renames the run folder
``<folder>.quarantine``. Assertions cite file names, labels and codes, never a request, a
draft, a contact or a canary value.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner, Result

from alverno_lab import cli
from alverno_lab.cli import _bench_stems, app
from alverno_lab.config import Settings
from alverno_lab.connectors import SimErp
from alverno_lab.evals.canaries import UNTYPED_LABEL, load_canaries
from alverno_lab.evals.service import load_svc_gold, svc_preconditions
from alverno_lab.evals.service_bench import (
    SVC_PRECONDITIONS,
    SVC_SAFETY_COUNTS,
    SvcBenchRow,
    canary_hit_files,
    ollama_server_version,
    svc_bench_blockers,
    svc_unmet_preconditions,
)
from alverno_lab.evals.service_eval import SvcEvalReport, run_service_eval, service_file_canaries
from alverno_lab.evals.thresholds import load_profiles
from alverno_lab.ops_pipeline import ops_quarantine
from alverno_lab.privacy import Canary, PIType
from alverno_lab.tools import registry

from .conftest import EVAL_DATA, HR_DATA, REPO_ROOT
from .ops_privacy_helpers import ops_privacy_ctx

runner = CliRunner()
OPS = EVAL_DATA / "ops"
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"
README = REPO_ROOT / "README.md"
DEFAULT_MODEL = str(Settings.model_fields["chat_model"].default)
OTHER_MODEL = "fake-ops-b"
RUN_DIR = re.compile(r"bench-svc-fake-\d{8}-\d{6}")
SVC_FR, SVC_EN = "RESULTS-SVC:FR", "RESULTS-SVC:EN"


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
        "ALVERNO_SVC_EVAL_PATH": SVC_GOLD,
        "ALVERNO_THRESHOLDS_PATH": THRESHOLDS,
        "ALVERNO_ACCESS_LOG_DIR": root / "access-cli",
    }
    for name, value in env.items():
        mp.setenv(name, str(value))
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        mp.delenv(name, raising=False)
    mp.chdir(root)


def bench(out: Path, *args: str) -> Result:
    return runner.invoke(app, ["service", "bench", "--fake", "--out", str(out), *args])


def run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir()) if out.is_dir() else []


def outside_svc(text: str) -> list[str]:
    """The README cut at the four RESULTS-SVC marker lines, the blocks left out."""
    lines = text.split("\n")
    bare = [line.strip() for line in lines]
    cuts = [bare.index(f"<!-- {name} -->") for name in (SVC_FR, f"/{SVC_FR}", SVC_EN, f"/{SVC_EN}")]
    a, b, c, d = cuts
    return ["\n".join(lines[: a + 1]), "\n".join(lines[b : c + 1]), "\n".join(lines[d:])]


def block(text: str, name: str) -> str:
    return text.split(f"<!-- {name} -->")[1].split(f"<!-- /{name} -->")[0]


def leaked(paths: list[Path], canaries: list[Canary]) -> list[tuple[Path, str]]:
    return canary_hit_files(paths, canaries)


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
    root = tmp_path_factory.mktemp("svc_bench")
    readme = root / "README.md"
    shutil.copyfile(README, readme)
    before = readme.read_text(encoding="utf-8")
    out = root / "reports"
    with pytest.MonkeyPatch.context() as mp:
        _apply_env(mp, root)
        result = bench(out, "--models", f"{DEFAULT_MODEL},{OTHER_MODEL}", "--readme", str(readme))
        canaries = service_file_canaries(Settings().for_module("service"))
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
    assert list((run_dir / "access").glob("service-*.jsonl"))
    traces = {p.name for p in (run_dir / "traces").iterdir()}
    assert traces == {f"{stem}-r{i}.jsonl" for i in (1, 2, 3)} | {f"{OTHER_MODEL}.jsonl"}
    # The CLI access log of `service ask` is never touched by the bench.
    assert not (bench_run.root / "access-cli").exists()


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
            assert report["label"] == model["model"]
            assert report["config"]["chat_model"] == model["model"]
    note = summary["note"]
    assert note["ollama_version"] == "fake"
    assert note["profile"] == "svc-full-fake"
    assert note["registry_sha256"] == registry.registry_sha256()


def test_the_summaries_hold_the_table_and_the_run_note(bench_run: BenchRun) -> None:
    (run_dir,) = run_dirs(bench_run.out)
    english = (run_dir / "summary.md").read_text(encoding="utf-8")
    french = (run_dir / "summary.fr.md").read_text(encoding="utf-8")
    assert f"| `{DEFAULT_MODEL}` | 3 | ✅ ✅ ✅ |" in english
    assert f"| `{OTHER_MODEL}` | 1 | ✅ |" in french
    assert "0 of 22" in english and "0 sur 22" in french
    assert "0 of 19 (95% upper bound 3/19 = 0.158)" in english
    assert "0 sur 14 (borne supérieure de 3/14 = 0,214 à 95 %)" in french
    report = SvcEvalReport.model_validate_json(
        (run_dir / f"{OTHER_MODEL}.json").read_text(encoding="utf-8")
    )
    for text in (english, french):
        for key in ("dataset_sha256", "erp_sha256", "gmao_sha256", "vocabulary_sha256"):
            assert report.config[key] in text, key
        for key in ("thresholds_sha256", "ops_thresholds_sha256"):
            assert f"`{key}` `{report.config[key]}`" in text, key
        assert registry.registry_sha256() in text
        assert f"`{DEFAULT_MODEL}` k = 3, `{OTHER_MODEL}` k = 1" in text
    assert "Ollama version: fake." in english
    assert "Version d'Ollama : fake." in french


def test_the_readme_changes_only_in_the_svc_blocks(bench_run: BenchRun) -> None:
    after = bench_run.readme.read_text(encoding="utf-8")
    assert outside_svc(after) == outside_svc(bench_run.before)
    assert f"| `{DEFAULT_MODEL}` | 3 | ✅ ✅ ✅ |" in block(after, SVC_EN)
    assert "| Modèle | k | porte |" in block(after, SVC_FR)
    assert "Generated by `alverno service bench`" in block(after, SVC_EN)
    assert "Généré par `alverno service bench`" in block(after, SVC_FR)
    assert "\r" not in after and after.endswith("\n")


def test_no_gold_value_reaches_any_output(bench_run: BenchRun) -> None:
    assert bench_run.canaries, "gold:no_canary"
    (run_dir,) = run_dirs(bench_run.out)
    assert leaked([run_dir, bench_run.readme], bench_run.canaries) == []
    assert output_hits(bench_run.result, bench_run.root / "output", bench_run.canaries) == []


# --------------------------------------------------------------------------- refusals
def test_a_blocker_leaves_the_readme_untouched(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = run_service_eval

    # The wrapper forwards the CLI's own call unchanged: any argument shape.
    def with_errors(*args: Any, **kwargs: Any) -> SvcEvalReport:
        report = real(*args, **kwargs)
        report.metrics["error_rate"] = 0.5
        return report

    monkeypatch.setattr(cli, "run_service_eval", with_errors)
    before = env.read_bytes()
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--readme", str(env))
    assert result.exit_code == 1, result.output[-2000:]
    assert f"transport errors or persisted canaries for {OTHER_MODEL}" in result.stderr
    assert "not updated" in result.stderr
    assert env.read_bytes() == before
    (run_dir,) = run_dirs(out)
    assert RUN_DIR.fullmatch(run_dir.name), run_dir.name


def test_a_planted_canary_quarantines_the_run_folder(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = run_service_eval
    typed = [
        c
        for c in load_canaries(SVC_GOLD)
        if c.label != UNTYPED_LABEL and c.canary.type is not PIType.ADRESSE
    ]
    assert typed, "gold:no_typed_canary"
    planted = typed[0]

    # The wrapper forwards the CLI's own call unchanged: any argument shape.
    def planting(*args: Any, **kwargs: Any) -> SvcEvalReport:
        report = real(*args, **kwargs)
        out_dir = kwargs["out_dir"]
        assert isinstance(out_dir, Path)
        (out_dir / "planted.txt").write_text(f"x {planted.canary.value} x\n", encoding="utf-8")
        return report

    monkeypatch.setattr(cli, "run_service_eval", planting)
    before = env.read_bytes()
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--readme", str(env))
    assert result.exit_code == 1, result.output[-2000:]
    assert env.read_bytes() == before
    (run_dir,) = run_dirs(out)
    assert run_dir.name.endswith(".quarantine")
    assert RUN_DIR.fullmatch(run_dir.name.removesuffix(".quarantine"))
    assert (run_dir / "planted.txt").is_file() and (run_dir / "summary.md").is_file()
    assert re.search(rf"planted\.txt: {re.escape(planted.label)}$", result.stdout, re.M)
    assert "canary hit(s) in the run folder" in result.stderr
    assert "reports moved to" in result.stderr
    printed = planted.canary.value in result.output
    assert not printed, planted.label


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


def test_a_readme_without_the_svc_markers_exits_2_before_any_run(env: Path) -> None:
    text = env.read_text(encoding="utf-8").replace(f"<!-- {SVC_EN} -->\n", "")
    env.write_text(text, encoding="utf-8", newline="\n")
    out = env.parent / "reports"
    result = bench(out, "--models", OTHER_MODEL, "--readme", str(env))
    assert result.exit_code == 2
    assert SVC_EN in result.stderr
    assert not out.exists()


# --------------------------------------------------------------------------- units
def test_the_safety_counts_are_the_22_zero_bounds_of_the_profile() -> None:
    profile = load_profiles(THRESHOLDS)["svc-full-ollama"]
    zero = {k for k, v in profile.max.items() if v == 0 and not k.startswith("n_")}
    assert len(SVC_SAFETY_COUNTS) == 22 == len(set(SVC_SAFETY_COUNTS))
    assert set(SVC_SAFETY_COUNTS) == zero - {"error_rate", "false_block_rate"}


def test_the_preconditions_are_those_of_the_gold_set() -> None:
    erp = SimErp.from_yaml(OPS / "erp.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))
    assert tuple(svc_preconditions(load_svc_gold(SVC_GOLD, erp))) == SVC_PRECONDITIONS
    profile = load_profiles(THRESHOLDS)["svc-full-ollama"]
    for key in SVC_PRECONDITIONS:
        assert profile.min[key] == profile.max[key], key


def _report(**metrics: float) -> SvcEvalReport:
    return SvcEvalReport(
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
    clean = SvcBenchRow("a", (_report(error_rate=0.0, artifact_canary_hits=0.0),), (True,))
    missing = SvcBenchRow("b", (_report(error_rate=0.0),), (True,))
    errored = SvcBenchRow(
        "c",
        (
            _report(error_rate=0.0, artifact_canary_hits=0.0),
            _report(error_rate=0.1, artifact_canary_hits=0.0),
        ),
        (True, False),
    )
    assert svc_bench_blockers([clean, missing, errored]) == ["b", "c"]
    assert errored.passed is False and clean.passed is True
    assert SvcBenchRow("d", (_report(),), (None,)).passed is None
    assert svc_unmet_preconditions([clean], tmp_path / "absent.yaml")[0].startswith(
        "thresholds not readable"
    )
    unmet = svc_unmet_preconditions([clean], THRESHOLDS)
    assert len(unmet) == len(SVC_PRECONDITIONS)
    assert unmet[0] == "a r1: n_items = None (min 48.0, max 48.0)"


def test_the_service_cells_are_the_median_of_the_repetitions() -> None:
    reports = tuple(
        _report(key_fact_recall=v, latency_p50_ms=t, prompt_tokens=p)
        for v, t, p in ((0.7, 100.0, 10.0), (0.9, 300.0, 30.0), (0.8, 200.0, 20.0))
    )
    row = SvcBenchRow("a", reports, (True, True, True))
    assert row.k == 3
    assert row.service_value("key_fact_recall") == 0.8
    assert row.median("latency_p50_ms") == 200.0
    assert row.tokens() == 20
    assert row.nonzero_counts() == list(SVC_SAFETY_COUNTS)


def test_bench_stems_never_collide() -> None:
    taken = {"summary", "summary.fr"}
    assert _bench_stems("gemma4:e4b", 3, taken) == [f"gemma4_e4b-r{i}" for i in (1, 2, 3)]
    assert _bench_stems("gemma4/e4b", 1, taken) == ["gemma4_e4b"]
    assert _bench_stems("GEMMA4:E4B", 1, taken) == ["GEMMA4_E4B_"]
    assert _bench_stems("summary", 1, taken) == ["summary_"]
    assert _bench_stems("gemma4_e4b-r2", 1, taken) == ["gemma4_e4b-r2_"]


def test_the_registry_digest_is_stable_and_covers_the_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = registry.registry_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert registry.registry_sha256() == digest
    changed = dict(registry.FLOW_PLANS)
    changed["order_status"] = ()
    monkeypatch.setattr(registry, "FLOW_PLANS", MappingProxyType(changed))
    assert registry.registry_sha256() != digest


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200, json={"version": "0.24.0"}), "0.24.0"),
        (httpx.Response(200, json={"version": "<script>"}), "unknown"),
        (httpx.Response(200, json=["0.24.0"]), "unknown"),
        (httpx.Response(200, text="not json"), "unknown"),
        (httpx.Response(500, json={"version": "0.24.0"}), "unknown"),
    ],
    ids=["ok", "odd_value", "not_object", "not_json", "http_500"],
)
def test_the_ollama_version_is_read_without_quoting_the_server(
    response: httpx.Response, expected: str
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return response

    version = ollama_server_version("http://localhost:11434/", transport=_transport(handler))
    assert version == expected
    assert seen == ["/api/version"]


def test_an_unreachable_ollama_gives_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert ollama_server_version("http://localhost:11434", transport=_transport(handler)) == (
        "unknown"
    )


def test_the_bench_reads_no_ollama_version_in_fake_mode(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Replaces the version reader whatever its arguments.
    def forbidden(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("network:version")

    monkeypatch.setattr(cli, "ollama_server_version", forbidden)
    result = bench(env.parent / "reports", "--models", OTHER_MODEL, "--limit", "1")
    assert result.exit_code == 0, result.output[-2000:]
