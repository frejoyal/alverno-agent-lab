"""``alverno maint ask`` and ``alverno maint eval`` through typer's CliRunner (I9 spec
decision 10).

``ask`` prints the art. 8 notice on stderr, reads the request on the first line of
stdin, answers a diagnosis, or shows a pending work request with its header and the
prepared write, then plays the operator's decisions read on stdin (an edit is
``field=value`` lines up to a line holding a single « . »). The end of the input or
Ctrl-C while a request waits abandons it; an unwritable access log exits 2. ``eval``
gates the report against ``maint-tools-fake`` (or ``maint-full-fake`` with ``--mode
full``): exit 0 on the gold set, 1 when the gate fails (a ``--limit`` run), 2 on a usage
error. ``--mode full --fake`` diagnoses with the ops echo fake (no model). Requests come
from the gold set; assertions cite item ids and codes, never a request or a canary.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from alverno_lab import cli
from alverno_lab.agents.maintenance.notice import MAINT_NOTICE, maint_notice
from alverno_lab.agents.ops import OpsEnvelope, TimelineLog
from alverno_lab.cli import EXECUTED_BANNER, app
from alverno_lab.connectors import SimGmao
from alverno_lab.evals.canaries import load_canaries
from alverno_lab.evals.maint import MaintGoldItem, load_maint_gold
from alverno_lab.ops_pipeline import ops_quarantine
from alverno_lab.privacy import AccessLogError, canary_in
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord

from .conftest import EVAL_DATA, HR_DATA, REPO_ROOT
from .ops_privacy_helpers import ops_privacy_ctx

runner = CliRunner()
OPS = EVAL_DATA / "ops"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
CANARIES = [c.canary for c in load_canaries(MAINT_GOLD)]


@functools.cache
def gold() -> tuple[MaintGoldItem, ...]:
    gmao = SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))
    return tuple(load_maint_gold(MAINT_GOLD, gmao))


@pytest.fixture(autouse=True)
def _ops_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the CLI at the repository data and a temporary access log."""
    log_dir = tmp_path / "access"
    env = {
        "ALVERNO_HR_CORPUS_DIR": HR_DATA / "corpus",
        "ALVERNO_HR_ROLES_PATH": HR_DATA / "org_roles.yaml",
        "ALVERNO_HR_CONTACTS_PATH": HR_DATA / "contacts.yaml",
        "ALVERNO_OPS_ERP_PATH": OPS / "erp.yaml",
        "ALVERNO_OPS_GMAO_PATH": OPS / "gmao.yaml",
        "ALVERNO_OPS_VOCABULARY_PATH": OPS / "vocabulary.yaml",
        "ALVERNO_MAINT_EVAL_PATH": MAINT_GOLD,
        "ALVERNO_THRESHOLDS_PATH": REPO_ROOT / "evals" / "thresholds.yaml",
        "ALVERNO_ACCESS_LOG_DIR": log_dir,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        monkeypatch.delenv(name, raising=False)
    return log_dir


def first(family: str, steps: list[str] | None = None) -> MaintGoldItem:
    for item in gold():
        if item.family == family and (
            steps is None or [s.decision for s in item.approval_script] == steps
        ):
            return item
    raise AssertionError(f"gold:no_{family}")


def ask(stdin: str, *args: str) -> Result:
    return runner.invoke(app, ["maint", "ask", "--fake", *args], input=stdin)


def events(log_dir: Path) -> list[str]:
    return [
        OpsAccessLogRecord.model_validate_json(line).event
        for path in sorted(log_dir.glob("maint-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def assert_no_canary(result: Result) -> None:
    for canary in CANARIES:
        assert not canary_in(canary, result.stdout), canary.type
        assert not canary_in(canary, result.stderr), canary.type


# --------------------------------------------------------------------------- ask
def test_a_diagnosis_is_answered_after_the_notice(_ops_env: Path) -> None:
    item = first("diagnosis")
    result = ask(item.request + "\n")
    assert result.exit_code == 0, item.id
    assert MAINT_NOTICE in result.stderr
    assert result.stderr.index(MAINT_NOTICE) < result.stderr.index("Demande : ")
    assert str(item.asset_id) in result.stdout and "Demande de travail" not in result.stdout
    assert events(_ops_env) == ["answered"]
    assert_no_canary(result)


def test_an_approved_work_request_is_written_once(_ops_env: Path) -> None:
    item = first("work_request", ["approve"])
    result = ask(item.request + "\na\n")
    assert result.exit_code == 0, item.id
    assert "Brouillon préparé par un outil automatisé — à relire" in result.stdout
    assert "Équipement : " in result.stdout and "Ligne : " in result.stdout
    assert "Écriture préparée : gmao.create_work_request" in result.stdout
    assert f"asset_id = {item.asset_id}" in result.stdout
    assert result.stdout.count(EXECUTED_BANNER) == 1
    assert events(_ops_env) == ["drafted", "approved"]
    assert_no_canary(result)


def test_an_edit_of_the_codes_is_re_presented_then_approved(_ops_env: Path) -> None:
    item = first("work_request", ["approve"])
    stdin = f"{item.request}\nm\nfailure_code=INDETERMINE\nligne sans signe\n.\na\n"
    result = ask(stdin)
    assert result.exit_code == 0, item.id
    assert "Ligne ignorée" in result.stderr
    assert "failure_code = INDETERMINE" in result.stdout
    assert events(_ops_env) == ["drafted", "edit_submitted", "re_presented", "approved"]


def test_an_edit_of_the_citations_is_read_as_a_list(_ops_env: Path) -> None:
    item = first("work_request", ["approve"])
    result = ask(f"{item.request}\nm\ncited_wo_ids=OT-0000-0001, OT-0000-0002\n.\n")
    assert result.exit_code == 0, item.id
    assert "[withheld: invalid_args]" in result.stdout
    assert events(_ops_env) == ["drafted", "edit_submitted", "withheld"]


def test_a_rejected_work_request_ends_rejected(_ops_env: Path) -> None:
    item = first("work_request", ["reject"])
    result = ask(item.request + "\nr\n")
    assert result.exit_code == 0, item.id
    assert "[rejected: approval_rejected]" in result.stdout
    assert events(_ops_env) == ["drafted", "rejected"]


@pytest.mark.parametrize("tail", ["", "m\nfailure_code=MEC-ROUL\n"], ids=["choice", "edit"])
def test_end_of_input_abandons_the_pending_request(_ops_env: Path, tail: str) -> None:
    item = first("work_request", ["approve"])
    result = ask(f"{item.request}\n{tail}")
    assert result.exit_code == 0, item.id
    assert "[abandoned]" in result.stdout
    assert events(_ops_env) == ["drafted", "abandoned"]


def interrupted(stream: object) -> str | None:
    raise KeyboardInterrupt


def test_ctrl_c_abandons_the_pending_request(
    _ops_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_read_choice", interrupted)
    item = first("work_request", ["approve"])
    result = ask(item.request + "\n")
    assert result.exit_code == 130, item.id
    assert "abandonnée" in result.stderr
    assert events(_ops_env) == ["drafted", "abandoned"]


class AbandonFailsLog(TimelineLog):
    """An access log whose ``abandoned`` write fails (every other write works)."""

    def append(self, rec: OpsAccessLogRecord) -> None:
        if rec.event == "abandoned":
            raise AccessLogError("access log not writable: simulated failure")
        super().append(rec)


def test_ctrl_c_with_an_unwritable_abandon_exits_2(
    _ops_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_read_choice", interrupted)
    monkeypatch.setattr(cli, "TimelineLog", AbandonFailsLog)
    item = first("work_request", ["approve"])
    result = ask(item.request + "\n")
    assert result.exit_code == 2, item.id
    assert "access log is unavailable" in result.stderr


def test_ctrl_c_before_any_request_exits_130(
    _ops_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted_start(self: object, request: str) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(OpsEnvelope, "start", interrupted_start)
    result = ask("rien\n")
    assert result.exit_code == 130
    assert events(_ops_env) == []


def test_a_refused_request_shows_its_code(_ops_env: Path) -> None:
    item = first("technician_identification")
    result = ask(item.request + "\n")
    assert result.exit_code == 0, item.id
    assert f"[refused: {item.expected_reason}]" in result.stdout
    assert events(_ops_env) == ["refused"]
    assert_no_canary(result)


class BrokenLog(TimelineLog):
    """An access log whose every write fails."""

    def append(self, rec: OpsAccessLogRecord) -> None:
        raise AccessLogError("access log not writable: simulated failure")


def test_an_unwritable_access_log_exits_2(_ops_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "TimelineLog", BrokenLog)
    item = first("diagnosis")
    result = ask(item.request + "\n")
    assert result.exit_code == 2, item.id
    assert "access log is unavailable" in result.stderr
    assert str(item.asset_id) not in result.stdout


@pytest.mark.parametrize(
    ("args", "env", "message"),
    [
        (["--mode", "nope"], {}, "mode must be"),
        ([], {"ALVERNO_ALLOW_REMOTE_OLLAMA": "true"}, "RemoteOllamaError"),
    ],
    ids=["bad_mode", "remote"],
)
def test_ask_usage_errors_exit_2(
    monkeypatch: pytest.MonkeyPatch, args: list[str], env: dict[str, str], message: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    result = ask("rien\n", *args)
    assert result.exit_code == 2
    assert message in result.output


# Every ADR 0014 field of the access log record and the notice phrase that announces it.
NOTICE_FIELDS = {
    "v": "la version du format",
    "module": "le module (service à la clientèle ou entretien)",
    "event_id": "un numéro tiré au hasard pour chaque ligne",
    "draft_id": "relie les lignes d'une même demande de travail",
    "ts": "l'heure (arrondie à l'heure, UTC)",
    "channel": "le canal",
    "flow": "le flux",
    "event": "l'événement",
    "reason": "le motif d'un refus ou d'une retenue",
    "tool_calls": "le nom des outils appelés",
    "entity_refs": "les numéros d'équipement et de bons de travail lus",
    "account_ref": "le champ du compte client reste vide",
    "pi_types": "les types et le nombre de renseignements personnels retirés",
    "pi_count": "les types et le nombre de renseignements personnels retirés",
    "quarantine_flags": "les codes de la quarantaine",
    "action_tool": "l'action préparée",
    "action_args_sha256": "l'empreinte de ses arguments",
    "executed_external_send": "l'indication qu'aucun envoi n'a eu lieu",
    "edit_round": "le tour de modification",
    "model": "le modèle utilisé",
    "latency_ms": "la durée",
    "approver": "votre décision",
    "actor": "jamais votre nom",
}


def test_the_notice_announces_every_access_log_field() -> None:
    assert set(NOTICE_FIELDS) == set(OpsAccessLogRecord.model_fields)
    for field, phrase in NOTICE_FIELDS.items():
        assert phrase in MAINT_NOTICE, field


def test_the_notice_covers_the_purpose_rights_and_retention() -> None:
    text = maint_notice(30)
    assert "conservé 30 jours" in text and "conservé 90 jours" in MAINT_NOTICE
    for phrase in (
        "machine seulement",
        "ne nomme et n'évalue personne",
        "n'envoie rien",
        "ne sont pas conservés",
        "perdue",
        "jamais leur valeur",
        "RPRP",
        "rectifier",
        "retirer votre consentement",
        "sans valoir retrait",
        "effacez le terminal",
        "confidentialite@alverno.example",
    ):
        assert phrase in text, phrase


# --------------------------------------------------------------------------- eval
def evaluate(*args: str) -> Result:
    return runner.invoke(app, ["maint", "eval", "--fake", *args])


def test_eval_passes_the_gate_on_the_gold_set(tmp_path: Path) -> None:
    result = evaluate("--out", str(tmp_path))
    assert result.exit_code == 0, result.output[-2000:]
    assert "Release gate [maint-tools-fake]: PASS" in result.stdout
    assert (tmp_path / "eval-maint-tools-fake.json").is_file()
    assert any((tmp_path / "access").glob("maint-*.jsonl"))
    assert_no_canary(result)


def test_eval_with_a_limit_fails_the_gate_and_repeats(tmp_path: Path) -> None:
    result = evaluate("--out", str(tmp_path), "--limit", "2", "--repeats", "2")
    assert result.exit_code == 1
    assert result.stdout.count("Release gate [maint-tools-fake]: FAIL") == 2
    for index in (1, 2):
        assert (tmp_path / f"eval-maint-tools-fake-r{index}.json").is_file()
    no_gate = evaluate("--out", str(tmp_path / "b"), "--limit", "1", "--no-gate")
    assert no_gate.exit_code == 0 and "Release gate" not in no_gate.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--mode", "nope"], "mode must be"),
        (["--dataset", "absent.jsonl"], "file.missing"),
    ],
    ids=["bad_mode", "no_dataset"],
)
def test_eval_usage_errors_exit_2(tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "out"
    result = evaluate("--out", str(out), *args)
    assert result.exit_code == 2
    assert message in result.output
    assert not out.exists() or not [p for p in out.rglob("*") if p.is_file()]


def test_eval_in_tools_mode_needs_fake(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["maint", "eval", "--mode", "tools", "--out", str(out)])
    assert result.exit_code == 2
    assert "--fake" in result.output
    assert not out.exists()


# --------------------------------------------------------------------------- full mode (fake)
def test_a_full_fake_diagnosis_is_answered(_ops_env: Path) -> None:
    item = first("diagnosis")
    result = ask(item.request + "\n", "--mode", "full")
    assert result.exit_code == 0, item.id
    assert MAINT_NOTICE in result.stderr
    assert "Diagnostic de l'équipement" in result.stdout and str(item.asset_id) in result.stdout
    assert events(_ops_env) == ["answered"]
    assert_no_canary(result)


def test_a_full_fake_work_request_is_approved_once(_ops_env: Path) -> None:
    item = first("work_request", ["approve"])
    result = ask(item.request + "\na\n", "--mode", "full")
    assert result.exit_code == 0, item.id
    assert "Écriture préparée : gmao.create_work_request" in result.stdout
    assert result.stdout.count(EXECUTED_BANNER) == 1
    assert events(_ops_env) == ["drafted", "approved"]
    assert_no_canary(result)


def test_eval_in_full_fake_mode_passes_its_gate(tmp_path: Path) -> None:
    result = evaluate("--mode", "full", "--out", str(tmp_path))
    assert result.exit_code == 0, result.output[-2000:]
    assert "Release gate [maint-full-fake]: PASS" in result.stdout
    assert (tmp_path / "eval-maint-full-fake.json").is_file()
    assert_no_canary(result)


def test_ci_runs_both_maintenance_gates_before_the_scans() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    tools = "uv run alverno maint eval --mode tools --fake --out reports/ci"
    full = "uv run alverno maint eval --mode full --fake --out reports/ci"
    assert workflow.index(tools) < workflow.index(full) < workflow.index("privacy scan")
