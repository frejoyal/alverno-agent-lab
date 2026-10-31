"""Checks on the real corpus and gold set; skipped while they are not written yet."""

from __future__ import annotations

import pytest

from alverno_lab.config import Settings
from alverno_lab.evals.dataset import KNOWN_TYPES, load_dataset
from alverno_lab.evals.runner import run_eval
from alverno_lab.evals.thresholds import check_thresholds
from alverno_lab.pipeline import build_retriever

from .conftest import REPO_ROOT

CORPUS = REPO_ROOT / "data" / "corpus"
GOLD = REPO_ROOT / "data" / "eval" / "quality_gold.jsonl"


@pytest.fixture
def repo_settings() -> Settings:
    if not GOLD.exists() or not any(CORPUS.glob("*.md")):
        pytest.skip("data/eval/quality_gold.jsonl or data/corpus not available yet")
    return Settings(corpus_dir=CORPUS, eval_path=GOLD)


def test_gold_set_is_consistent_with_corpus(repo_settings: Settings) -> None:
    retriever, _ = build_retriever(repo_settings, fake=True)
    doc_ids = {c.doc_id for c in retriever.chunks}
    items = load_dataset(GOLD)
    unknown_types = {i.type for i in items} - KNOWN_TYPES
    assert not unknown_types, f"unknown question types: {unknown_types}"
    missing = {d for i in items for d in i.expected_doc_ids if d not in doc_ids}
    assert not missing, f"expected_doc_ids absent from the corpus: {sorted(missing)}"
    assert any(i.should_refuse for i in items)


def test_fake_retrieval_eval_passes_repository_gate(repo_settings: Settings) -> None:
    retriever, _ = build_retriever(repo_settings, fake=True)
    report = run_eval(retriever, load_dataset(GOLD), "retrieval")
    result = check_thresholds(report, REPO_ROOT / "evals" / "thresholds.yaml")
    assert result.passed, result.failures
