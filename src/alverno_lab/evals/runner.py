"""Run the gold set against a retriever or the full agent and write reports."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from alverno_lab.agents.quality.gate import gate_decision
from alverno_lab.agents.quality.graph import QualityAgent, unique
from alverno_lab.evals.dataset import GoldItem
from alverno_lab.evals.metrics import (
    context_recall,
    hit_at_k,
    key_fact_recall,
    mean,
    percentile,
    reciprocal_rank,
    safe_ratio,
)
from alverno_lab.ollama import OllamaError, OllamaModelNotFoundError, OllamaUnavailableError
from alverno_lab.retrieval import HybridRetriever

EvalMode = Literal["retrieval", "full"]


class ItemResult(BaseModel):
    """Per-question outcome."""

    id: str
    type: str
    should_refuse: bool
    refused: bool
    refusal_reason: str | None = None
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    expected_doc_ids: list[str] = Field(default_factory=list)
    hit_at_1: float | None = None
    hit_at_3: float | None = None
    reciprocal_rank: float | None = None
    relevance: float | None = None
    answer: str | None = None
    citations: list[str] = Field(default_factory=list)
    dropped_citations: list[str] = Field(default_factory=list)
    key_fact_recall: float | None = None
    # Share of key facts present in the retrieved extracts (before the LLM).
    context_recall: float | None = None
    latency_ms: float = 0.0
    # Transport failure (timeout, HTTP error): excluded from quality metrics, counted
    # in ``error_rate``, never as a refusal.
    error: str | None = None


class EvalReport(BaseModel):
    """Aggregated metrics plus per-item details."""

    mode: EvalMode
    backend: str
    label: str = ""
    created_at: str
    config: dict[str, str] = Field(default_factory=dict)
    n_items: int
    n_answerable: int
    n_traps: int
    metrics: dict[str, float]
    items: list[ItemResult]

    @property
    def profile(self) -> str:
        """Threshold profile name, e.g. ``retrieval-fake``."""
        return f"{self.mode}-{self.backend}"


def _evaluate_retrieval(item: GoldItem, retriever: HybridRetriever) -> ItemResult:
    started = time.perf_counter()
    hits = retriever.search(item.question)
    decision = gate_decision(item.question, hits, retriever)
    latency = (time.perf_counter() - started) * 1000
    scored = _scored(
        item,
        retrieved=unique(h.chunk.doc_id for h in hits),
        refused=not decision.allowed,
        reason=decision.reason,
        relevance=decision.relevance,
        latency=latency,
    )
    if not item.should_refuse:
        contexts = [h.chunk.indexed_text for h in hits]
        scored.context_recall = context_recall(contexts, item.must_include)
    return scored


def _evaluate_full(item: GoldItem, agent: QualityAgent) -> ItemResult:
    result = agent.ask(item.question)
    scored = _scored(
        item,
        retrieved=result.retrieved_doc_ids,
        refused=result.refused,
        reason=result.refusal_reason,
        relevance=result.relevance,
        latency=result.latency_ms,
    )
    scored.answer = result.answer
    scored.citations = result.citations
    scored.dropped_citations = result.dropped_citations
    if not item.should_refuse:
        scored.key_fact_recall = (
            0.0 if result.refused else key_fact_recall(result.answer, item.must_include)
        )
        by_id = {c.chunk_id: c for c in agent.retriever.chunks}
        contexts = [by_id[cid].indexed_text for cid in result.retrieved_chunk_ids if cid in by_id]
        scored.context_recall = context_recall(contexts, item.must_include)
    return scored


def _errored(item: GoldItem, exc: Exception) -> ItemResult:
    return ItemResult(
        id=item.id,
        type=item.type,
        should_refuse=item.should_refuse,
        refused=False,
        expected_doc_ids=item.expected_doc_ids,
        error=f"{type(exc).__name__}: {exc}",
    )


def _scored(
    item: GoldItem,
    *,
    retrieved: list[str],
    refused: bool,
    reason: str | None,
    relevance: float | None,
    latency: float,
) -> ItemResult:
    result = ItemResult(
        id=item.id,
        type=item.type,
        should_refuse=item.should_refuse,
        refused=refused,
        refusal_reason=reason,
        retrieved_doc_ids=retrieved,
        expected_doc_ids=item.expected_doc_ids,
        relevance=relevance if relevance is None or relevance > float("-inf") else None,
        latency_ms=latency,
    )
    if not item.should_refuse:
        result.hit_at_1 = hit_at_k(retrieved, item.expected_doc_ids, 1)
        result.hit_at_3 = hit_at_k(retrieved, item.expected_doc_ids, 3)
        result.reciprocal_rank = reciprocal_rank(retrieved, item.expected_doc_ids)
    return result


def aggregate(items: Sequence[ItemResult], mode: EvalMode) -> dict[str, float]:
    """Compute report metrics from per-item results."""
    scored = [r for r in items if r.error is None]
    answerable = [r for r in scored if not r.should_refuse]
    traps = [r for r in scored if r.should_refuse]
    traps_refused = sum(1 for r in traps if r.refused)
    answerable_refused = sum(1 for r in answerable if r.refused)
    latencies = [r.latency_ms for r in scored]
    metrics = {
        "hit_at_1": mean([r.hit_at_1 or 0.0 for r in answerable]),
        "hit_at_3": mean([r.hit_at_3 or 0.0 for r in answerable]),
        "mrr": mean([r.reciprocal_rank or 0.0 for r in answerable]),
        "context_recall": mean([r.context_recall or 0.0 for r in answerable]),
        "trap_refusal_accuracy": safe_ratio(traps_refused, len(traps)),
        "false_answer_rate": safe_ratio(len(traps) - traps_refused, len(traps)),
        "over_refusal_rate": safe_ratio(answerable_refused, len(answerable)),
        "decision_accuracy": safe_ratio(
            traps_refused + len(answerable) - answerable_refused, len(scored)
        ),
        "error_rate": safe_ratio(len(items) - len(scored), len(items)),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
    }
    if mode == "full":
        valid = sum(len(r.citations) for r in scored if not r.refused)
        dropped = sum(len(r.dropped_citations) for r in scored)
        # Nothing cited at all is not a perfect score: an agent that refuses everything
        # must not pass the citation bound trivially.
        metrics["citation_validity_rate"] = safe_ratio(valid, valid + dropped)
        metrics["key_fact_recall"] = mean([r.key_fact_recall or 0.0 for r in answerable])
    # 6 decimals: rounding to 4 here, then to 3 for display, turned 0.990476 into
    # 0.9905, then 0.991.
    return {k: round(v, 6) for k, v in metrics.items()}


def run_eval(
    target: QualityAgent | HybridRetriever,
    dataset: Sequence[GoldItem],
    mode: EvalMode = "retrieval",
    *,
    backend: str = "fake",
    label: str = "",
    config: dict[str, str] | None = None,
) -> EvalReport:
    """Evaluate ``target`` on ``dataset``; ``full`` mode needs a :class:`QualityAgent`.

    An unreachable server or a missing model aborts the run; other transport errors are
    recorded per item and counted in ``error_rate``.
    """
    if mode == "full" and not isinstance(target, QualityAgent):
        raise TypeError("full mode needs a QualityAgent")
    retriever = target.retriever if isinstance(target, QualityAgent) else target
    items: list[ItemResult] = []
    for item in dataset:
        try:
            if mode == "full" and isinstance(target, QualityAgent):
                items.append(_evaluate_full(item, target))
            else:
                items.append(_evaluate_retrieval(item, retriever))
        except (OllamaUnavailableError, OllamaModelNotFoundError):
            # Server down or model not pulled: every item would fail the same way.
            raise
        except OllamaError as exc:
            # One timeout must not abort the whole run; LLMOutputError is handled in the
            # graph (llm_error refusal), so only transport failures land here.
            items.append(_errored(item, exc))
    return EvalReport(
        mode=mode,
        backend=backend,
        label=label,
        created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        config=config or {},
        n_items=len(items),
        n_answerable=sum(1 for r in items if not r.should_refuse),
        n_traps=sum(1 for r in items if r.should_refuse),
        metrics=aggregate(items, mode),
        items=items,
    )


METRIC_LABELS = {
    "hit_at_1": "Retrieval hit@1",
    "hit_at_3": "Retrieval hit@3",
    "mrr": "Retrieval MRR",
    "context_recall": "Context recall (key facts in retrieved extracts)",
    "trap_refusal_accuracy": "Trap refusal accuracy",
    "false_answer_rate": "False answer rate on traps",
    "over_refusal_rate": "Over-refusal rate (answerable refused)",
    "decision_accuracy": "Answer/refuse decision accuracy",
    "citation_validity_rate": "Citation validity rate",
    "key_fact_recall": "Key-fact recall",
    "error_rate": "Error rate (transport failures)",
    "latency_p50_ms": "Latency p50 (ms)",
    "latency_p95_ms": "Latency p95 (ms)",
}


def report_markdown(report: EvalReport) -> str:
    """Render a report as Markdown."""
    lines = [
        f"# Evaluation report — {report.mode} ({report.backend})",
        "",
        f"- Created: {report.created_at}",
        f"- Items: {report.n_items} ({report.n_answerable} answerable, {report.n_traps} traps)",
    ]
    if report.label:
        lines.append(f"- Label: {report.label}")
    lines.extend(f"- {k}: `{v}`" for k, v in sorted(report.config.items()))
    lines += ["", "| Metric | Value |", "|---|---|"]
    for key, value in report.metrics.items():
        # ``.4g`` would write 23503.7 ms as 2.35e+04.
        shown = f"{value:.1f}" if key.endswith("_ms") else f"{value:.4g}"
        lines.append(f"| {METRIC_LABELS.get(key, key)} | {shown} |")
    misses = [
        r
        for r in report.items
        if r.error
        or (r.should_refuse != r.refused)
        or r.hit_at_3 == 0.0
        or (r.context_recall is not None and r.context_recall < 1.0)
        or (not r.refused and r.key_fact_recall is not None and r.key_fact_recall < 1.0)
    ]
    if misses:
        lines += [
            "",
            "## Items to review",
            "",
            "| id | type | issue | retrieved |",
            "|---|---|---|---|",
        ]
        for r in misses:
            if r.error:
                issue = "error"
            elif r.should_refuse and not r.refused:
                issue = "trap answered"
            elif r.refused and not r.should_refuse:
                issue = f"over-refusal ({r.refusal_reason})"
            elif r.hit_at_3 == 0.0:
                issue = "expected doc not in top 3"
            elif r.context_recall is not None and r.context_recall < 1.0:
                issue = f"key facts missing from context ({r.context_recall:.2f})"
            else:
                issue = f"key facts missing from answer ({r.key_fact_recall:.2f})"
            lines.append(f"| {r.id} | {r.type} | {issue} | {', '.join(r.retrieved_doc_ids[:3])} |")
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, out_dir: Path, stem: str | None = None) -> tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.md`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = stem or f"eval-{report.profile}"
    json_path = out_dir / f"{name}.json"
    md_path = out_dir / f"{name}.md"
    # LF and a final newline on every platform: the reports are published.
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    md_path.write_text(report_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path
