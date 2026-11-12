"""Hosted reference of module 4 (ADR 0023), with a simulated command line only.

No test calls ``claude``: the sender is simulated, and the one test of ``cli_sender``
replaces ``subprocess.run``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel
from typer.testing import CliRunner

from alverno_lab.cli import app
from alverno_lab.evals import hosted_reference as hosted
from alverno_lab.evals.dataset import load_dataset
from alverno_lab.pipeline import build_retriever
from alverno_lab.privacy import PrivacyContext

from .conftest import REPO_ROOT, hr_repo_settings

QUALITY_GOLD = REPO_ROOT / "data" / "eval" / "quality_gold.jsonl"


class Draft(BaseModel):
    answer: str
    citations: list[str]
    confidence: float


class Recorder:
    """Simulated sender that keeps every prompt it receives."""

    def __init__(self) -> None:
        self.sent: list[tuple[list[str], str]] = []

    def __call__(self, argv: Sequence[str], stdin: str) -> str:
        self.sent.append((list(argv), stdin))
        return hosted.simulated_sender(argv, stdin)


def chat(
    ctx: PrivacyContext, sender: Recorder, *, opt_in: bool = True, traps: Sequence[str] = ()
) -> tuple[hosted.HostedChat, hosted.Audit]:
    audit = hosted.Audit()
    model = hosted.HostedChat(
        "claude-haiku",
        ctx=ctx,
        audit=audit,
        trap_questions=traps,
        opt_in=opt_in,
        sender=sender,
    )
    return model, audit


# --------------------------------------------------------------------------- selection
@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), (" TRUE ", True), ("1", False), ("yes", False), ("", False)],
)
def test_the_opt_in_is_the_literal_true(value: str, expected: bool) -> None:
    assert hosted.allowed({hosted.ALLOW_ENV: value}) is expected
    assert hosted.allowed({}) is False


def test_eligible_items_are_the_28_answerable_ones_without_a_spec_source() -> None:
    items = load_dataset(QUALITY_GOLD)
    chosen = hosted.eligible(items)
    assert len(chosen) == 28
    assert not any(i.should_refuse for i in chosen)
    assert not any(d.startswith("SPEC-") for i in chosen for d in i.expected_doc_ids)
    with_traps = hosted.eligible(items, keep_traps=True)
    assert len(with_traps) - len(chosen) == sum(1 for i in items if i.should_refuse)


def test_spec_detection_targets_extract_headers_not_mentions() -> None:
    assert hosted.has_spec("[QP-001#01] titre\ntexte\n\n[SPEC-AGN-01#03] Spécification")
    assert not hosted.has_spec("[QP-001#01] Appliquer la SPEC-AGN-01 du client.")


# --------------------------------------------------------------------------- command line
def test_the_command_line_offers_no_tool_and_keeps_nothing() -> None:
    argv = hosted.claude_argv("haiku", "système", Draft.model_json_schema())
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--model") + 1] == "haiku"
    for flag in ("--safe-mode", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    for flag in ("--allowedTools", "--mcp-config", "--dangerously-skip-permissions", "--bare"):
        assert flag not in argv
    assert json.loads(argv[argv.index("--json-schema") + 1])["required"]


def test_cli_sender_runs_in_an_empty_folder_without_paid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs, argv=argv, files=list(Path(kwargs["cwd"]).iterdir()))
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(hosted.subprocess, "run", fake_run)
    assert hosted.cli_sender(["claude", "-p"], "question") == "[]"
    assert "ANTHROPIC_API_KEY" not in seen["env"] and "ANTHROPIC_AUTH_TOKEN" not in seen["env"]
    assert seen["files"] == [] and seen["input"] == "question"
    assert Path(seen["cwd"]).resolve() != REPO_ROOT.resolve()


def test_cli_sender_turns_failures_into_transport_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

    monkeypatch.setattr(hosted.subprocess, "run", failing)
    with pytest.raises(hosted.HostedTransportError):
        hosted.cli_sender(["claude"], "")

    def missing(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(hosted.subprocess, "run", missing)
    with pytest.raises(hosted.HostedTransportError):
        hosted.cli_sender(["claude"], "")


def _output(**changes: Any) -> str:
    messages = json.loads(hosted.simulated_sender([], ""))
    for key, value in changes.items():
        target = messages[0] if key in ("apiKeySource", "tools", "mcp_servers") else messages[1]
        target[key] = value
    return json.dumps(messages)


def test_parse_reads_the_structured_output_and_usage() -> None:
    out = hosted.parse_cli_output(_output())
    assert out.structured == {"answer": "", "citations": [], "confidence": 0.0}
    assert (out.model, out.input_tokens, out.output_tokens) == ("simulated", 1, 1)


@pytest.mark.parametrize(
    "changes",
    [{"apiKeySource": "ANTHROPIC_API_KEY"}, {"tools": ["Bash"]}, {"mcp_servers": [{"name": "x"}]}],
)
def test_parse_refuses_a_key_a_tool_or_an_mcp_server(changes: dict[str, Any]) -> None:
    with pytest.raises(hosted.HostedReferenceDisabled):
        hosted.parse_cli_output(_output(**changes))


@pytest.mark.parametrize(
    "stdout",
    ["not json", "{}", "[]", _output(is_error=True), _output(subtype="error_max_turns")],
)
def test_parse_turns_bad_answers_into_transport_errors(stdout: str) -> None:
    with pytest.raises(hosted.HostedTransportError):
        hosted.parse_cli_output(stdout)


def test_parse_without_structured_output_is_an_output_error() -> None:
    with pytest.raises(hosted.LLMOutputError):
        hosted.parse_cli_output(_output(structured_output=None))


# --------------------------------------------------------------------------- barriers
def test_without_the_opt_in_nothing_is_sent(privacy_ctx: PrivacyContext) -> None:
    sender = Recorder()
    model, audit = chat(privacy_ctx, sender, opt_in=False)
    with pytest.raises(hosted.HostedReferenceDisabled):
        model.complete_json("système", "Question : x", Draft)
    assert sender.sent == [] and audit.counts()["hosted_calls_default"] == 0


def test_a_spec_extract_or_personal_information_is_never_sent(privacy_ctx: PrivacyContext) -> None:
    sender = Recorder()
    model, audit = chat(privacy_ctx, sender)
    for prompt in (hosted.CONTROL_SPEC, hosted.CONTROL_PI):
        with pytest.raises(hosted.HostedBlocked):
            model.complete_json("système", prompt, Draft)
    assert sender.sent == []
    assert (audit.spec_skipped, audit.pi_blocked) == (1, 1)
    assert not any(audit.counts().values())


def test_an_allowed_prompt_is_sent_and_audited_clean(privacy_ctx: PrivacyContext) -> None:
    sender = Recorder()
    model, audit = chat(privacy_ctx, sender, traps=["Quelle est la couleur du ciel ?"])
    draft = model.complete_json(
        "système", "[QP-001#01] Appliquer la SPEC-AGN-01 du client.\nQuestion : x", Draft
    )
    assert draft.confidence == 0.0 and len(sender.sent) == 1
    assert sender.sent[0][1].endswith("Question : x")
    assert audit.calls == 1 and not any(audit.counts().values())
    assert model.stats.calls == 1 and model.model_name == "claude-cli:haiku"


def test_each_negative_control_fails_as_planned(privacy_ctx: PrivacyContext) -> None:
    controls = hosted.negative_controls(privacy_ctx, load_dataset(QUALITY_GOLD))
    assert set(controls) == {
        "hosted_pi_sent",
        "hosted_trap_sent",
        "hosted_spec_sent",
        "hosted_calls_default",
    }
    assert all(c["failed_as_planned"] and int(c["count"]) > 0 for c in controls.values())


# --------------------------------------------------------------------------- run
def test_the_run_checks_the_opt_in_before_anything(privacy_ctx: PrivacyContext) -> None:
    sender = Recorder()
    with pytest.raises(hosted.HostedReferenceDisabled):
        hosted.run_reference(
            "claude-haiku",
            [],
            None,
            privacy_ctx,
            min_confidence=0.2,
            sender=sender,
            env={},  # type: ignore[arg-type]
        )
    with pytest.raises(hosted.HostedReferenceDisabled):
        hosted.run_reference(
            "gpt",
            [],
            None,
            privacy_ctx,
            min_confidence=0.2,
            env={hosted.ALLOW_ENV: "true"},  # type: ignore[arg-type]
        )
    assert sender.sent == []


def test_a_simulated_run_sends_only_eligible_prompts(
    privacy_ctx: PrivacyContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    settings = hr_repo_settings()
    retriever, _ = build_retriever(settings, fake=True)
    items = load_dataset(QUALITY_GOLD)
    sender = Recorder()
    run, report = hosted.run_reference(
        "claude-haiku",
        items,
        retriever,
        privacy_ctx,
        min_confidence=0.2,
        sender=sender,
        env={hosted.ALLOW_ENV: "true"},
    )
    assert run.passed and not any(run.counts.values())
    assert run.n_eligible == 28 and run.n_sent_items + run.spec_skipped + run.pi_blocked == 28
    assert run.calls == len(sender.sent) and report.n_traps == 0
    traps = [i.question for i in items if i.should_refuse]
    for _, prompt in sender.sent:
        assert not hosted.has_spec(prompt)
        assert not any(question in prompt for question in traps)
    assert hosted.ReferenceRun.from_json(run.to_json()).to_json() == run.to_json()


# --------------------------------------------------------------------------- command and CI
def test_matrix_refuses_the_reference_without_the_opt_in(tmp_path: Path) -> None:
    out = tmp_path / "m"
    args = ["matrix", "--fake", "--tasks", "T1", "--out", str(out), "--reference"]
    result = CliRunner().invoke(app, [*args, "claude-haiku"])
    assert result.exit_code == 2 and "nothing was run" in result.output
    assert not out.exists()
    assert CliRunner().invoke(app, [*args, "gpt"]).exit_code == 2


def test_ci_never_enables_or_installs_the_hosted_reference() -> None:
    workflows = list((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert hosted.ALLOW_ENV not in text, path.name
        assert "--reference" not in text, path.name
        steps = [
            s.get("run", "")
            for job in yaml.safe_load(text)["jobs"].values()
            for s in job.get("steps", [])
        ]
        assert not any("claude" in step for step in steps), path.name
