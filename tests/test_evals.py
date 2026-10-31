"""Evaluation metrics, runner, reports and the release gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alverno_lab.agents.quality.graph import QualityAgent
from alverno_lab.evals.dataset import DatasetError, load_dataset
from alverno_lab.evals.metrics import (
    context_recall,
    hit_at_k,
    key_fact_recall,
    mean,
    percentile,
    reciprocal_rank,
    safe_ratio,
)
from alverno_lab.evals.runner import (
    EvalReport,
    ItemResult,
    aggregate,
    report_markdown,
    run_eval,
    write_report,
)
from alverno_lab.evals.thresholds import ThresholdError, check_thresholds
from alverno_lab.llm import FakeChat
from alverno_lab.ollama import (
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from alverno_lab.retrieval import HybridRetriever

from .conftest import FIXTURES


def test_hit_at_k_and_mrr() -> None:
    ranked = ["QP-001", "QP-007", "IT-002"]
    assert hit_at_k(ranked, ["QP-007"], 1) == 0.0
    assert hit_at_k(ranked, ["QP-007"], 3) == 1.0
    assert hit_at_k(ranked, ["IT-002", "QP-001"], 1) == 1.0
    assert reciprocal_rank(ranked, ["QP-007"]) == 0.5
    assert reciprocal_rank(ranked, ["NC-001"]) == 0.0


def test_key_fact_recall_normalises() -> None:
    answer = "Le caniveau maximal est de 0.5\u00a0MM et l'épaisseur 60 µm."
    assert key_fact_recall(answer, ["0,5 mm", "60 µm"]) == 1.0
    assert key_fact_recall(answer, ["0,5 mm", "120 µm"]) == 0.5
    assert key_fact_recall(answer, []) == 1.0
    # Regression: a fact must not match inside a larger number.
    assert key_fact_recall("intervalle de 16 mois", ["6 mois"]) == 0.0
    assert key_fact_recall("max 10,5 mm", ["0,5 mm"]) == 0.0
    assert key_fact_recall("6,5 mois", ["6"]) == 0.0
    assert key_fact_recall("aux 6 mois.", ["6 mois"]) == 1.0


def test_context_recall_is_chunk_level() -> None:
    contexts = ["Couleur RAL 7035, fini texturé fin.", "Épaisseur de 70 à 110 µm."]
    assert context_recall(contexts, ["RAL 7035", "70 à 110 µm"]) == 1.0
    assert context_recall(contexts[:1], ["RAL 7035", "70 à 110 µm"]) == 0.5
    assert context_recall([], ["RAL 7035"]) == 0.0
    assert context_recall(contexts, []) == 1.0


def test_report_flags_key_facts_missing_from_context(tmp_path: Path) -> None:
    items = [
        ItemResult(
            id="q1",
            type="factual",
            should_refuse=False,
            refused=False,
            hit_at_1=1.0,
            hit_at_3=1.0,
            context_recall=0.5,
            retrieved_doc_ids=["SPEC-T"],
        ),
        ItemResult(
            id="q2",
            type="factual",
            should_refuse=False,
            refused=False,
            hit_at_1=1.0,
            hit_at_3=1.0,
            context_recall=1.0,
        ),
    ]
    metrics = aggregate(items, "retrieval")
    assert metrics["context_recall"] == 0.75
    report = EvalReport(
        mode="retrieval",
        backend="fake",
        created_at="2026-10-31T00:00:00+00:00",
        n_items=2,
        n_answerable=2,
        n_traps=0,
        metrics=metrics,
        items=items,
    )
    _, md_path = write_report(report, tmp_path)
    markdown = md_path.read_text(encoding="utf-8")
    assert "| q1 | factual | key facts missing from context (0.50) | SPEC-T |" in markdown
    assert "| q2 |" not in markdown


def test_report_flags_key_facts_missing_from_answer() -> None:
    # Regression: in full mode, an answer that dropped facts present in its context was
    # not listed, and latencies of 10 s or more were written as 2.35e+04.
    items = [
        ItemResult(
            id="q1",
            type="procedural",
            should_refuse=False,
            refused=False,
            hit_at_3=1.0,
            context_recall=1.0,
            key_fact_recall=0.5,
            retrieved_doc_ids=["QP-006"],
        ),
        ItemResult(
            id="q2",
            type="factual",
            should_refuse=False,
            refused=False,
            hit_at_3=1.0,
            context_recall=1.0,
            key_fact_recall=1.0,
        ),
    ]
    report = EvalReport(
        mode="full",
        backend="ollama",
        created_at="2026-10-31T00:00:00+00:00",
        n_items=2,
        n_answerable=2,
        n_traps=0,
        metrics=aggregate(items, "full") | {"latency_p50_ms": 23503.7, "latency_p95_ms": 31220.6},
        items=items,
    )
    markdown = report_markdown(report)
    assert "| q1 | procedural | key facts missing from answer (0.50) | QP-006 |" in markdown
    assert "| q2 |" not in markdown
    assert "| Latency p50 (ms) | 23503.7 |" in markdown
    assert "| Latency p95 (ms) | 31220.6 |" in markdown
    assert "| Key-fact recall | 0.75 |" in markdown


def test_percentile_mean_ratio() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([10.0], 95) == 10.0
    assert percentile([], 50) == 0.0
    assert percentile([1.0, 2.0], 100) == 2.0
    with pytest.raises(ValueError):
        percentile([1.0], 101)
    assert mean([]) == 0.0
    assert safe_ratio(1, 0) == 0.0


def test_aggregate_refusal_metrics() -> None:
    items = [
        ItemResult(id="a", type="factual", should_refuse=False, refused=False, hit_at_1=1.0),
        ItemResult(id="b", type="factual", should_refuse=False, refused=True, hit_at_1=0.0),
        ItemResult(id="c", type="trap_absent", should_refuse=True, refused=True),
        ItemResult(id="d", type="trap_injection", should_refuse=True, refused=False),
    ]
    metrics = aggregate(items, "retrieval")
    assert metrics["hit_at_1"] == 0.5
    assert metrics["trap_refusal_accuracy"] == 0.5
    assert metrics["false_answer_rate"] == 0.5
    assert metrics["over_refusal_rate"] == 0.5
    assert metrics["decision_accuracy"] == 0.5
    assert "citation_validity_rate" not in metrics
    assert metrics["error_rate"] == 0.0


def test_citation_validity_is_zero_when_nothing_is_cited() -> None:
    # Regression: an agent refusing everything scored a perfect 1.0.
    items = [
        ItemResult(id="a", type="factual", should_refuse=False, refused=True),
        ItemResult(id="b", type="trap_absent", should_refuse=True, refused=True),
    ]
    assert aggregate(items, "full")["citation_validity_rate"] == 0.0


def test_transport_error_is_recorded_not_counted_as_refusal(retriever: HybridRetriever) -> None:
    # Regression: one timeout aborted the whole evaluation.
    items = load_dataset(FIXTURES / "eval.jsonl")
    chat = FakeChat(error=OllamaTimeoutError("read timeout"))
    report = run_eval(QualityAgent(retriever, chat), items, "full")
    errored = [r for r in report.items if r.error]
    assert errored and all("OllamaTimeoutError" in (r.error or "") for r in errored)
    assert all(not r.refused for r in errored)
    assert report.metrics["error_rate"] > 0.0
    # Traps refused by the gate before any LLM call are still scored normally.
    assert report.metrics["false_answer_rate"] == 0.0


def test_server_down_or_missing_model_aborts_the_run(retriever: HybridRetriever) -> None:
    # Regression: both were recorded item by item, and a model that answered nothing
    # still got a row of metrics (traps "refused" by the gate).
    items = load_dataset(FIXTURES / "eval.jsonl")
    for error in (OllamaUnavailableError("down"), OllamaModelNotFoundError("missing")):
        with pytest.raises(type(error)):
            run_eval(QualityAgent(retriever, FakeChat(error=error)), items, "full")


def test_dataset_loader_validates(tmp_path: Path) -> None:
    items = load_dataset(FIXTURES / "eval.jsonl")
    assert len(items) == 6
    assert sum(i.is_trap for i in items) == 2
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid"):
        load_dataset(bad)
    line = {
        "id": "x",
        "question": "q",
        "type": "factual",
        "expected_doc_ids": ["A"],
        "must_include": ["f"],
    }
    bad.write_text(json.dumps(line) + "\n\n" + json.dumps(line) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="duplicate"):
        load_dataset(bad)
    bad.write_text(json.dumps(line | {"expected_doc_ids": []}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="expected_doc_ids"):
        load_dataset(bad)
    # Regression: without key facts, an answerable item scored 1.0 in both recalls,
    # and a misspelt key was ignored, which gave the same result.
    bad.write_text(json.dumps(line | {"must_include": []}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="without must_include"):
        load_dataset(bad)
    misspelt = {k: v for k, v in line.items() if k != "must_include"} | {"must_includes": ["f"]}
    bad.write_text(json.dumps(misspelt) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="must_includes"):
        load_dataset(bad)
    bad.write_text("\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="empty"):
        load_dataset(bad)
    with pytest.raises(DatasetError, match="not found"):
        load_dataset(tmp_path / "missing.jsonl")


def test_run_eval_retrieval_on_fixtures(retriever: HybridRetriever, tmp_path: Path) -> None:
    report = run_eval(retriever, load_dataset(FIXTURES / "eval.jsonl"), "retrieval")
    assert report.profile == "retrieval-fake"
    assert (report.n_items, report.n_answerable, report.n_traps) == (6, 4, 2)
    assert report.metrics["hit_at_1"] == 1.0
    assert report.metrics["mrr"] == 1.0
    assert report.metrics["trap_refusal_accuracy"] == 1.0
    assert report.metrics["false_answer_rate"] == 0.0
    json_path, md_path = write_report(report, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["mode"] == "retrieval"
    assert "| Retrieval hit@1 | 1 |" in md_path.read_text(encoding="utf-8")
    assert check_thresholds(report, FIXTURES / "thresholds.yaml").passed


def test_reports_are_written_with_lf_and_a_final_newline(
    retriever: HybridRetriever, tmp_path: Path
) -> None:
    # Regression: reports written on Windows had CRLF line endings, and the JSON report
    # had no final newline.
    report = run_eval(retriever, load_dataset(FIXTURES / "eval.jsonl"), "retrieval")
    for path in write_report(report, tmp_path):
        data = path.read_bytes()
        assert b"\r\n" not in data and data.endswith(b"\n"), path.name


def test_run_eval_full_with_fake_chat(retriever: HybridRetriever) -> None:
    agent = QualityAgent(retriever, FakeChat())
    report = run_eval(agent, load_dataset(FIXTURES / "eval.jsonl"), "full")
    assert report.metrics["citation_validity_rate"] == 1.0
    assert report.metrics["false_answer_rate"] == 0.0
    assert 0.0 <= report.metrics["key_fact_recall"] <= 1.0
    for r in report.items:
        if r.should_refuse:
            assert r.refused, r.id
        elif not r.refused:
            assert r.citations, r.id
    assert report.metrics["error_rate"] == 0.0
    with pytest.raises(TypeError):
        run_eval(retriever, [], "full")


def test_check_thresholds_fails_on_regression(retriever: HybridRetriever, tmp_path: Path) -> None:
    report = run_eval(retriever, load_dataset(FIXTURES / "eval.jsonl"), "retrieval")
    strict = tmp_path / "t.yaml"
    strict.write_text(
        "retrieval-fake:\n  min:\n    hit_at_1: 1.01\n    missing_metric: 0.1\n"
        "  max:\n    latency_p95_ms: -1\n",
        encoding="utf-8",
    )
    result = check_thresholds(report, strict)
    assert not result.passed
    assert result.exit_code == 1
    assert len(result.failures) == 3
    assert check_thresholds(report, strict, profile="full-ollama").failures[0].startswith("no ")
    strict.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ThresholdError):
        check_thresholds(report, strict)
    strict.write_text("p:\n  min: [1]\n", encoding="utf-8")
    with pytest.raises(ThresholdError):
        check_thresholds(report, strict)
    with pytest.raises(ThresholdError):
        check_thresholds(report, tmp_path / "missing.yaml")


def test_misspelt_or_empty_profile_fails_the_gate(
    retriever: HybridRetriever, tmp_path: Path
) -> None:
    # Regression: an unknown key ("minimum") or a profile without bounds passed the gate
    # without checking anything.
    report = run_eval(retriever, load_dataset(FIXTURES / "eval.jsonl"), "retrieval")
    path = tmp_path / "t.yaml"
    path.write_text("retrieval-fake:\n  minimum:\n    hit_at_1: 0.5\n", encoding="utf-8")
    with pytest.raises(ThresholdError, match="minimum"):
        check_thresholds(report, path)
    for body in ("retrieval-fake:\n", "retrieval-fake: {}\n", "retrieval-fake:\n  min: {}\n"):
        path.write_text(body, encoding="utf-8")
        result = check_thresholds(report, path)
        assert not result.passed and result.exit_code == 1
        assert result.failures == ["profile 'retrieval-fake' has no bounds"]


def test_repository_thresholds_file_is_valid() -> None:
    from alverno_lab.evals.thresholds import load_profiles

    profiles = load_profiles(Path(__file__).parent.parent / "evals" / "thresholds.yaml")
    assert "retrieval-fake" in profiles
    assert profiles["retrieval-fake"].min
