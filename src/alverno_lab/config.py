"""Runtime configuration, read from ``ALVERNO_*`` environment variables or ``.env``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RerankerChoice = Literal["auto", "cross-encoder", "none"]
Module = Literal["quality", "hr", "service", "maint"]

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
    # Host names of the same machine that count as local, for docker compose only
    # (docs/adr/0019): the agents reach Ollama as "ollama" on an internal network with
    # no way out. Unlike ALVERNO_ALLOW_REMOTE_OLLAMA, it never opens the cloud models.
    trusted_ollama_hosts: frozenset[str] = frozenset()
    # Best measured model on the gold set (README results).
    chat_model: str = "gemma4:e4b"
    embed_model: str = "qwen3-embedding:0.6b"
    request_timeout: float = Field(default=120.0, gt=0)
    keep_alive: str = "10m"
    num_ctx: int = Field(default=8192, ge=512)
    # Cap on the tokens of one answer: a looping generation stops instead of holding the
    # single Ollama slot until the context is full (ADR 0020, précision du 2026-11-11).
    num_predict: int = Field(default=1024, ge=64)
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

    # Module 2 (HR) data, kept apart from the Quality corpus and index (docs/adr/0007).
    hr_corpus_dir: Path = Path("data/hr/corpus")
    hr_eval_path: Path = Path("data/eval/hr_gold.jsonl")
    hr_index_path: Path = Path(".cache/hr/index.npz")
    hr_roles_path: Path = Path("data/hr/org_roles.yaml")
    hr_contacts_path: Path = Path("data/hr/contacts.yaml")
    pii_eval_path: Path = Path("data/eval/pii_detect.jsonl")
    # HR no-match gate (docs/adr/0011). Fake: lowest best coverage of an answerable gold
    # item (0.125) minus 0.02, floored to 0.05 and clamped to [0.20, 0.60]. Ollama:
    # lowest best cosine of an answerable gold item (0.497985, h031) minus 0.02,
    # floored to the hundredth and clamped to [0.40, 0.60]: 0.47 (measured 2026-11-01).
    hr_vector_no_match_threshold: float = 0.47
    hr_fake_no_match_threshold: float = 0.2

    # Law 25 layer. A role or group of fewer than k people is a small group (lab
    # convention, never below 5). The access log keeps day files for at most 90 days.
    small_group_k: int = Field(default=5, ge=5)
    access_log_dir: Path = Path("logs/access")
    access_log_retention_days: int = Field(default=90, ge=1, le=90)

    # Module 3 (customer service 3a, maintenance 3b): fictional fixtures of the simulated
    # ERP and GMAO, their business vocabulary and the gold sets (docs/adr/0012).
    ops_erp_path: Path = Path("data/eval/ops/erp.yaml")
    ops_gmao_path: Path = Path("data/eval/ops/gmao.yaml")
    ops_vocabulary_path: Path = Path("data/eval/ops/vocabulary.yaml")
    svc_eval_path: Path = Path("data/eval/svc_gold.jsonl")
    maint_eval_path: Path = Path("data/eval/maint_gold.jsonl")
    # Edit rounds of a draft (at most 2, docs/adr/0013); work orders read per diagnosis
    # (8, at most 12) over the last 548 days (docs/adr/0012).
    ops_max_edit_rounds: int = Field(default=2, ge=1, le=2)
    ops_max_work_orders: int = Field(default=8, ge=1, le=12)
    ops_history_days: int = Field(default=548, ge=1)
    # Module 5a, MCP server (docs/adr/0017): stdio only, so no address and no port. The
    # module 3 tools stay closed unless ALVERNO_MCP_OPS=true, and only with a local client.
    mcp_ops: bool = False
    mcp_max_input_chars: int = Field(default=500, ge=1, le=2000)
    mcp_eval_path: Path = Path("data/eval/mcp_gold.jsonl")
    # Module 5b, management orchestrator (docs/adr/0018).
    dir_eval_path: Path = Path("data/eval/dir_gold.jsonl")

    @model_validator(mode="after")
    def _check_ollama_is_local(self) -> Self:
        host = (urlsplit(self.ollama_url).hostname or "").lower()
        if host in LOCAL_HOSTS or host in {h.lower() for h in self.trusted_ollama_hosts}:
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

    def for_module(self, module: Module) -> Settings:
        """Settings of one module: ``hr`` swaps in the HR corpus, gold set, index and
        no-match thresholds; ``service`` and ``maint`` swap in their gold set
        (``svc_eval_path``, ``maint_eval_path``); ``quality`` returns an unchanged copy.

        ``hr`` also turns the reranker off: its gate threshold (``no_match_threshold``)
        was calibrated for Quality only, and the HR module has no reranker calibration
        (docs/adr/0011). The HR gate therefore always uses the cosine (or, in fake mode,
        the coverage) threshold that ADR 0011 calibrates.
        """
        if module == "quality":
            return self.model_copy()
        if module == "service":
            return self.model_copy(update={"eval_path": self.svc_eval_path})
        if module == "maint":
            return self.model_copy(update={"eval_path": self.maint_eval_path})
        if module != "hr":
            raise ValueError(f"unknown module {module!r} (quality, hr, service or maint)")
        return self.model_copy(
            update={
                "corpus_dir": self.hr_corpus_dir,
                "eval_path": self.hr_eval_path,
                "index_path": self.hr_index_path,
                "vector_no_match_threshold": self.hr_vector_no_match_threshold,
                "fake_no_match_threshold": self.hr_fake_no_match_threshold,
                "reranker": "none",
            }
        )

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
