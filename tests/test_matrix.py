"""`alverno matrix` (module 4, ADRs 0022 and 0024) on a simulated runner and probe.

No test starts Ollama or a subprocess: the runner writes the files each bench would
write, and the probe answers like /api/ps and /api/generate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from alverno_lab.cli import app
from alverno_lab.evals import matrix as mx

from .conftest import REPO_ROOT

THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"
GIB = mx.GIB
# Retrieval metrics that pass both retrieval-ollama and hr-retrieval-ollama.
RETRIEVAL_OK = {
    "hit_at_1": 1.0,
    "hit_at_3": 1.0,
    "mrr": 1.0,
    "context_recall": 1.0,
    "trap_refusal_accuracy": 1.0,
    "false_answer_rate": 0.0,
    "over_refusal_rate": 0.0,
    "error_rate": 0.0,
    "latency_p50_ms": 10.0,
    "latency_p95_ms": 20.0,
}
HR_RETRIEVAL_OK = {
    **RETRIEVAL_OK,
    "privacy_trap_refusal": 1.0,
    "privacy_reason_accuracy": 1.0,
    "pair_accuracy": 1.0,
    "n_privacy_traps": 20,
    "n_pairs": 16,
    "n_canary_items": 13,
    "n_canary_types": 7,
    "screen_false_block_rate": 0.0,
    "pi_over_refusal": 0.0,
    "backend_canary_hits": 0,
    "artifact_canary_hits": 0,
}


def run(
    model: str = "m",
    *,
    gates: Sequence[bool | None] = (True,),
    total: float | None = 5.0,
    p95: float = 1000.0,
    errors: Sequence[float] = (0.0,),
    codes: Sequence[int] = (0,),
    counts: Sequence[str] = (),
    task: str = "T1",
    metrics: Mapping[str, float] | None = None,
) -> mx.Run:
    return mx.Run(
        task=task,
        model=model,
        stage="screen",
        k=len(gates),
        gates=list(gates),
        error_rates=list(errors),
        exit_codes=list(codes),
        safety_nonzero=list(counts),
        metrics=dict(metrics or {}),
        latency_p50_ms=p95 / 2,
        latency_p95_ms=p95,
        vram_gb=total,
        ram_spill_gb=0.0 if total is not None else None,
    )


# --------------------------------------------------------------------------- hardware
def test_hardware_reads_the_model_entry_of_api_ps() -> None:
    payload = {
        "models": [
            {"name": "qwen3-embedding:0.6b", "size": GIB, "size_vram": GIB},
            {
                "name": "gemma4:e4b",
                "model": "gemma4:e4b",
                "size": 10 * GIB,
                "size_vram": 7 * GIB,
                "context_length": 8192,
            },
        ]
    }
    found = mx.hardware_from_ps(payload, "gemma4:e4b")
    assert found == mx.Hardware(vram_gb=7.0, ram_spill_gb=3.0, context_length=8192)
    assert found is not None and found.total_gb == 10.0


def test_hardware_matches_the_latest_tag_and_misses_unloaded_models() -> None:
    payload = {"models": [{"name": "bge-m3:latest", "size": 2 * GIB, "size_vram": 2 * GIB}]}
    assert mx.hardware_from_ps(payload, "bge-m3") == mx.Hardware(2.0, 0.0, None)
    assert mx.hardware_from_ps(payload, "llama3.2:3b") is None
    assert mx.hardware_from_ps({}, "bge-m3") is None
    assert mx.loaded_models(payload) == ["bge-m3:latest"]


def test_tokens_per_second_come_from_eval_count_and_duration() -> None:
    assert mx.tokens_per_s({"eval_count": 128, "eval_duration": 2_000_000_000}) == 64.0
    assert mx.tokens_per_s({"eval_count": 128, "eval_duration": 0}) is None
    assert mx.tokens_per_s({}) is None


def test_the_probe_reads_ps_and_sends_the_fixed_speed_prompt() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"eval_count": 100, "eval_duration": 1_000_000_000})

    probe = mx.OllamaProbe("http://localhost:11434/", 8192, transport=httpx.MockTransport(handler))
    assert probe.ps() == {"models": []}
    assert probe.speed("gemma4:e4b") == 100.0
    body = seen[0]
    assert body["prompt"] == mx.SPEED_PROMPT and body["stream"] is False
    assert body["options"] == {"num_predict": 128, "num_ctx": 8192, "temperature": 0, "seed": 0}


# --------------------------------------------------------------------------- rule
@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (6.9, 8),
        (7.0, 8),
        (7.01, 12),
        (11.0, 12),
        (15.0, 16),
        (23.0, 24),
        (23.5, None),
        (None, None),
    ],
)
def test_a_model_fits_a_tier_with_one_gigabyte_of_margin(
    total: float | None, expected: int | None
) -> None:
    assert mx.tier(total) == expected


def test_admissibility_needs_every_gate_no_error_no_count_and_a_clean_exit() -> None:
    assert run().admissible
    assert not run(gates=(True, False, True)).admissible
    assert not run(gates=(None,)).admissible
    assert not run(gates=()).admissible
    assert not run(errors=(0.0, 0.02)).admissible
    assert not run(codes=(1,)).admissible
    assert not run(counts=("answer_leak_count",)).admissible


def test_rank_orders_by_memory_then_p95_within_half_a_gigabyte() -> None:
    runs = [
        run("heavy", total=9.0, p95=100),
        run("light-slow", total=4.0, p95=900),
        run("light-fast", total=4.4, p95=300),
        run("mid", total=4.6, p95=50),
        run("failed", total=1.0, gates=(False,)),
        run("unmeasured", total=None),
    ]
    # 4.4 joins the group of 4.0 (within 0.5 GB); 4.6 does not (0.6 GB above 4.0).
    assert [r.model for r in mx.rank(runs)] == ["light-fast", "light-slow", "mid", "heavy"]


def test_safety_counts_are_the_integer_zero_bounds_of_the_profile() -> None:
    counts = mx.safety_counts(THRESHOLDS, "dir-full-ollama")
    assert "contact_exposure" in counts and "log_content_hits" in counts
    assert "error_rate" not in counts and "false_block_rate" not in counts
    assert not any(c.startswith("n_") for c in counts)
    assert mx.safety_counts(THRESHOLDS, "full-ollama") == []
    assert mx.safety_counts(THRESHOLDS, "no-such-profile") == []


class Scripted(mx.Matrix):
    """A matrix whose confirmations are given in advance."""

    def __init__(self, out: Path, confirmations: Mapping[str, mx.Run]) -> None:
        super().__init__(out=out, thresholds=THRESHOLDS)
        self.confirmations = confirmations
        self.asked: list[str] = []

    def run(self, stage: mx.Stage, task: mx.Task, model: str) -> mx.Run:
        self.asked.append(f"{stage}:{model}")
        return self.confirmations[model]


def test_decide_retains_the_next_model_when_the_first_fails_its_confirmation(
    tmp_path: Path,
) -> None:
    screened = [
        run("a", total=3.0, metrics={"key_fact_recall": 0.8}),
        run("b", total=6.0, metrics={"key_fact_recall": 0.95}),
        run("c", total=12.0, metrics={"key_fact_recall": 0.9}),
    ]
    matrix = Scripted(tmp_path, {"a": run("a", gates=(True, False, True)), "b": run("b")})
    decision, checks = mx.decide(matrix, mx.TASKS["T1"], screened)
    assert matrix.asked == ["confirm:a", "confirm:b"]
    assert decision == mx.Decision("T1", "b", 8, 6.0, ["a", "b"], "b")
    assert [c.model for c in checks] == ["a", "b"]


def test_decide_publishes_no_model_when_nothing_is_admissible(tmp_path: Path) -> None:
    screened = [run("a", gates=(False,)), run("b", errors=(0.1,))]
    decision, checks = mx.decide(Scripted(tmp_path, {}), mx.TASKS["T2"], screened)
    assert decision.retained is None and decision.tier_gb is None and checks == []
    text = mx.decisions_table([decision], "fr")
    assert "aucun modèle local recommandé sur ce poste" in text


def test_without_confirmation_nothing_is_retained(tmp_path: Path) -> None:
    matrix = Scripted(tmp_path, {})
    decision, _ = mx.decide(matrix, mx.TASKS["T1"], [run("a")], confirm=False)
    assert decision.retained is None and matrix.asked == []


def test_the_overall_tier_is_that_of_the_heaviest_retained_chat_model() -> None:
    decisions = [
        mx.Decision("T1", "a", 8, 5.0, ["a"], "a"),
        mx.Decision("T2", "b", 12, 9.0, ["b"], "b"),
        mx.Decision("T0", "e", 8, 1.0, ["e"], "e"),
        mx.Decision("T3a", None, None, None, [], None),
    ]
    assert mx.overall_tier(decisions) == 12
    assert mx.overall_tier(decisions[2:]) is None
    assert mx.overall_tier([mx.Decision("T1", "x", None, 30.0, ["x"], "x")]) is None
    assert "12 Go" in mx.overall_line(decisions, "fr")
    assert "12 GB" in mx.overall_line(decisions, "en")


# --------------------------------------------------------------------------- runner
class FakeBenches:
    """Writes what each command writes; records argv and env."""

    def __init__(self, gates: Mapping[str, bool] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.gates = dict(gates or {})

    def __call__(self, argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
        argv = list(argv)
        self.calls.append((argv, dict(env)))
        out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
        if argv[0] in ("index",) or argv[:2] == ["hr", "index"]:
            return 0
        assert out is not None
        model = argv[argv.index("--models") + 1] if "--models" in argv else ""
        metrics = {"error_rate": 0.0, "latency_p50_ms": 500.0, "latency_p95_ms": 900.0}
        if argv[0] == "eval" or argv[:2] == ["hr", "eval"]:
            profile = "retrieval-fake" if argv[0] == "eval" else "hr-retrieval-fake"
            out.mkdir(parents=True, exist_ok=True)
            report = {"metrics": RETRIEVAL_OK if argv[0] == "eval" else HR_RETRIEVAL_OK}
            (out / f"eval-{profile}.json").write_text(json.dumps(report), encoding="utf-8")
            return 0
        bench = out / "bench-fake-20261111-000000"
        bench.mkdir(parents=True, exist_ok=True)
        passed = self.gates.get(model, True)
        if argv[0] in ("bench", "hr"):
            summary: Any = [{"model": model, "gate_passed": passed, "report": {"metrics": metrics}}]
        else:
            k = int(argv[argv.index("--repeats") + 1])
            summary = {
                "models": [
                    {
                        "model": model,
                        "gates": [passed] * k,
                        "reports": [{"metrics": metrics}] * k,
                    }
                ]
            }
        (bench / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return 0


class FakeProbe:
    def __init__(self, sizes: Mapping[str, float]) -> None:
        self.sizes = sizes

    def ps(self) -> dict[str, Any]:
        return {
            "models": [
                {"name": n, "size": int(s * GIB), "size_vram": int(min(s, 7.0) * GIB)}
                for n, s in self.sizes.items()
            ]
        }

    def speed(self, model: str) -> float:
        return 42.0


def matrix_of(tmp_path: Path, runner: FakeBenches, sizes: Mapping[str, float]) -> mx.Matrix:
    return mx.Matrix(
        out=tmp_path / "m",
        thresholds=THRESHOLDS,
        fake=True,
        runner=runner,
        probe=FakeProbe(sizes),  # type: ignore[arg-type]
        echo=lambda _: None,
    )


def test_a_screening_records_gate_hardware_and_speed_then_resumes(tmp_path: Path) -> None:
    runner = FakeBenches()
    matrix = matrix_of(tmp_path, runner, {"gemma4:e4b": 9.0})
    first = matrix.run("screen", mx.TASKS["T1"], "gemma4:e4b")
    assert first.admissible and first.k == 1
    assert (first.vram_gb, first.ram_spill_gb, first.total_gb) == (7.0, 2.0, 9.0)
    assert first.tokens_per_s == 42.0 and first.loaded_before == ["gemma4:e4b"]
    argv, env = runner.calls[0]
    assert argv[:3] == ["bench", "--models", "gemma4:e4b"] and "--fake" in argv
    assert env == {"ALVERNO_CHAT_MODEL": "gemma4:e4b"}
    again = matrix.run("screen", mx.TASKS["T1"], "gemma4:e4b")
    assert len(runner.calls) == 1, "a finished pair is read back, not rerun"
    assert again.to_json() == first.to_json()


def test_t1_and_t2_confirm_with_three_invocations_each_gated(tmp_path: Path) -> None:
    runner = FakeBenches()
    matrix = matrix_of(tmp_path, runner, {"m": 4.0})
    check = matrix.run("confirm", mx.TASKS["T2"], "m")
    assert [c[0][:2] for c in runner.calls] == [["hr", "bench"]] * 3
    outs = [Path(c[0][c[0].index("--out") + 1]).name for c in runner.calls]
    assert outs == ["r1", "r2", "r3"]
    assert check.k == 3 and check.gates == [True, True, True] and check.admissible


def test_module_benches_repeat_the_model_themselves(tmp_path: Path) -> None:
    runner = FakeBenches(gates={"bad": False})
    matrix = matrix_of(tmp_path, runner, {"m": 4.0, "bad": 2.0})
    screen = matrix.run("screen", mx.TASKS["T3a"], "m")
    confirm = matrix.run("confirm", mx.TASKS["T3b"], "m")
    failed = matrix.run("screen", mx.TASKS["T3a"], "bad")
    repeats = [c[0][c[0].index("--repeats") + 1] for c in runner.calls]
    assert repeats == ["1", "3", "1"]
    assert runner.calls[1][1] == {"ALVERNO_CHAT_MODEL": "m"}
    assert screen.admissible and confirm.k == 3 and confirm.admissible
    assert not failed.admissible


def test_a_missing_bench_folder_is_an_error_not_a_pass(tmp_path: Path) -> None:
    def broken(argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
        return 2

    matrix = mx.Matrix(out=tmp_path, thresholds=THRESHOLDS, runner=broken, echo=lambda _: None)
    for task in ("T1", "T3a"):
        result = matrix.run("screen", mx.TASKS[task], "m")
        assert not result.admissible and "exit 2" in result.note


def test_retrieval_builds_its_own_index_in_the_report_folder(tmp_path: Path) -> None:
    runner = FakeBenches()
    matrix = matrix_of(tmp_path, runner, {"bge-m3": 1.2})
    result = matrix.run("screen", mx.TASKS["T0"], "bge-m3")
    assert result.admissible and result.metrics["hit_at_1"] == 1.0
    assert result.tokens_per_s is None, "no speed probe for an embedder"
    folder = tmp_path / "m" / "screen" / "T0" / "bge-m3"
    for argv, env in runner.calls:
        assert env["ALVERNO_EMBED_MODEL"] == "bge-m3"
        for key in ("ALVERNO_INDEX_PATH", "ALVERNO_HR_INDEX_PATH"):
            path = Path(env[key])
            assert path.is_relative_to(folder / "index"), key
            assert ".cache" not in path.parts
        if "eval" in argv:
            assert "--no-gate" in argv
    commands = [" ".join(argv[:2]) for argv, _ in runner.calls]
    assert commands == ["index --fake", "hr index", "eval --mode", "hr eval"]


def test_a_failing_retrieval_set_fails_the_pair(tmp_path: Path) -> None:
    runner = FakeBenches()

    def weak(argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
        code = runner(argv, env, log)
        if argv[:2] == ["hr", "eval"]:
            out = Path(argv[argv.index("--out") + 1])
            report = {"metrics": {**HR_RETRIEVAL_OK, "hit_at_1": 0.1}}
            (out / "eval-hr-retrieval-fake.json").write_text(json.dumps(report), "utf-8")
        return code

    matrix = mx.Matrix(out=tmp_path, thresholds=THRESHOLDS, fake=True, runner=weak)
    assert not matrix.run("screen", mx.TASKS["T0"], "e").admissible


def test_a_canary_reaching_the_hr_retrieval_backend_is_a_safety_count(tmp_path: Path) -> None:
    runner = FakeBenches()

    def leaky(argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
        code = runner(argv, env, log)
        if argv[:2] == ["hr", "eval"]:
            out = Path(argv[argv.index("--out") + 1])
            report = {"metrics": {**HR_RETRIEVAL_OK, "backend_canary_hits": 1}}
            (out / "eval-hr-retrieval-fake.json").write_text(json.dumps(report), "utf-8")
        return code

    matrix = mx.Matrix(out=tmp_path, thresholds=THRESHOLDS, fake=True, runner=leaky)
    result = matrix.run("screen", mx.TASKS["T0"], "e")
    assert result.safety_nonzero == ["hr_backend_canary_hits"] and not result.admissible


def test_direction_reports_are_gated_on_their_medians(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    cli = CliRunner()

    def in_process(argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
        return cli.invoke(app, list(argv), env=dict(env)).exit_code

    matrix = mx.Matrix(out=tmp_path, thresholds=THRESHOLDS, fake=True, limit=4, runner=in_process)
    result = matrix.run("screen", mx.TASKS["T5b"], "fake-model")
    # Four items miss the profile's preconditions: the gate fails and the bench exits 1.
    assert result.gates == [False] and result.exit_codes == [1]
    assert "task_success" in result.metrics and not result.admissible


# --------------------------------------------------------------------------- outputs
def test_publishable_files_leave_the_ignored_folders_out(tmp_path: Path) -> None:
    for name in (
        "matrix.md",
        "screen/T1/m/bench-x/summary.json",
        "screen/T0/e/index/quality/index.npz",
        "screen/T2/m/bench-x/access/a.jsonl",
        "logs/screen-T1-m.log",
        "screen/T0/e/traces/t.jsonl",
        "screen/T2/m/bench-y.quarantine/s.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    shown = [p.relative_to(tmp_path).as_posix() for p in mx.publishable_files(tmp_path)]
    assert shown == ["matrix.md", "screen/T1/m/bench-x/summary.json"]


def test_markdown_uses_french_decimals_and_lists_each_task() -> None:
    screened = {"T1": [run("a", metrics={"key_fact_recall": 0.875})]}
    decisions = [mx.Decision("T1", "a", 8, 5.0, ["a"], "a")]
    fr = mx.matrix_markdown(screened, {"T1": [run("a")]}, decisions, {"measured": "x"}, "fr")
    en = mx.matrix_markdown(screened, {}, decisions, {"measured": "x"}, "en")
    assert "0,875" in fr and "## T1 Qualité" in fr and "Confirmation (k = 3)" in fr
    assert "0.875" in en and "## T1 Quality" in en and "Confirmation (k = 3)" not in en
    assert "| T1 Qualité | `a` | 5,00 | 8 Go |" in fr


# --------------------------------------------------------------------------- command
README = "x\n<!-- RESULTS-MATRIX:FR -->\nold\n<!-- /RESULTS-MATRIX:FR -->\n" + (
    "<!-- RESULTS-MATRIX:EN -->\nold\n<!-- /RESULTS-MATRIX:EN -->\n"
)


def _patched(monkeypatch: pytest.MonkeyPatch, *, errors: float = 0.0) -> None:
    sizes = {"small": 3.0, "big": 9.0, "emb": 0.6}

    def fake_run(self: mx.Matrix, stage: mx.Stage, task: mx.Task, model: str) -> mx.Run:
        k = 1 if stage == "screen" else 3
        result = run(
            model,
            task=task.id,
            gates=(True,) * k,
            errors=(errors,) * k,
            total=sizes[model],
            metrics={task.shown[0]: 0.9},
        )
        result.stage = stage
        return result

    monkeypatch.setattr(mx.Matrix, "run", fake_run)
    monkeypatch.setenv("ALVERNO_EVAL_PATH", str(REPO_ROOT / "data" / "eval" / "quality_gold.jsonl"))


def _invoke(tmp_path: Path, *extra: str) -> Any:
    return CliRunner().invoke(
        app,
        [
            "matrix",
            "--fake",
            "--models",
            "small,big",
            "--heavy-models",
            "",
            "--embed-models",
            "emb",
            "--thresholds",
            str(THRESHOLDS),
            "--out",
            str(tmp_path / "m"),
            *extra,
        ],
    )


def test_matrix_writes_its_files_and_the_readme_when_every_run_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch)
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    result = _invoke(tmp_path, "--readme", str(readme))
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "m" / "matrix.json").read_text(encoding="utf-8"))
    assert [d["retained"] for d in data["decisions"]] == ["small"] * 5 + ["emb"]
    assert data["overall_tier_gb"] == 8
    assert set(data["screen"]) == set(mx.TASKS)
    text = readme.read_text(encoding="utf-8")
    assert "old" not in text and "`small`" in text and "8 Go" in text and "8 GB" in text
    assert (tmp_path / "m" / "matrix.md").is_file() and (tmp_path / "m" / "matrix.en.md").is_file()


def test_a_run_with_errors_blocks_the_readme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patched(monkeypatch, errors=0.02)
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    result = _invoke(tmp_path, "--readme", str(readme))
    assert result.exit_code == 1
    assert "runs with an error" in result.output
    assert readme.read_text(encoding="utf-8") == README


def test_no_confirm_screens_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    result = _invoke(tmp_path, "--no-confirm", "--tasks", "T1")
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "m" / "matrix.json").read_text(encoding="utf-8"))
    assert data["decisions"] == [] and data["confirm"] == {"T1": []}


def test_an_unknown_task_is_a_usage_error(tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--tasks", "T1,T9")
    assert result.exit_code == 2
    assert not (tmp_path / "m").exists()
