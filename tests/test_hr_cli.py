"""``alverno hr index`` and ``alverno hr ask`` through typer's CliRunner (spec §6, M11).

The question is read on stdin after the transparency notice; stdout and stderr never hold
a canary, even when a backend fails; exit codes follow module 1 (2 for a user error,
an unavailable access log, a legacy tracing variable or the R14 refusals).
Values are synthetic (data/eval/synthetic_identities.yaml).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner, Result

from alverno_lab import cli, hr_pipeline
from alverno_lab.agents.hr.prompts import FOOTER, HR_POLICY, NOTICE
from alverno_lab.cli import app
from alverno_lab.llm import FakeChat, LLMOutputError
from alverno_lab.ollama import OllamaTimeoutError, OllamaUnavailableError
from alverno_lab.privacy import AccessLogRecord, Canary, canary_in
from alverno_lab.retrieval import HybridRetriever

from .conftest import EVAL_DATA, HR_DATA, REPO_ROOT, case_id
from .hr_support import CANARY_ITEMS, NAME, PHONE, echo_prompt

runner = CliRunner()
VACATIONS = "Combien de semaines de vacances après 3 ans de service ?"
PAY = "Combien gagne le cariste ?"
NAMED = "Je m'appelle Sylvie Gagnon, comment demander mes vacances ?"
PHONE_Q = "Mon numéro de téléphone est le 819-555-0142, comment le changer dans le portail ?"
ALL_CANARIES: list[Canary] = [c for item in CANARY_ITEMS for c in item.canaries]


@pytest.fixture(autouse=True)
def _hr_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the CLI at the repository HR data and a temporary access log."""
    log_dir = tmp_path / "access"
    monkeypatch.setenv("ALVERNO_HR_CORPUS_DIR", str(HR_DATA / "corpus"))
    monkeypatch.setenv("ALVERNO_HR_ROLES_PATH", str(HR_DATA / "org_roles.yaml"))
    monkeypatch.setenv("ALVERNO_HR_CONTACTS_PATH", str(HR_DATA / "contacts.yaml"))
    monkeypatch.setenv("ALVERNO_HR_EVAL_PATH", str(EVAL_DATA / "hr_gold.jsonl"))
    monkeypatch.setenv("ALVERNO_HR_INDEX_PATH", str(tmp_path / "hr" / "index.npz"))
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_DIR", str(log_dir))
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        monkeypatch.delenv(name, raising=False)
    return log_dir


def ask(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, ["hr", "ask", "--fake", *args], input=stdin)


def log_records(log_dir: Path) -> list[AccessLogRecord]:
    return [
        AccessLogRecord.model_validate_json(line)
        for path in sorted(log_dir.glob("hr-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def assert_no_canary(result: Result, canaries: list[Canary] = ALL_CANARIES) -> None:
    for canary in canaries:
        assert not canary_in(canary, result.stdout), canary.type
        assert not canary_in(canary, result.stderr), canary.type


# --------------------------------------------------------------------------- stdin
def test_question_on_stdin_cites_rh005(_hr_env: Path) -> None:
    result = ask(stdin=VACATIONS + "\n")
    assert result.exit_code == 0, result.output
    assert "Sources: RH-005#" in result.stdout
    assert FOOTER in result.stdout
    assert NOTICE in result.stderr
    assert "shell history" not in result.stderr
    (record,) = log_records(_hr_env)
    assert record.decision == "answered" and record.doc_ids == ("RH-005",)


def test_person_targeted_question_is_refused(_hr_env: Path) -> None:
    result = ask(stdin=PAY)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(HR_POLICY.message("person_targeted"))
    assert "[refused: person_targeted]" in result.stdout
    (record,) = log_records(_hr_env)
    assert record.reason == "person_targeted"


def test_notice_is_printed_before_stdin_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    original_echo = typer.echo
    original_read = cli._read_question

    def echo(message: Any = None, *args: Any, **kwargs: Any) -> None:
        if message == NOTICE:
            events.append("notice")
        original_echo(message, *args, **kwargs)

    def read() -> str:
        events.append("read")
        return original_read()

    monkeypatch.setattr(cli.typer, "echo", echo)
    monkeypatch.setattr(cli, "_read_question", read)
    result = ask(stdin=VACATIONS)
    assert result.exit_code == 0, result.output
    assert events == ["notice", "read"]
    assert result.stderr.index(NOTICE) == 0


def test_question_as_argument_warns_about_shell_history() -> None:
    result = ask(VACATIONS)
    assert result.exit_code == 0, result.output
    assert "shell history" in result.stderr
    assert result.stderr.index("shell history") < result.stderr.index(NOTICE)
    assert "Sources: RH-005#" in result.stdout


def test_empty_stdin_is_an_empty_question() -> None:
    result = ask(stdin="")
    assert result.exit_code == 0
    assert "[refused: empty_question]" in result.stdout


def test_json_output_and_trace_hold_no_question(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    result = ask("--json", "--trace", str(trace), stdin=PHONE_Q)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "question" not in payload and payload["pi_types"] == ["telephone"]
    assert payload["answer"].endswith(
        "ont été retirés de votre question avant son traitement ; ils ne sont pas conservés."
    )
    content = trace.read_text(encoding="utf-8")
    assert not canary_in(PHONE, content) and "portail" not in content
    assert_no_canary(result)


def test_stdin_is_decoded_as_utf8_in_a_real_process(tmp_path: Path) -> None:
    env = {
        **{k: v for k, v in __import__("os").environ.items() if not k.startswith("ALVERNO_")},
        "ALVERNO_HR_CORPUS_DIR": str(HR_DATA / "corpus"),
        "ALVERNO_HR_ROLES_PATH": str(HR_DATA / "org_roles.yaml"),
        "ALVERNO_HR_CONTACTS_PATH": str(HR_DATA / "contacts.yaml"),
        "ALVERNO_ACCESS_LOG_DIR": str(tmp_path / "access"),
        "PYTHONIOENCODING": "",
        "PYTHONUTF8": "0",
    }
    done = subprocess.run(
        [sys.executable, "-c", "from alverno_lab.cli import app; app()", "hr", "ask", "--fake"],
        input=(VACATIONS + "\n").encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    out = done.stdout.decode("utf-8")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "Sources: RH-005#" in out
    assert NOTICE in done.stderr.decode("utf-8")


# --------------------------------------------------------------------------- canaries
@pytest.mark.parametrize("question", [i.question for i in CANARY_ITEMS], ids=case_id)
def test_stdout_and_stderr_hold_no_canary(question: str) -> None:
    for args in ([], ["--json"]):
        result = ask(*args, stdin=question)
        assert result.exit_code == 0, result.output
        assert_no_canary(result)


def _fake_chat(monkeypatch: pytest.MonkeyPatch, chat: FakeChat) -> None:
    monkeypatch.setattr(hr_pipeline, "make_chat", lambda *a, **k: chat)


@pytest.mark.parametrize("question", [NAMED, PHONE_Q], ids=["name", "phone"])
def test_failing_chat_backend_exits_2_with_the_class_name_only(
    question: str, monkeypatch: pytest.MonkeyPatch, _hr_env: Path
) -> None:
    _fake_chat(monkeypatch, FakeChat(error=OllamaUnavailableError(f"server echoed: {question}")))
    result = ask(stdin=question)
    assert result.exit_code == 2
    assert "Error: OllamaUnavailableError" in result.stderr
    assert "server echoed" not in result.stderr
    assert_no_canary(result)
    (record,) = log_records(_hr_env)
    assert record.decision == "error"


def test_failing_retrieval_backend_exits_2_with_the_class_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: HybridRetriever, query: str, top_k: int | None = None) -> list[Any]:
        raise OllamaTimeoutError(f"embedding timed out for {query}")

    monkeypatch.setattr(HybridRetriever, "search", fail)
    result = ask(stdin=PHONE_Q)
    assert result.exit_code == 2
    assert result.stderr.strip().endswith("Error: OllamaTimeoutError")
    assert_no_canary(result)


def test_invalid_model_output_is_a_refusal_without_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_chat(monkeypatch, FakeChat(error=LLMOutputError(f"bad output: {NAMED}")))
    result = ask(stdin=NAMED)
    assert result.exit_code == 0
    assert "[refused: llm_error]" in result.stdout
    assert_no_canary(result, [NAME])


def test_a_chat_that_repeats_the_prompt_leaks_nothing_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for item in CANARY_ITEMS:
        _fake_chat(monkeypatch, FakeChat(responder=echo_prompt))
        result = ask(stdin=item.question)
        assert result.exit_code == 0, result.output
        assert_no_canary(result, list(item.canaries))


# --------------------------------------------------------------------------- exit codes
def test_audit_unavailable_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_DIR", str(blocker))
    result = ask(stdin=VACATIONS)
    assert result.exit_code == 2
    assert result.stdout.startswith(HR_POLICY.message("audit_unavailable"))
    assert "Sources:" not in result.stdout
    assert "access log is unavailable" in result.stderr


@pytest.mark.parametrize("name", ["LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"])
def test_legacy_tracing_variable_exits_2(
    name: str, monkeypatch: pytest.MonkeyPatch, _hr_env: Path
) -> None:
    monkeypatch.setenv(name, "true")
    result = ask(stdin=VACATIONS)
    assert result.exit_code == 2
    assert "LegacyTracingError" in result.stderr
    assert log_records(_hr_env) == []


@pytest.mark.parametrize(
    ("env", "args"),
    [
        ({"ALVERNO_ALLOW_REMOTE_OLLAMA": "true"}, []),
        ({"ALVERNO_CHAT_MODEL": "gpt-oss:120b-cloud"}, []),
        ({}, ["--model", "kimi-k2:1t-cloud"]),
        ({"ALVERNO_EMBED_MODEL": "embed:cloud"}, []),
    ],
    ids=["remote", "cloud-env", "cloud-option", "cloud-embed"],
)
def test_r14_remote_opt_in_or_cloud_model_exits_2(
    env: dict[str, str], args: list[str], monkeypatch: pytest.MonkeyPatch, _hr_env: Path
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = ask(*args, stdin=VACATIONS)
    assert result.exit_code == 2, result.output
    assert "RemoteOllamaError" in result.stderr and "R14" in result.stderr
    assert NOTICE not in result.stderr  # refused before the question is read
    assert log_records(_hr_env) == []
    if not args:
        index = runner.invoke(app, ["hr", "index", "--fake"])
        assert index.exit_code == 2 and "R14" in index.stderr


def test_missing_roles_file_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVERNO_HR_ROLES_PATH", str(tmp_path / "missing.yaml"))
    result = ask(stdin=VACATIONS)
    assert result.exit_code == 2
    assert "PrivacyConfigError" in result.stderr


# --------------------------------------------------------------------------- hr index
def test_hr_index_fake_builds_in_memory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["hr", "index", "--fake"])
    assert result.exit_code == 0, result.output
    assert "HR chunks" in result.stdout and "fake mode, not saved" in result.stdout
    assert not (tmp_path / "hr").exists()


def test_hr_index_refuses_a_corpus_that_breaks_the_privacy_rules() -> None:
    quality = REPO_ROOT / "tests" / "fixtures" / "corpus"
    result = runner.invoke(app, ["hr", "index", "--fake", "--corpus", str(quality)])
    assert result.exit_code == 2
    assert "CorpusError" in result.stderr


def test_hr_ask_needs_the_hr_index_in_real_mode() -> None:
    # No Ollama call: the missing index is reported before any embedding.
    result = runner.invoke(app, ["hr", "ask"], input=VACATIONS)
    assert result.exit_code == 2
    assert "IndexNotFoundError" in result.stderr or "IndexStoreError" in result.stderr
    # The hint names the HR command, not the Quality one.
    assert "alverno hr index" in result.stderr and "run `alverno index`" not in result.stderr


def test_an_unquoted_question_is_read_whole_and_never_echoed_by_click() -> None:
    # Regression: Click rejected the extra words and printed them on stderr.
    result = ask(
        "Je", "m'appelle", "Sylvie", "Gagnon,", "comment", "demander", "mes", "vacances", "?"
    )
    assert result.exit_code == 0, result.output
    assert "shell history" in result.stderr
    assert "Sources: RH-005#" in result.stdout
    assert_no_canary(result, [NAME])
    assert "Sylvie" not in result.stderr and "Gagnon" not in result.stderr


def test_the_notice_states_the_configured_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_RETENTION_DAYS", "30")
    result = ask(stdin=VACATIONS)
    assert result.exit_code == 0, result.output
    assert "conservé 30 jours" in result.stderr and "90 jours" not in result.stderr


def test_a_model_name_the_log_cannot_hold_exits_2_before_anything_runs(_hr_env: Path) -> None:
    name = "hf.co/some-organisation/Some-Model-Name-8B-Instruct-GGUF:Q4_K_M"
    result = ask("--model", name, stdin=VACATIONS)
    assert result.exit_code == 2
    assert "PrivacyConfigError" in result.stderr and "not loggable" in result.stderr
    assert "access log is unavailable" not in result.stderr
    assert not _hr_env.exists()  # nothing ran, nothing was logged


# --------------------------------------------------------------------------- tracebacks
def test_hr_app_never_shows_locals_in_tracebacks(tmp_path: Path) -> None:
    assert cli.hr_app.pretty_exceptions_show_locals is False
    assert all(
        a.pretty_exceptions_show_locals is False
        for a in vars(cli).values()
        if isinstance(a, typer.Typer)
    )
    # An unexpected error inside ``ask``: the traceback must not print the question,
    # which is a local variable of several frames.
    script = (
        "import sys\n"
        "from alverno_lab.agents.hr.graph import HrAgent\n"
        "def boom(self, question, **kwargs):\n"
        "    held = question\n"
        "    raise RuntimeError('boom')\n"
        "HrAgent.ask = boom\n"
        "from alverno_lab.cli import app\n"
        "app()\n"
    )
    path = tmp_path / "crash.py"
    path.write_text(script, encoding="utf-8")
    env = {
        **{k: v for k, v in __import__("os").environ.items() if not k.startswith("ALVERNO_")},
        "ALVERNO_HR_CORPUS_DIR": str(HR_DATA / "corpus"),
        "ALVERNO_HR_ROLES_PATH": str(HR_DATA / "org_roles.yaml"),
        "ALVERNO_HR_CONTACTS_PATH": str(HR_DATA / "contacts.yaml"),
        "ALVERNO_ACCESS_LOG_DIR": str(tmp_path / "access"),
        "COLUMNS": "200",
    }
    done = subprocess.run(
        [sys.executable, str(path), "hr", "ask", "--fake"],
        input=PHONE_Q.encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    err = done.stderr.decode("utf-8", "replace")
    assert done.returncode == 1
    assert "RuntimeError" in err
    assert not canary_in(PHONE, err) and not canary_in(PHONE, done.stdout.decode("utf-8"))
