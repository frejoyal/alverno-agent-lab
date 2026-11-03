"""Ollama clients against httpx mock transports (no network)."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
from pydantic import BaseModel

from alverno_lab.embeddings import OllamaEmbedder
from alverno_lab.llm import FakeChat, LLMOutputError, OllamaChat
from alverno_lab.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)


class Out(BaseModel):
    answer: str
    confidence: float


def test_ollama_chat_payload_and_stats() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        seen.append(json.loads(request.content))
        body = {
            "message": {"role": "assistant", "content": '{"answer": "ok", "confidence": 0.5}'},
            "prompt_eval_count": 42,
            "eval_count": 7,
        }
        return httpx.Response(200, json=body)

    chat = OllamaChat("qwen-test", transport=httpx.MockTransport(handler))
    out = chat.complete_json("sys", "user", Out)
    assert out == Out(answer="ok", confidence=0.5)
    # The reset request first, then the call itself (see the reset test below).
    assert len(seen) == 2
    payload = seen[1]
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["format"] == Out.model_json_schema()
    assert payload["options"] == {"temperature": 0, "num_ctx": 8192}
    assert payload["keep_alive"] == "10m"
    assert chat.stats.calls == 1
    assert (chat.stats.prompt_tokens, chat.stats.completion_tokens) == (42, 7)
    assert chat.model_name == "qwen-test"


def test_ollama_chat_retries_then_fails_on_invalid_json() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "not json"}})

    chat = OllamaChat("m", transport=httpx.MockTransport(handler), max_retries=1)
    with pytest.raises(LLMOutputError):
        chat.complete_json("s", "u", Out)
    # Each attempt is preceded by the reset request.
    assert len(seen) == 4
    # Regression: the retry resent an identical payload; it now shows the error.
    retry = seen[3]["messages"]
    assert isinstance(retry, list) and len(retry) == 4
    assert retry[2] == {"role": "assistant", "content": "not json"}
    assert "JSON valide" in retry[3]["content"]
    assert "JSON: Invalid JSON" in retry[3]["content"]


def test_ollama_chat_retry_names_the_invalid_field() -> None:
    # Regression: only the "1 validation error for Out" header reached the model and the
    # final error, without the field or the reason.
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = '{"answer": "ok", "confidence": "haute"}'
        return httpx.Response(200, json={"message": {"content": content}})

    chat = OllamaChat("m", transport=httpx.MockTransport(handler), max_retries=1)
    with pytest.raises(LLMOutputError, match="confidence: Input should be a valid number"):
        chat.complete_json("s", "u", Out)
    retry = seen[3]["messages"]
    assert isinstance(retry, list)
    assert "confidence: Input should be a valid number" in retry[3]["content"]
    # The invalid value itself is not repeated.
    assert "haute" not in retry[3]["content"]


def test_ollama_chat_resets_the_prompt_cache_before_each_call() -> None:
    # Regression (ADR 0016, non-régression): Ollama reused the cached prompt prefix of
    # the previous request, so at temperature 0 the same request could cite another
    # chunk depending on what ran before. A fixed request now precedes each call.
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = {"message": {"content": '{"answer": "ok", "confidence": 1}'}}
        return httpx.Response(200, json={**body, "prompt_eval_count": 3, "eval_count": 1})

    chat = OllamaChat("m", transport=httpx.MockTransport(handler), keep_alive="5m", num_ctx=4096)
    chat.complete_json("s1", "u1", Out)
    chat.complete_json("s2", "u2", Out)
    resets, calls = seen[0::2], seen[1::2]
    assert [c["messages"][0]["content"] for c in calls] == ["s1", "s2"]  # type: ignore[index]
    for reset in resets:
        assert reset == {
            "model": "m",
            "messages": [{"role": "user", "content": "."}],
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 1},
        }
    # Only the real calls count in the statistics.
    assert (chat.stats.calls, chat.stats.prompt_tokens) == (2, 6)


def test_ollama_chat_reset_can_be_turned_off() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": '{"answer": "a", "confidence": 1}'}}
        )

    chat = OllamaChat("m", transport=httpx.MockTransport(handler), reset_prompt_cache=False)
    chat.complete_json("s", "u", Out)
    assert len(seen) == 1 and "format" in seen[0]


def test_ollama_chat_strips_code_fences() -> None:
    content = '```json\n{"answer": "a", "confidence": 1}\n```'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": content}})

    chat = OllamaChat("m", transport=httpx.MockTransport(handler))
    assert chat.complete_json("s", "u", Out).answer == "a"


def _raise(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    ("transport", "error", "text"),
    [
        (_raise(httpx.ConnectError("refused")), OllamaUnavailableError, "not reachable"),
        (_raise(httpx.ReadTimeout("slow")), OllamaTimeoutError, "did not answer"),
        (
            httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "not found"})),
            OllamaModelNotFoundError,
            "ollama pull",
        ),
        (httpx.MockTransport(lambda r: httpx.Response(500, text="boom")), OllamaError, "500"),
        (httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")), OllamaError, "JSON"),
        (httpx.MockTransport(lambda r: httpx.Response(200, json=[1])), OllamaError, "object"),
    ],
)
def test_ollama_errors_are_clear(
    transport: httpx.MockTransport, error: type[Exception], text: str
) -> None:
    chat = OllamaChat("m", transport=transport)
    with pytest.raises(error, match=text):
        chat.complete_json("s", "u", Out)


def test_local_server_ignores_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (Law 25): a proxy from the environment or the Windows registry received
    # the questions and extracts sent to localhost.
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.alverno.example:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    for url in ("http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"):
        assert OllamaClient(url)._client.trust_env is False
    # A remote server is an explicit opt-in: it keeps the network settings of the
    # environment (proxy, certificate authority).
    assert OllamaClient("https://llm.alverno.example")._client.trust_env is True


def test_ollama_embedder_batches_and_normalises() -> None:
    batches: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        data = json.loads(request.content)
        # Regression: without an explicit context, Ollama used its server default and
        # the embedder evicted the chat model from video memory at every question.
        assert data["options"] == {"num_ctx": 2048}
        batches.append(data["input"])
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0] for _ in data["input"]]})

    emb = OllamaEmbedder("e", batch_size=2, transport=httpx.MockTransport(handler))
    out = emb.embed(["a", "b", "c"])
    assert batches == [["a", "b"], ["c"]]
    assert out.shape == (3, 2)
    assert np.allclose(out[0], [0.6, 0.8])
    assert emb.model_name == "e"
    assert emb.embed([]).shape[0] == 0


def test_ollama_embedder_rejects_bad_response() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(OllamaError, match="unexpected"):
        OllamaEmbedder("e", transport=transport).embed(["a"])


def test_fake_chat_script_order_and_callable() -> None:
    chat = FakeChat(
        [{"answer": "un", "confidence": 1}, lambda s, u, schema: {"answer": u, "confidence": 0}]
    )
    assert chat.complete_json("s", "x", Out).answer == "un"
    assert chat.complete_json("s", "deux", Out).answer == "deux"
    assert chat.stats.calls == 2
    assert chat.model_name == "fake-chat"
