"""Shared offline fixtures: tiny corpus, hash embedder, fake chat."""

from __future__ import annotations

from pathlib import Path

import pytest

from alverno_lab.config import Settings
from alverno_lab.corpus import Chunk
from alverno_lab.pipeline import build_retriever, load_chunks
from alverno_lab.retrieval import HybridRetriever

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ignore any developer ``.env`` and ``ALVERNO_*`` variables."""
    import os

    for key in list(os.environ):
        if key.startswith("ALVERNO_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointing at the fixture corpus and eval set."""
    return Settings(
        corpus_dir=FIXTURES / "corpus",
        eval_path=FIXTURES / "eval.jsonl",
        index_path=tmp_path / "index.npz",
        reports_dir=tmp_path / "reports",
        thresholds_path=FIXTURES / "thresholds.yaml",
    )


@pytest.fixture
def chunks(settings: Settings) -> list[Chunk]:
    """Chunks of the fixture corpus."""
    return load_chunks(settings)


@pytest.fixture
def retriever(settings: Settings) -> HybridRetriever:
    """Fake-mode hybrid retriever over the fixture corpus."""
    built, _ = build_retriever(settings, fake=True)
    return built
