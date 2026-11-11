"""MCP server of module 5a (ADR 0017): tool list, input rules, closed error codes, stdio.

Unit tests drive :func:`call_tool` with a stub backend that counts calls; one test starts
``alverno mcp serve --fake`` as a subprocess and talks to it over stdio, as a client would.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import StdioServerParameters
from mcp.client import Client

from alverno_lab.config import Settings
from alverno_lab.index import IndexNotFoundError
from alverno_lab.mcp_server import server as srv
from alverno_lab.mcp_server.adapters import UnknownSymptomError, order_request, symptom_request
from alverno_lab.mcp_server.schemas import AgentAnswer, OpsAnswer, SearchResult
from alverno_lab.ollama import OllamaUnavailableError

from .conftest import REPO_ROOT

QUESTION = "Quelle est la procédure pour une pièce non conforme ?"


class StubBackends:
    """Duck-typed :class:`Backends`: records each call, returns a fixed result."""

    def __init__(self, *, ops: bool = False, error: Exception | None = None) -> None:
        self.ops_enabled = ops
        self.calls: list[str] = []
        self.error = error

    def _done(self, name: str, result: Any) -> Any:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return result

    def quality_search(self, query: str, k: int) -> SearchResult:
        return self._done("quality_search", SearchResult(hits=[]))

    def quality_ask(self, question: str) -> AgentAnswer:
        answer = AgentAnswer(answer="ok", refused=False, refusal_reason=None, citations=[])
        return self._done("quality_ask", answer)

    def hr_policy_ask(self, question: str) -> AgentAnswer:
        return self.quality_ask(question)

    def order_status(self, args: Any) -> OpsAnswer:
        return self._done(
            "order_status", OpsAnswer(outcome="answered", text="", refusal_reason=None)
        )

    def machine_diagnosis(self, args: Any) -> OpsAnswer:
        return self._done(
            "machine_diagnosis", OpsAnswer(outcome="answered", text="", refusal_reason=None)
        )


def call(backends: StubBackends, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    result = srv.call_tool(backends, Settings(), name, arguments)  # type: ignore[arg-type]
    payload: dict[str, Any] = result.structured_content or {}
    assert result.is_error == ("error" in payload)
    assert json.loads(result.content[0].text) == payload  # type: ignore[union-attr]
    return payload


def listed(*, ops: bool) -> list[Any]:
    server = srv.build_server(StubBackends(ops=ops), Settings())  # type: ignore[arg-type]

    async def go() -> list[Any]:
        async with Client(server) as client:
            return list((await client.list_tools()).tools)

    return anyio.run(go)


# --------------------------------------------------------------------------- tool list
def test_default_tools_are_the_three_level_2_tools() -> None:
    names = [t.name for t in listed(ops=False)]
    assert names == ["quality_search", "quality_ask", "hr_policy_ask"]
    assert list(srv.tool_names(ops=False)) == names


def test_the_option_adds_exactly_the_two_module_3_read_tools() -> None:
    names = [t.name for t in listed(ops=True)]
    assert names == [*srv.tool_names(ops=False), "order_status", "machine_diagnosis"]


@pytest.mark.parametrize("ops", [False, True])
def test_every_tool_is_read_only_with_a_closed_schema(ops: bool) -> None:
    for tool in listed(ops=ops):
        assert tool.annotations is not None and tool.annotations.read_only_hint, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.input_schema["additionalProperties"] is False, tool.name


def test_free_text_fields_carry_the_length_limit() -> None:
    schemas = {t.name: t.input_schema for t in listed(ops=False)}
    limit = Settings().mcp_max_input_chars
    assert schemas["quality_search"]["properties"]["query"]["maxLength"] == limit
    assert schemas["quality_ask"]["properties"]["question"]["maxLength"] == limit
    assert schemas["hr_policy_ask"]["properties"]["question"]["maxLength"] == limit


def test_the_server_tells_the_client_to_stay_local() -> None:
    assert "tourne sur le poste" in srv.INSTRUCTIONS
    assert "modèle hébergé" in srv.INSTRUCTIONS


# --------------------------------------------------------------------------- calls
def test_a_valid_call_reaches_its_backend_once() -> None:
    backends = StubBackends()
    assert call(backends, "quality_ask", {"question": QUESTION})["answer"] == "ok"
    assert backends.calls == ["quality_ask"]


@pytest.mark.parametrize(
    "name",
    ["approve", "customer_reply", "create_work_request", "work_request", "order_status", "x"],
)
def test_unknown_or_closed_tools_never_reach_a_backend(name: str) -> None:
    backends = StubBackends(ops=False)
    assert call(backends, name, {"order_id": "CMD-2026-0401"}) == {"error": "unknown_tool"}
    assert backends.calls == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("quality_ask", {"question": "x" * 501}),
        ("quality_ask", {"question": "   "}),
        ("quality_ask", {"question": ["liste"]}),
        ("quality_ask", {}),
        ("quality_ask", None),
        ("hr_policy_ask", {"question": QUESTION, "employee_id": "E-0001"}),
        ("quality_search", {"query": "soudage", "k": "trois"}),
        ("quality_search", {"query": "soudage", "k": 9}),
        ("quality_search", {"query": "soudage", "k": True}),
        ("order_status", {"order_id": "commande 2026-0401"}),
        ("machine_diagnosis", {"asset_id": "PLI-01", "symptom_code": "SYM-FUITE"}),
        ("machine_diagnosis", {"asset_id": "EQ-PLI-01", "symptom_code": "fuite"}),
    ],
)
def test_invalid_inputs_are_rejected_before_any_backend(
    name: str, arguments: dict[str, Any] | None
) -> None:
    backends = StubBackends(ops=True)
    assert call(backends, name, arguments) == {"error": "invalid_input"}
    assert backends.calls == []


def test_the_length_limit_is_inclusive() -> None:
    backends = StubBackends()
    limit = Settings().mcp_max_input_chars
    assert "error" not in call(backends, "quality_ask", {"question": "x" * limit})


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OllamaUnavailableError("Ollama is down"), "backend_unavailable"),
        (IndexNotFoundError("no index"), "backend_unavailable"),
        (UnknownSymptomError("symptom_code"), "invalid_input"),
        (RuntimeError(QUESTION), "internal_error"),
    ],
)
def test_backend_failures_give_a_closed_code_without_the_message(
    error: Exception, code: str
) -> None:
    backends = StubBackends(ops=True, error=error)
    result = srv.call_tool(backends, Settings(), "quality_ask", {"question": QUESTION})  # type: ignore[arg-type]
    assert result.structured_content == {"error": code}
    assert QUESTION not in result.content[0].text  # type: ignore[union-attr]
    assert str(error) not in result.content[0].text  # type: ignore[union-attr]


def test_the_module_3_requests_are_fixed_texts_built_from_typed_ids() -> None:
    assert order_request("CMD-2026-0401") == "Où en est la commande CMD-2026-0401 ?"
    request = symptom_request("EQ-PLI-01", "fuite")
    assert request.startswith("Diagnostic de l'équipement EQ-PLI-01 : fuite.")


# --------------------------------------------------------------------------- stdio
def test_the_ops_warning_goes_to_stderr_before_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(srv.anyio, "run", lambda *a, **k: None)
    srv.run_stdio(StubBackends(ops=True), Settings())  # type: ignore[arg-type]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ALVERNO_MCP_OPS=true" in captured.err
    srv.run_stdio(StubBackends(ops=False), Settings())  # type: ignore[arg-type]
    assert capsys.readouterr().err == ""


def test_serve_speaks_mcp_over_stdio(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from alverno_lab.cli import app; app()", "mcp", "serve", "--fake"],
        cwd=str(REPO_ROOT),
        env={"ALVERNO_ACCESS_LOG_DIR": str(tmp_path / "access"), "PYTHONIOENCODING": "utf-8"},
    )

    async def go() -> tuple[list[str], dict[str, Any]]:
        async with Client(params, read_timeout_seconds=120) as client:
            names = [t.name for t in (await client.list_tools()).tools]
            got = await client.call_tool("hr_policy_ask", {"question": "Quel jour est férié ?"})
            return names, got.structured_content or {}

    names, payload = anyio.run(go)
    assert names == list(srv.tool_names(ops=False))
    assert set(payload) == {"answer", "refused", "refusal_reason", "citations"}
    lines = [
        json.loads(line)
        for path in sorted((tmp_path / "access").glob("hr-*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [line["channel"] for line in lines] == ["mcp"]


def test_no_telemetry_exporter_is_installed() -> None:
    # The SDK depends on the OpenTelemetry API only: without the SDK, spans go nowhere.
    assert importlib.util.find_spec("opentelemetry.sdk") is None
