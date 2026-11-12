"""Module 6 (ADRs 0025 to 0028): SharePoint sync, local API, Entra ID tokens, Copilot
Studio files and the m365-fake profile. Offline: test keys only, no tenant, no network.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from starlette.testclient import TestClient
from typer.testing import CliRunner

from alverno_lab.agents.quality.graph import QualityAnswer
from alverno_lab.cli import app
from alverno_lab.corpus import load_corpus
from alverno_lab.m365 import copilot, sharepoint
from alverno_lab.m365.api import ROUTES, app_routes, create_app, hash_user, load_salt
from alverno_lab.m365.auth import (
    REQUIRED_ROLE,
    Principal,
    TokenRejected,
    TokenVerifier,
    make_test_keys,
)
from alverno_lab.m365.evaluation import (
    ASK,
    TEST_AUDIENCE,
    TEST_TENANT,
    auth_cases,
    count_bypass,
    log_content_hits,
    wrong_group_kept,
)
from alverno_lab.privacy.access_log import AccessLog, AccessLogError
from alverno_lab.privacy.m365_access_log import M365AccessLogRecord

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "data" / "corpus"
LIBRARY = REPO_ROOT / "data" / "m365" / "sharepoint" / "library.json"
INTEGRATION = REPO_ROOT / "integrations" / "copilot-studio"
KEYS = make_test_keys()


def answer(question: str, *, refused: bool = False) -> QualityAnswer:
    return QualityAnswer(
        question=question,
        answer="Refus." if refused else "Le comité se réunit chaque semaine [QP-007#02].",
        citations=[] if refused else ["QP-007#02"],
        cited_doc_ids=[] if refused else ["QP-007"],
        confidence=0.0 if refused else 0.9,
        refused=refused,
        refusal_reason="no_match" if refused else None,
        latency_ms=1.0,
    )


def token(**claims: Any) -> str:
    return KEYS.token(tenant_id=TEST_TENANT, audience=TEST_AUDIENCE, **claims)


def verifier() -> TokenVerifier:
    return TokenVerifier(KEYS.jwks, tenant_id=TEST_TENANT, audience=TEST_AUDIENCE)


def client_of(tmp_path: Path, ask: Any = answer, ver: TokenVerifier | None = None) -> TestClient:
    log = AccessLog(tmp_path / "access", prefix="m365", record_type=M365AccessLogRecord)
    return TestClient(create_app(ask, ver or verifier(), log, b"salt"))


def log_lines(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for path in sorted((tmp_path / "access").glob("m365-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


# --------------------------------------------------------------------------- 6a sync
def test_the_export_pins_every_corpus_file_without_copying_text() -> None:
    library = sharepoint.load_library(LIBRARY)
    assert sorted(i.name for i in library.items) == sorted(p.name for p in CORPUS.glob("*.md"))
    for item in library.items:
        assert item.sha256 == sharepoint.sha256_file(CORPUS / item.name)
        assert sharepoint.readable(item) and sharepoint.allowed(item)
        assert (item.sensitivity_label == "Restreint — client") == item.name.startswith("SPEC-")
    assert "## " not in LIBRARY.read_text(encoding="utf-8")


def test_sync_reproduces_the_corpus_then_rewrites_only_changes(tmp_path: Path) -> None:
    library = sharepoint.load_library(LIBRARY)
    out = tmp_path / "corpus"
    first = sharepoint.sync(library, CORPUS, out)
    assert len(first.written) == 31 and not first.unchanged
    assert sharepoint.identical(out, CORPUS)
    assert [d.doc_id for d in load_corpus(out)] == [d.doc_id for d in load_corpus(CORPUS)]
    second = sharepoint.sync(library, CORPUS, out)
    assert not second.written and len(second.unchanged) == 31
    changed = library.items[0].model_copy(update={"etag": '"{NEW},2"'})
    third = sharepoint.sync(
        library.model_copy(update={"items": (changed, *library.items[1:])}), CORPUS, out
    )
    assert third.written == [changed.name]


def test_a_removed_item_is_retired_not_deleted(tmp_path: Path) -> None:
    library = sharepoint.load_library(LIBRARY)
    out = tmp_path / "corpus"
    sharepoint.sync(library, CORPUS, out)
    gone = library.items[-1]
    result = sharepoint.sync(library.model_copy(update={"items": library.items[:-1]}), CORPUS, out)
    assert result.retired == [gone.name] and not (out / gone.name).exists()
    assert len(list((out / sharepoint.RETIRED).iterdir())) == 1
    assert gone.name not in {p.name for p in out.glob("*.md")}
    assert len(load_corpus(out)) == 30


def test_group_and_label_filter_the_items(tmp_path: Path) -> None:
    library = sharepoint.load_library(LIBRARY)
    no_group = library.items[0].model_copy(update={"readers": ("Qualité — rédaction",)})
    secret = library.items[1].model_copy(update={"sensitivity_label": "Niveau 4 — RH"})
    result = sharepoint.sync(
        library.model_copy(update={"items": (no_group, secret, *library.items[2:])}),
        CORPUS,
        tmp_path / "out",
    )
    assert result.skipped_group == [no_group.name] and result.skipped_label == [secret.name]
    assert not (tmp_path / "out" / no_group.name).exists()


def test_a_hash_mismatch_stops_before_any_write(tmp_path: Path) -> None:
    content = tmp_path / "content"
    shutil.copytree(CORPUS, content)
    (content / "QP-001.md").write_text("altéré\n", encoding="utf-8")
    with pytest.raises(sharepoint.SyncError, match=r"QP-001\.md"):
        sharepoint.sync(sharepoint.load_library(LIBRARY), content, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_malformed_export_is_refused(tmp_path: Path) -> None:
    data = json.loads(LIBRARY.read_text(encoding="utf-8"))
    data["items"].append(dict(data["items"][0]))
    path = tmp_path / "library.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(sharepoint.SyncError, match="duplicate"):
        sharepoint.load_library(path)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(sharepoint.SyncError):
        sharepoint.load_library(path)


def test_the_wrong_group_control_fails_as_planned(monkeypatch: pytest.MonkeyPatch) -> None:
    library = sharepoint.load_library(LIBRARY)
    assert wrong_group_kept(library, CORPUS) == 0
    monkeypatch.setattr(sharepoint, "readable", lambda item, group="": True)
    assert wrong_group_kept(library, CORPUS) > 0


# --------------------------------------------------------------------------- 6b tokens
def test_a_valid_token_gives_the_principal() -> None:
    principal = verifier().verify(f"Bearer {token(oid='abc')}")
    assert principal.oid == "abc" and principal.tid == TEST_TENANT
    assert REQUIRED_ROLE in principal.roles


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("missing_token", 401, "missing_token"),
        ("unknown_signature", 401, "signature"),
        ("expired", 401, "expired"),
        ("not_yet_valid", 401, "not_yet_valid"),
        ("wrong_issuer", 401, "issuer"),
        ("wrong_audience", 401, "audience"),
        ("wrong_tenant", 401, "tenant"),
        ("missing_role", 403, "missing_role"),
        ("symmetric_algorithm", 401, "algorithm"),
    ],
)
def test_each_rejected_token_names_its_code(case: str, status: int, code: str) -> None:
    headers = auth_cases(KEYS, time.time())[case]
    with pytest.raises(TokenRejected) as caught:
        verifier().verify(headers.get("authorization"))
    assert (caught.value.status, caught.value.code) == (status, code)
    assert (caught.value.oid is not None) is (status == 403)


@pytest.mark.parametrize("header", ["Basic abc", "Bearer ", "Bearer not.a.jwt", "token"])
def test_malformed_headers_are_unauthorized(header: str) -> None:
    with pytest.raises(TokenRejected) as caught:
        verifier().verify(header)
    assert caught.value.status == 401


def test_an_unknown_key_id_is_unauthorized() -> None:
    other = make_test_keys(kid="another-kid")
    with pytest.raises(TokenRejected, match="unknown_key"):
        verifier().verify(f"Bearer {other.token(tenant_id=TEST_TENANT, audience=TEST_AUDIENCE)}")


def test_the_verifier_needs_its_tenant_and_reads_a_jwks_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="required"):
        TokenVerifier(KEYS.jwks, tenant_id="", audience=TEST_AUDIENCE)
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps(KEYS.jwks), encoding="utf-8")
    loaded = TokenVerifier.from_file(path, tenant_id=TEST_TENANT, audience=TEST_AUDIENCE)
    assert loaded.verify(f"Bearer {token()}").tid == TEST_TENANT
    assert "d" not in KEYS.jwks["keys"][0], "the JWKS holds the public key only"


# --------------------------------------------------------------------------- 6b API
def test_health_needs_no_token_and_returns_no_content(tmp_path: Path) -> None:
    with client_of(tmp_path) as client:
        response = client.get("/v1/health")
    assert response.json() == {"status": "ok"} and log_lines(tmp_path) == []


def test_a_valid_call_answers_and_logs_metadata_only(tmp_path: Path) -> None:
    question = "Quand le comité de revue se réunit-il chaque semaine ?"
    with client_of(tmp_path) as client:
        response = client.post(
            ASK, json={"question": question}, headers={"authorization": f"Bearer {token()}"}
        )
    assert response.status_code == 200
    assert response.json()["sources"] == ["QP-007"] and response.json()["refused"] is False
    [line] = log_lines(tmp_path)
    assert line["decision"] == "answered" and line["n_citations"] == 1
    assert line["user"] == hash_user("00000000-0000-0000-0000-000000000001", b"salt")
    assert log_content_hits(tmp_path / "access", [question, response.json()["answer"]]) == 0


def test_a_refusal_is_served_and_logged_with_its_reason(tmp_path: Path) -> None:
    with client_of(tmp_path, ask=lambda q: answer(q, refused=True)) as client:
        response = client.post(
            ASK, json={"question": "x" * 30}, headers={"authorization": f"Bearer {token()}"}
        )
    assert response.json()["refused"] is True
    assert log_lines(tmp_path)[0]["reason"] == "no_match"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "no_question"),
        ({"question": "  "}, "no_question"),
        ({"question": "x" * 501}, "too_long"),
    ],
)
def test_invalid_bodies_are_refused(tmp_path: Path, body: dict[str, Any], code: str) -> None:
    with client_of(tmp_path) as client:
        response = client.post(ASK, json=body, headers={"authorization": f"Bearer {token()}"})
    assert (response.status_code, response.json()) == (400, {"error": code})
    assert log_lines(tmp_path)[0]["decision"] == "invalid"


def test_rejected_tokens_are_logged_without_a_user_unless_forbidden(tmp_path: Path) -> None:
    with client_of(tmp_path) as client:
        statuses = count_bypass(client, auth_cases(KEYS, time.time()))
    assert all(s in (401, 403) for s in statuses.values())
    lines = log_lines(tmp_path)
    assert {line["decision"] for line in lines} == {"unauthorized", "forbidden"}
    assert all((line["user"] is None) == (line["decision"] == "unauthorized") for line in lines)


def test_the_auth_bypass_control_fails_as_planned(tmp_path: Path) -> None:
    class NoRoleCheck(TokenVerifier):
        """A verifier that forgets the role: the missing-role case gets through."""

        def verify(self, authorization: str | None) -> Principal:
            try:
                return super().verify(authorization)
            except TokenRejected as exc:
                if exc.oid is None:
                    raise
                return Principal(oid=exc.oid, tid=TEST_TENANT, roles=())

    lax = NoRoleCheck(KEYS.jwks, tenant_id=TEST_TENANT, audience=TEST_AUDIENCE)
    with client_of(tmp_path, ver=lax) as client:
        statuses = count_bypass(client, auth_cases(KEYS, time.time()))
    assert statuses["missing_role"] == 200
    assert sum(1 for s in statuses.values() if s == 200) == 1


def test_an_agent_error_is_a_502_and_an_unwritable_log_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(question: str) -> QualityAnswer:
        raise RuntimeError("model down")

    headers = {"authorization": f"Bearer {token()}"}
    with client_of(tmp_path, ask=broken) as client:
        assert client.post(ASK, json={"question": "q"}, headers=headers).status_code == 502
    assert log_lines(tmp_path)[-1]["reason"] == "agent_error"

    def refuse(self: Any, rec: Any) -> None:
        raise AccessLogError("not writable")

    monkeypatch.setattr(AccessLog, "append", refuse)
    with client_of(tmp_path) as client:
        assert client.post(ASK, json={"question": "q"}, headers=headers).status_code == 503
        assert client.post(ASK, json={"question": "q"}).status_code == 503


def test_the_api_exposes_only_its_two_routes() -> None:
    application = create_app(
        answer,
        verifier(),
        AccessLog(Path("x"), prefix="m365", record_type=M365AccessLogRecord),
        b"s",
    )
    assert app_routes(application) == set(ROUTES)


def test_the_log_content_control_fails_as_planned(tmp_path: Path) -> None:
    question = "Quelle est la tolérance de planéité des tables de soudage ?"
    (tmp_path / "planted.jsonl").write_text(json.dumps({"q": question}), encoding="utf-8")
    assert log_content_hits(tmp_path, [question]) == 1
    assert log_content_hits(tmp_path, ["court"]) == 0


def test_the_salt_is_made_once_and_kept(tmp_path: Path) -> None:
    path = tmp_path / "m365" / "salt"
    first = load_salt(path)
    assert len(first) == 32 and load_salt(path) == first


@pytest.mark.parametrize(
    ("fields", "rule"),
    [
        ({"decision": "answered", "reason": "x", "user": "0" * 32}, "m365_log.reason"),
        ({"decision": "refused", "user": "0" * 32}, "m365_log.reason"),
        ({"decision": "unauthorized", "reason": "expired", "user": "0" * 32}, "m365_log.user"),
        ({"decision": "forbidden", "reason": "missing_role"}, "m365_log.user"),
        ({"decision": "answered", "user": "0" * 32, "question": "q"}, "extra_forbidden"),
    ],
)
def test_the_record_rules(fields: dict[str, Any], rule: str) -> None:
    base = {"event_id": "a" * 32, "ts": "2026-11-11T10:00Z", "channel": "api"}
    with pytest.raises(ValidationError) as caught:
        M365AccessLogRecord.model_validate({**base, **fields})
    assert rule in {e["type"] for e in caught.value.errors()}


# --------------------------------------------------------------------------- 6c files
def test_the_committed_connector_definition_is_generated_from_the_api() -> None:
    assert (INTEGRATION / copilot.SWAGGER).read_text(encoding="utf-8") == copilot.render_swagger()
    document = copilot.openapi_document()
    assert copilot.swagger_routes(document) == set(ROUTES)
    assert document["host"].startswith("127.0.0.1:")


def test_the_repository_integration_passes_its_checks() -> None:
    check = copilot.check_integration(INTEGRATION, set(ROUTES))
    assert (check.spec_api_coverage, check.topic_action_valid, check.generative_flags) == (
        1.0,
        1,
        0,
    )
    assert check.n_topics == 4 and check.n_actions == 1 and check.swagger_current


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "copilot-studio"
    shutil.copytree(INTEGRATION, root)
    return root


def test_an_unknown_action_fails_the_topic_check(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    topic = root / copilot.TOPICS / "quality-question.yaml"
    topic.write_text(topic.read_text(encoding="utf-8").replace("AskQuality", "AskHr"), "utf-8")
    assert copilot.check_integration(root, set(ROUTES)).topic_action_valid == 0


@pytest.mark.parametrize("change", ["enable", "drop", "knowledge"])
def test_a_generative_setting_is_counted(tmp_path: Path, change: str) -> None:
    root = _copy(tmp_path)
    data = yaml.safe_load((root / copilot.AGENT).read_text(encoding="utf-8"))
    if change == "enable":
        data["settings"]["generativeAnswers"] = True
    elif change == "drop":
        del data["settings"]["webBrowsing"]
    else:
        data["knowledgeSources"] = ["SharePoint"]
    (root / copilot.AGENT).write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    assert copilot.check_integration(root, set(ROUTES)).generative_flags == 1


def test_an_undescribed_route_lowers_the_coverage() -> None:
    check = copilot.check_integration(INTEGRATION, {*ROUTES, ("POST", "/v1/hr/ask")})
    assert check.spec_api_coverage < 1.0


# --------------------------------------------------------------------------- commands
def test_m365_sync_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["m365", "sync", "--out", str(tmp_path / "c")])
    assert result.exit_code == 0, result.output
    assert "Synced 31 item(s)" in result.output and "31 written" in result.output


def test_m365_sync_refuses_a_broken_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    bad = tmp_path / "library.json"
    bad.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ALVERNO_M365_LIBRARY_PATH", str(bad))
    result = CliRunner().invoke(app, ["m365", "sync", "--out", str(tmp_path / "c")])
    assert result.exit_code == 2


def test_m365_serve_refuses_another_address_or_missing_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["m365", "serve", "--host", "0.0.0.0"]).exit_code == 2
    result = runner.invoke(app, ["m365", "serve"])
    assert result.exit_code == 2 and "ALVERNO_M365_TENANT_ID" in result.output


def test_m365_eval_passes_its_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = CliRunner().invoke(app, ["m365", "eval", "--fake", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Release gate [m365-fake]: PASS" in result.output
    report = json.loads((tmp_path / "eval-m365-fake.json").read_text(encoding="utf-8"))
    assert report["metrics"]["api_parity"] == 1.0 and report["metrics"]["n_auth_cases"] == 9
    assert report["mismatched"] == []
