"""``alverno service ask`` and ``alverno service eval`` through typer's CliRunner (I6 spec
decision 7).

``ask`` prints the art. 8 notice on stderr, reads the request on the first line of
stdin, shows a pending draft with its header and plays the operator's decisions read
on stdin (an edit runs up to a line holding a single « . »). The end of the input or
Ctrl-C while a draft waits abandons it; an unwritable access log exits 2. ``eval`` gates
the report against ``svc-tools-fake``: exit 0 on the gold set, 1 when the gate fails (a
``--limit`` run), 2 on a usage error. Requests come from the gold set; assertions cite
item ids and codes, never a request, a draft or a canary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from alverno_lab import cli
from alverno_lab.agents.ops import OpsEnvelope, TimelineLog
from alverno_lab.agents.service.notice import SERVICE_NOTICE, service_notice
from alverno_lab.cli import APPROVED_BANNER, DECISION_PROMPT, EDIT_END, app
from alverno_lab.connectors import SimErp
from alverno_lab.evals.canaries import load_canaries
from alverno_lab.evals.service import SvcGoldItem, load_svc_gold
from alverno_lab.ops_pipeline import ops_quarantine
from alverno_lab.privacy import AccessLogError, canary_in
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord

from .conftest import EVAL_DATA, HR_DATA, REPO_ROOT
from .ops_privacy_helpers import ops_privacy_ctx

runner = CliRunner()
OPS = EVAL_DATA / "ops"
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
GOLD = load_svc_gold(
    SVC_GOLD, SimErp.from_yaml(OPS / "erp.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))
)
CANARIES = [c.canary for c in load_canaries(SVC_GOLD)]


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
        "ALVERNO_SVC_EVAL_PATH": SVC_GOLD,
        "ALVERNO_THRESHOLDS_PATH": REPO_ROOT / "evals" / "thresholds.yaml",
        "ALVERNO_ACCESS_LOG_DIR": log_dir,
    }
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER"):
        monkeypatch.delenv(name, raising=False)
    return log_dir


def first(rule: str, steps: list[str] | None = None) -> SvcGoldItem:
    """The first gold item of a family (and approval script), picked, never typed in."""
    for item in GOLD:
        if item.family == rule and (
            steps is None or [s.decision for s in item.approval_script] == steps
        ):
            return item
    raise AssertionError(f"gold:no_{rule}")


def ask(stdin: str, *args: str) -> Result:
    return runner.invoke(app, ["service", "ask", "--fake", *args], input=stdin)


def events(log_dir: Path) -> list[str]:
    return [
        OpsAccessLogRecord.model_validate_json(line).event
        for path in sorted(log_dir.glob("service-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def assert_no_canary(result: Result) -> None:
    for canary in CANARIES:
        assert not canary_in(canary, result.stdout), canary.type
        assert not canary_in(canary, result.stderr), canary.type


def draft_of(result: Result, index: int = 0) -> str:
    """The text of the ``index``-th draft shown on stdout."""
    blocks = result.stdout.split(APPROVED_BANNER)[0].split("--- Brouillon à relire")[1:]
    body = blocks[index].split("\n\n", 1)[1]
    return body.rsplit("\n---", 1)[0]


# --------------------------------------------------------------------------- ask
def test_an_order_status_is_answered_after_the_notice(_ops_env: Path) -> None:
    item = first("order_status")
    result = ask(item.request + "\n")
    assert result.exit_code == 0, item.id
    assert SERVICE_NOTICE in result.stderr
    assert result.stderr.index(SERVICE_NOTICE) < result.stderr.index("Demande : ")
    assert "Brouillon" not in result.stdout and str(item.order_id) in result.stdout
    assert events(_ops_env) == ["answered"]
    assert_no_canary(result)


def test_an_approved_reply_is_shown_once_and_recorded(_ops_env: Path) -> None:
    item = first("customer_reply", ["approve"])
    result = ask(item.request + "\na\n")
    assert result.exit_code == 0, item.id
    shown = draft_of(result)
    assert "Brouillon préparé par un outil automatisé — à relire" in result.stdout
    assert "Compte : " in result.stdout and "Destinataire : " in result.stdout
    assert f"Identifiants cités : {item.order_id}" in result.stdout
    # EFVP step 10: after approval, the approved text is printed once, for the operator.
    review, approved = result.stdout.split(APPROVED_BANNER)
    shown_once = review.count(shown) == 1
    printed_once = approved.count(shown) == 1
    assert shown_once, f"{item.id}:draft_shown_once"
    assert printed_once, f"{item.id}:approved_printed_once"
    assert "rien n'a été envoyé" in APPROVED_BANNER
    assert DECISION_PROMPT in result.stderr
    assert events(_ops_env) == ["drafted", "approved"]
    assert_no_canary(result)


def test_an_edit_is_read_up_to_the_dot_then_approved(_ops_env: Path) -> None:
    item = first("customer_reply", ["edit", "approve"])
    edited = item.approval_script[0].edited_text
    assert edited is not None
    result = ask(f"{item.request}\nm\n{edited}\n{EDIT_END}\na\n")
    assert result.exit_code == 0, item.id
    assert "(tour 1)" in result.stdout
    assert draft_of(result, 1).strip() == edited.strip()
    assert events(_ops_env) == ["drafted", "edit_submitted", "re_presented", "approved"]
    assert_no_canary(result)


def test_a_rejected_draft_ends_rejected(_ops_env: Path) -> None:
    item = first("customer_reply", ["reject"])
    result = ask(item.request + "\nrejeter\n")
    assert result.exit_code == 0, item.id
    assert "[rejected: approval_rejected]" in result.stdout
    assert events(_ops_env) == ["drafted", "rejected"]


def test_an_unknown_choice_is_asked_again(_ops_env: Path) -> None:
    item = first("customer_reply", ["reject"])
    result = ask(item.request + "\npeut-être\nr\n")
    assert result.exit_code == 0, item.id
    assert "Choix non reconnu." in result.stderr
    assert result.stderr.count(DECISION_PROMPT) == 2
    assert events(_ops_env) == ["drafted", "rejected"]


@pytest.mark.parametrize("tail", ["", "m\nune ligne sans fin\n"], ids=["choice", "edit"])
def test_end_of_input_abandons_the_pending_draft(_ops_env: Path, tail: str) -> None:
    item = first("customer_reply", ["approve"])
    result = ask(f"{item.request}\n{tail}")
    assert result.exit_code == 0, item.id
    assert "[abandoned]" in result.stdout
    assert events(_ops_env) == ["drafted", "abandoned"]


def interrupted(stream: object) -> str | None:
    raise KeyboardInterrupt


def test_ctrl_c_abandons_the_pending_draft(_ops_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_read_choice", interrupted)
    item = first("customer_reply", ["approve"])
    result = ask(item.request + "\n")
    assert result.exit_code == 130, item.id
    assert "abandonné" in result.stderr
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
    item = first("customer_reply", ["approve"])
    result = ask(item.request + "\n")
    assert result.exit_code == 2, item.id
    assert "access log is unavailable" in result.stderr
    assert "abandonné" not in result.stderr
    assert events(_ops_env) == ["drafted"]


def test_ctrl_c_before_any_draft_exits_130(_ops_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted_start(self: object, request: str) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(OpsEnvelope, "start", interrupted_start)
    result = ask("rien\n")
    assert result.exit_code == 130
    assert "abandonné" in result.stderr
    assert events(_ops_env) == []


def test_a_refused_request_shows_its_code(_ops_env: Path) -> None:
    item = first("unprefixed_id")
    result = ask(item.request + "\n")
    assert result.exit_code == 0, item.id
    assert f"[refused: {item.expected_reason}]" in result.stdout
    assert events(_ops_env) == ["refused"]


class BrokenLog(TimelineLog):
    """An access log whose every write fails."""

    def append(self, rec: OpsAccessLogRecord) -> None:
        raise AccessLogError("access log not writable: simulated failure")


def test_an_unwritable_access_log_exits_2(_ops_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "TimelineLog", BrokenLog)
    item = first("order_status")
    result = ask(item.request + "\n")
    assert result.exit_code == 2, item.id
    assert "access log is unavailable" in result.stderr
    assert str(item.order_id) not in result.stdout


@pytest.mark.parametrize(
    ("args", "env", "message"),
    [
        (["--mode", "full"], {}, "pipeline:full_mode_in_i7"),
        (["--mode", "nope"], {}, "mode must be"),
        ([], {"ALVERNO_ALLOW_REMOTE_OLLAMA": "true"}, "RemoteOllamaError"),
    ],
    ids=["full", "bad_mode", "remote"],
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
    "draft_id": "relie les lignes d'un même brouillon",
    "ts": "l'heure (arrondie à l'heure, UTC)",
    "channel": "le canal",
    "flow": "le flux",
    "event": "l'événement",
    "reason": "le motif d'un refus ou d'une retenue",
    "tool_calls": "le nom des outils appelés",
    "entity_refs": "les numéros de commande lus",
    "account_ref": "le compte en portée",
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
    # EFVP section 4 and action table: the notice is compared with the ADR 0014 fields.
    assert set(NOTICE_FIELDS) == set(OpsAccessLogRecord.model_fields)
    for field, phrase in NOTICE_FIELDS.items():
        assert phrase in SERVICE_NOTICE, field


def test_the_notice_covers_the_rights_and_the_retention() -> None:
    text = service_notice(30)
    assert "conservé 30 jours" in text and "conservé 90 jours" in SERVICE_NOTICE
    for phrase in (
        "n'envoie rien",
        "ne sont pas conservés",
        "perdu",
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
    return runner.invoke(app, ["service", "eval", "--fake", *args])


def test_eval_passes_the_gate_on_the_gold_set(tmp_path: Path) -> None:
    result = evaluate("--out", str(tmp_path))
    assert result.exit_code == 0, result.output[-2000:]
    assert "Release gate [svc-tools-fake]: PASS" in result.stdout
    assert (tmp_path / "eval-svc-tools-fake.json").is_file()
    assert any((tmp_path / "access").glob("service-*.jsonl"))
    assert_no_canary(result)


def test_eval_with_a_limit_fails_the_gate(tmp_path: Path) -> None:
    result = evaluate("--out", str(tmp_path), "--limit", "3")
    assert result.exit_code == 1
    assert "FAIL n_items = 3.0" in result.stdout


def test_eval_repeats_write_one_report_each(tmp_path: Path) -> None:
    result = evaluate("--out", str(tmp_path), "--limit", "2", "--repeats", "2")
    assert result.exit_code == 1
    assert result.stdout.count("Release gate [svc-tools-fake]: FAIL") == 2
    for index in (1, 2):
        assert (tmp_path / f"eval-svc-tools-fake-r{index}.json").is_file()
    no_gate = evaluate("--out", str(tmp_path / "b"), "--limit", "1", "--no-gate")
    assert no_gate.exit_code == 0
    assert "Release gate" not in no_gate.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--mode", "full"], "pipeline:full_mode_in_i7"),
        (["--mode", "nope"], "mode must be"),
        (["--dataset", "absent.jsonl"], "file.missing"),
    ],
    ids=["full", "bad_mode", "no_dataset"],
)
def test_eval_usage_errors_exit_2(tmp_path: Path, args: list[str], message: str) -> None:
    out = tmp_path / "out"
    result = evaluate("--out", str(out), *args)
    assert result.exit_code == 2
    assert message in result.output
    # A refused run writes no file (no empty trace file of a mode that cannot run yet).
    assert not out.exists() or not [p for p in out.rglob("*") if p.is_file()]


def test_eval_in_tools_mode_needs_fake(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["service", "eval", "--mode", "tools", "--out", str(out)])
    assert result.exit_code == 2
    assert "--fake" in result.output
    assert not out.exists()
