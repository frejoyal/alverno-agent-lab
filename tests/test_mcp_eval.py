"""Evaluation of the MCP server (profile mcp-fake, ADR 0020) and its negative controls.

The full run passes the pre-registered gate. Each control turns one barrier off on a
small copy of the sets and must make its metric fail; a control that does not would mean
a blind metric. Reports never hold a question.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from alverno_lab.cli import app
from alverno_lab.config import Settings
from alverno_lab.evals import mcp_eval
from alverno_lab.evals.mcp_eval import McpEvalReport, run_mcp_eval
from alverno_lab.evals.thresholds import check_thresholds
from alverno_lab.mcp_server import adapters
from alverno_lab.mcp_server import server as srv
from alverno_lab.mcp_server.schemas import AgentAnswer

from .conftest import EVAL_DATA, REPO_ROOT

THRESHOLDS = REPO_ROOT / "evals" / "thresholds.yaml"


def _settings(**update: Any) -> Settings:
    base = {
        "eval_path": EVAL_DATA / "quality_gold.jsonl",
        "hr_eval_path": EVAL_DATA / "hr_gold.jsonl",
        "mcp_eval_path": EVAL_DATA / "mcp_gold.jsonl",
        "thresholds_path": THRESHOLDS,
        "corpus_dir": REPO_ROOT / "data" / "corpus",
        "hr_corpus_dir": REPO_ROOT / "data" / "hr" / "corpus",
    }
    return Settings(**{**base, **update})


def _head(src: Path, dst: Path, n: int, keep: Any = None) -> Path:
    lines = [line for line in src.read_text(encoding="utf-8").splitlines() if line]
    if keep is not None:
        lines = [line for line in lines if keep(json.loads(line))]
    dst.write_text("\n".join(lines[:n]) + "\n", encoding="utf-8")
    return dst


@pytest.fixture(scope="module")
def full_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[McpEvalReport, Path]:
    out = tmp_path_factory.mktemp("mcp-eval")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(REPO_ROOT)  # relative settings paths
        return run_mcp_eval(_settings(), out_dir=out), out


@pytest.fixture
def small(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Two Quality items, two HR items (one with canaries) and all 16 traps.

    The run reads the repository's data through relative settings paths.
    """
    monkeypatch.chdir(REPO_ROOT)
    return _settings(
        eval_path=_head(EVAL_DATA / "quality_gold.jsonl", tmp_path / "q.jsonl", 2),
        hr_eval_path=_head(
            EVAL_DATA / "hr_gold.jsonl",
            tmp_path / "h.jsonl",
            2,
            keep=lambda i: not i["should_refuse"] or i["canaries"],
        ),
    )


# --------------------------------------------------------------------------- full run
def test_the_full_run_passes_the_pre_registered_gate(full_run: tuple[McpEvalReport, Path]) -> None:
    report, _ = full_run
    result = check_thresholds(report, THRESHOLDS)
    assert result.passed, result.failures
    assert report.metrics["n_parity_items"] == 164
    assert report.tools_default == ["quality_search", "quality_ask", "hr_policy_ask"]
    assert report.tools_ops[-2:] == ["order_status", "machine_diagnosis"]


def test_the_report_holds_no_question(full_run: tuple[McpEvalReport, Path]) -> None:
    _, out = full_run
    texts = "\n".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
    for name in ("quality_gold.jsonl", "hr_gold.jsonl"):
        for line in (EVAL_DATA / name).read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["question"] not in texts


def test_the_report_records_the_hashes(full_run: tuple[McpEvalReport, Path]) -> None:
    report, out = full_run
    assert report.dir_thresholds_sha256 == mcp_eval.dir_thresholds_sha256(THRESHOLDS)
    assert set(report.dataset_sha256) == {"quality_gold.jsonl", "hr_gold.jsonl", "mcp_gold.jsonl"}
    assert json.loads((out / "eval-mcp-fake.json").read_text(encoding="utf-8"))["profile"] == (
        "mcp-fake"
    )


def test_the_listener_probe_ignores_socketpair_and_restores_socket() -> None:
    bind = socket.socket.bind
    with mcp_eval.listener_probe() as calls:
        a, b = socket.socketpair()
        a.close()
        b.close()
        assert calls == []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.close()
    assert calls == ["bind", "listen"]
    assert socket.socket.bind is bind


# --------------------------------------------------------------------------- controls
def test_a_write_tool_in_the_registry_is_counted(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_write = srv.ToolSpec("approve", "", srv.QualityAskInput, "question", srv._quality)
    monkeypatch.setattr(srv, "BASE_TOOLS", (*srv.BASE_TOOLS, fake_write))
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["exposed_write_tools"] > 0


def test_ignoring_the_option_exposes_the_module_3_tools(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapters.Backends, "ops_enabled", property(lambda self: True))
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["ops_tools_exposed_by_default"] > 0


def test_a_server_that_listens_is_counted(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = srv.build_server

    def http_like(*args: Any, **kwargs: Any) -> Any:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.close()
        return build(*args, **kwargs)

    monkeypatch.setattr(mcp_eval, "build_server", http_like)
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["network_listeners"] > 0


def test_an_adapter_that_rewrites_the_answer_breaks_parity(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rewrite(self: Any, question: str) -> AgentAnswer:
        result = self.quality.ask(question)
        return AgentAnswer(
            answer=result.answer + " (reformulé)",
            refused=result.refused,
            refusal_reason=result.refusal_reason,
            citations=list(result.citations),
        )

    monkeypatch.setattr(adapters.Backends, "quality_ask", rewrite)
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["parity_mismatches"] > 0


def test_removing_the_input_rules_lets_traps_reach_the_backend(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(srv, "check_text", lambda text, max_chars: True)
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["protocol_trap_reached_backend"] > 0
    assert report.metrics["protocol_trap_rejection_rate"] < 1


def test_a_question_copied_into_an_error_is_counted(
    small: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = srv.call_tool

    def leaky(backends: Any, settings: Settings, name: str, arguments: Any) -> Any:
        result = call(backends, settings, name, arguments)
        if result.is_error:
            result.structured_content = {**(result.structured_content or {}), "args": arguments}
        return result

    monkeypatch.setattr(srv, "call_tool", leaky)
    report = run_mcp_eval(small, out_dir=tmp_path / "out")
    assert report.metrics["log_content_hits"] > 0


# --------------------------------------------------------------------------- cli
def test_the_eval_command_needs_fake() -> None:
    result = CliRunner().invoke(app, ["mcp", "eval"])
    assert result.exit_code == 2
    assert "--fake" in result.output


def test_ci_runs_the_mcp_gate_before_the_scan() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = "uv run alverno mcp eval --fake --out reports/ci"
    assert workflow.index(step) < workflow.index("privacy scan")
