"""Runtime configuration, read from ``ALVERNO_*`` environment variables or ``.env``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RerankerChoice = Literal["auto", "cross-encoder", "none"]

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class RemoteOllamaError(ValueError):
    """``ollama_url`` or an Ollama cloud model points off the workstation without the opt-in."""


class Settings(BaseSettings):
    """All tunable parameters of the lab. Defaults are the documented baseline."""

    model_config = SettingsConfigDict(
        env_prefix="ALVERNO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama backend (local only). A remote host would send questions and extracts off
    # the workstation (Law 25, docs/loi25.md): it needs ALVERNO_ALLOW_REMOTE_OLLAMA=true.
    ollama_url: str = "http://localhost:11434"
    allow_remote_ollama: bool = False
    # Best measured model on the gold set (README results).
    chat_model: str = "gemma4:e4b"
    embed_model: str = "qwen3-embedding:0.6b"
    request_timeout: float = Field(default=120.0, gt=0)
    keep_alive: str = "10m"
    num_ctx: int = Field(default=8192, ge=512)
    # Context of the embedding model, sent with every request. Without it, Ollama
    # applies its server default (OLLAMA_CONTEXT_LENGTH, up to 32,768 tokens for
    # qwen3-embedding): the embedder then evicts the chat model from an 8 GB card at
    # every question (docs/adr/0005). Chunks (1,200 characters) need far less.
    embed_num_ctx: int = Field(default=2048, ge=512)
    embed_batch_size: int = Field(default=32, ge=1)
    embed_query_prefix: str = ""

    # Optional cross-encoder reranker (extra ``rerank``).
    reranker: RerankerChoice = "auto"
    rerank_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    # Paths.
    corpus_dir: Path = Path("data/corpus")
    eval_path: Path = Path("data/eval/quality_gold.jsonl")
    index_path: Path = Path(".cache/index.npz")
    reports_dir: Path = Path("reports")
    thresholds_path: Path = Path("evals/thresholds.yaml")

    # Chunking and retrieval.
    max_chunk_chars: int = Field(default=1200, ge=200)
    max_sensitivity: int = Field(default=2, ge=1, le=5)
    top_k: int = Field(default=20, ge=1)
    final_k: int = Field(default=5, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    hash_dim: int = Field(default=256, ge=16)

    # No-match gate. The signal depends on the reranker in use (see retrieval.py):
    # cross-encoder logit, cosine similarity, or lexical coverage in --fake mode.
    no_match_threshold: float = 0.0
    vector_no_match_threshold: float = 0.55
    fake_no_match_threshold: float = 0.5

    # Answer acceptance.
    min_confidence: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_ollama_is_local(self) -> Self:
        host = (urlsplit(self.ollama_url).hostname or "").lower()
        if host in LOCAL_HOSTS:
            return self
        if not self.allow_remote_ollama:
            raise RemoteOllamaError(
                f"ollama_url {self.ollama_url!r} is not local (localhost, 127.0.0.1, ::1); "
                "questions and extracts would be sent off the workstation. "
                "Set ALVERNO_ALLOW_REMOTE_OLLAMA=true only after a Law 25 assessment."
            )
        print(
            f"Warning: remote Ollama backend {self.ollama_url!r}: questions and document "
            "extracts are sent off this workstation (see docs/loi25.md).",
            file=sys.stderr,
        )
        return self

    def check_model_is_local(self, name: str) -> str:
        """Return ``name``; an Ollama cloud model needs the same opt-in as a remote host.

        Ollama serves its cloud models (names ending in ``:cloud`` or ``-cloud``) through
        the local URL but runs them on its hosted service. The check relies on the name.
        """
        if not name.strip().lower().endswith((":cloud", "-cloud")):
            return name
        if not self.allow_remote_ollama:
            raise RemoteOllamaError(
                f"model {name!r} is an Ollama cloud model: questions and extracts would be "
                "sent off the workstation. "
                "Set ALVERNO_ALLOW_REMOTE_OLLAMA=true only after a Law 25 assessment."
            )
        print(
            f"Warning: Ollama cloud model {name!r}: questions and document extracts are "
            "sent off this workstation (see docs/loi25.md).",
            file=sys.stderr,
        )
        return name
