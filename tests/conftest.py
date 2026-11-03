"""Shared offline fixtures: tiny corpus, hash embedder, fake chat."""

from __future__ import annotations

from pathlib import Path

import pytest

from alverno_lab.config import Settings
from alverno_lab.corpus import Chunk
from alverno_lab.hr_pipeline import load_privacy_context
from alverno_lab.pipeline import build_retriever, load_chunks
from alverno_lab.privacy import PrivacyContext
from alverno_lab.retrieval import HybridRetriever

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ignore any developer ``.env`` and ``ALVERNO_*`` variables, and plain CLI output.

    typer forces a colour terminal at import when ``GITHUB_ACTIONS``, ``FORCE_COLOR`` or
    ``PY_COLORS`` is set, so usage errors in CI split ``--repeats`` into styled spans;
    tests assert on plain text whatever the shell.
    """
    import os

    import typer.rich_utils

    for key in list(os.environ):
        if key.startswith("ALVERNO_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(typer.rich_utils, "FORCE_TERMINAL", False)
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


def case_id(value: object) -> str:
    """Test id of a parametrised value: its type only, never the value.

    pytest numbers repeated ids, so ``pytest -v`` and CI logs never print a synthetic
    name, SIN or phone number taken from a test case.
    """
    return type(value).__name__


HR_DATA = REPO_ROOT / "data" / "hr"
EVAL_DATA = REPO_ROOT / "data" / "eval"


def hr_repo_settings(**overrides: object) -> Settings:
    """Settings pointing at the repository HR data (tests run in a temporary directory)."""
    values: dict[str, object] = {
        "hr_corpus_dir": HR_DATA / "corpus",
        "hr_eval_path": EVAL_DATA / "hr_gold.jsonl",
        "hr_roles_path": HR_DATA / "org_roles.yaml",
        "hr_contacts_path": HR_DATA / "contacts.yaml",
        "pii_eval_path": EVAL_DATA / "pii_detect.jsonl",
        "thresholds_path": REPO_ROOT / "evals" / "thresholds.yaml",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def privacy_ctx() -> PrivacyContext:
    """Privacy context of the repository HR module (roles, mailboxes, corpus vocabulary)."""
    return load_privacy_context(hr_repo_settings())
