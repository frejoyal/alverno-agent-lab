"""Settings: local-only Ollama backend (Law 25)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from alverno_lab.config import RemoteOllamaError, Settings
from alverno_lab.pipeline import make_chat, make_embedder


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_local_ollama_urls_are_accepted(url: str) -> None:
    assert Settings(ollama_url=url).ollama_url == url


def test_remote_ollama_url_is_refused_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVERNO_OLLAMA_URL", "https://llm.alverno.example")
    with pytest.raises(ValidationError, match="not local"):
        Settings()


def test_remote_ollama_needs_explicit_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(ollama_url="http://gpu.alverno.example:11434", allow_remote_ollama=True)
    assert settings.allow_remote_ollama
    assert "sent off this workstation" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["qwen3.5:cloud", "gpt-oss:120b-cloud"])
def test_ollama_cloud_model_needs_explicit_opt_in(name: str) -> None:
    # Regression (Law 25): Ollama serves its cloud models through the local URL, so the
    # host check let --model send questions and extracts off the workstation.
    with pytest.raises(RemoteOllamaError, match="cloud model"):
        make_chat(Settings(), fake=False, model=name)
    with pytest.raises(RemoteOllamaError, match="cloud model"):
        make_embedder(Settings(embed_model=name), fake=False)
    assert Settings(allow_remote_ollama=True).check_model_is_local(name) == name
    assert Settings().check_model_is_local("gemma4:e4b") == "gemma4:e4b"


def test_chat_answer_length_is_capped() -> None:
    # Regression (ADR 0020, précision du 2026-11-11): a looping generation held the single
    # Ollama slot for about 15 minutes and every queued request timed out.
    assert Settings().num_predict == 1024
    chat = make_chat(Settings(num_predict=256), fake=False, model="gemma4:e4b")
    assert getattr(chat, "num_predict", None) == 256
