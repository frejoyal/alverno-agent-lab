"""Hybrid retrieval: BM25 + vectors fused with RRF, optional cross-encoder, no-match gate."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from alverno_lab.corpus import Chunk
from alverno_lab.embeddings import Embedder
from alverno_lab.index import BM25Index, VectorIndex
from alverno_lab.text import tokenize

RERANK_WEIGHT = 0.45
RRF_WEIGHT = 0.55
CONSENSUS_BONUS = 0.20
# Superseded versions rank below current ones. When the query names the archived
# version ("version 2.1", "v2.1") or its doc_id, a much smaller penalty applies so that
# history questions still retrieve it (the current version usually stays first).
SUPERSEDED_PENALTY = 0.50
NAMED_VERSION_PENALTY = 0.10


def names_version(query: str, chunk: Chunk) -> bool:
    """True when ``query`` explicitly names this chunk's archived version or doc_id."""
    if chunk.doc_id.lower() in query.lower():
        return True
    if not chunk.version:
        return False
    pattern = r"\b(?:version|v\.?)\s*" + re.escape(chunk.version) + r"(?![\w.]*\d)"
    return re.search(pattern, query, re.IGNORECASE) is not None


def superseded_penalty(query: str, chunk: Chunk) -> float:
    """Ranking penalty of a chunk: 0 for current documents."""
    if not chunk.superseded_by:
        return 0.0
    return NAMED_VERSION_PENALTY if names_version(query, chunk) else SUPERSEDED_PENALTY


GateSignal = Literal["rerank", "cosine", "coverage"]


class Hit(BaseModel):
    """One retrieved chunk with every intermediate score, for audit and traces."""

    chunk: Chunk
    score: float
    rank_vec: int | None = None
    rank_bm25: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    vector_score: float | None = None
    bm25_score: float | None = None
    coverage: float = 0.0


def rrf_fuse(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: ``score(d) = sum 1 / (k + rank)`` with ranks from 1.

    Duplicates inside one ranking count once (best rank). Ties keep first-seen order.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    order = {item: i for i, item in enumerate(scores)}
    return sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def min_max(values: Sequence[float]) -> list[float]:
    """Min-max normalise to [0, 1]; a constant list maps to 1.0 (as in the original code)."""
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def query_coverage(query: str, text: str) -> float:
    """Share of distinct query tokens present in ``text`` (0 when the query has none)."""
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    return len(query_tokens & set(tokenize(text))) / len(query_tokens)


@runtime_checkable
class Reranker(Protocol):
    """Scores (query, passage) pairs; higher is more relevant."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Return one score per text."""
        ...


class NoopReranker:
    """Keeps the RRF order: every passage gets the same score."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Return zeros."""
        del query
        return [0.0] * len(texts)


class RerankerUnavailableError(RuntimeError):
    """sentence-transformers is not installed (extra ``rerank``)."""


class CrossEncoderReranker:
    """Multilingual cross-encoder from sentence-transformers (optional extra ``rerank``)."""

    def __init__(
        self, model_name: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", max_chars: int = 6000
    ) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise RerankerUnavailableError(
                "the cross-encoder reranker needs the optional extra: uv sync --extra rerank"
            ) from exc
        self.model_name = model_name
        self.max_chars = max_chars
        # sentence-transformers ships without type hints.
        self._model: Any = CrossEncoder(model_name)

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Return raw cross-encoder logits."""
        if not texts:
            return []
        pairs = [(query, text[: self.max_chars]) for text in texts]
        return [float(s) for s in self._model.predict(pairs)]


class HybridRetriever:
    """BM25 + dense retrieval, RRF fusion, optional reranking and a no-match gate.

    Gate signal by reranker:

    * cross-encoder -> best raw rerank score (``no_match_threshold``, logit scale);
    * Noop with real embeddings -> best cosine similarity;
    * Noop in ``--fake`` mode -> best lexical query coverage among fused hits,
      because RRF scores are rank-based and carry no absolute relevance.
    """

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Embedder,
        vector_index: VectorIndex,
        bm25_index: BM25Index,
        *,
        reranker: Reranker | None = None,
        rrf_k: int = 60,
        candidate_k: int = 20,
        final_k: int = 5,
        no_match_threshold: float = 0.0,
        gate_signal: GateSignal | None = None,
        query_prefix: str = "",
    ) -> None:
        if [c.chunk_id for c in chunks] != vector_index.chunk_ids:
            raise ValueError("vector index rows do not match the chunk list")
        self.chunks = list(chunks)
        self.embedder = embedder
        self.vector_index = vector_index
        self.bm25_index = bm25_index
        self.reranker: Reranker = reranker or NoopReranker()
        self.rrf_k = rrf_k
        self.candidate_k = candidate_k
        self.final_k = final_k
        self.no_match_threshold = no_match_threshold
        self.query_prefix = query_prefix
        if gate_signal is None:
            gate_signal = "cosine" if isinstance(self.reranker, NoopReranker) else "rerank"
        self.gate_signal: GateSignal = gate_signal

    @property
    def uses_reranker(self) -> bool:
        """True when a real reranker is plugged in."""
        return not isinstance(self.reranker, NoopReranker)

    def search(self, query: str, top_k: int | None = None) -> list[Hit]:
        """Return the ``top_k`` (default ``final_k``) best hits from ``candidate_k`` candidates."""
        limit = top_k or self.final_k
        if not query.strip() or not self.chunks:
            return []
        query_vec = self.embedder.embed([self.query_prefix + query])
        vec = self.vector_index.search(query_vec[0], self.candidate_k)
        bm25 = self.bm25_index.search(query, self.candidate_k)
        vec_ids = [self.chunks[i].chunk_id for i, _ in vec]
        bm25_ids = [self.chunks[i].chunk_id for i, _ in bm25]
        vec_rank = {cid: r for r, cid in enumerate(vec_ids, start=1)}
        bm25_rank = {cid: r for r, cid in enumerate(bm25_ids, start=1)}
        vec_score = {self.chunks[i].chunk_id: s for i, s in vec}
        bm25_score = {self.chunks[i].chunk_id: s for i, s in bm25}
        by_id = {c.chunk_id: c for c in self.chunks}

        fused = rrf_fuse([vec_ids, bm25_ids], k=self.rrf_k)[: self.candidate_k]
        if not fused:
            return []
        candidates = [by_id[cid] for cid, _ in fused]
        rrf_scores = [s for _, s in fused]
        rerank = self.reranker.score(query, [c.indexed_text for c in candidates])
        rerank_norm = min_max(rerank)
        rrf_norm = min_max(rrf_scores)

        hits: list[Hit] = []
        for i, chunk in enumerate(candidates):
            cid = chunk.chunk_id
            consensus = (
                CONSENSUS_BONUS if vec_rank.get(cid) == 1 and bm25_rank.get(cid) == 1 else 0.0
            )
            penalty = superseded_penalty(query, chunk)
            hits.append(
                Hit(
                    chunk=chunk,
                    score=RERANK_WEIGHT * rerank_norm[i]
                    + RRF_WEIGHT * rrf_norm[i]
                    + consensus
                    - penalty,
                    rank_vec=vec_rank.get(cid),
                    rank_bm25=bm25_rank.get(cid),
                    rrf_score=rrf_scores[i],
                    rerank_score=rerank[i] if self.uses_reranker else None,
                    vector_score=vec_score.get(cid),
                    bm25_score=bm25_score.get(cid),
                    coverage=query_coverage(query, chunk.indexed_text),
                )
            )
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def relevance(self, hits: Sequence[Hit]) -> float:
        """Absolute relevance of the best hit according to ``gate_signal``."""
        if not hits:
            return float("-inf")
        if self.gate_signal == "rerank":
            return max(
                (h.rerank_score for h in hits if h.rerank_score is not None),
                default=float("-inf"),
            )
        if self.gate_signal == "coverage":
            return max(h.coverage for h in hits)
        return max((h.vector_score for h in hits if h.vector_score is not None), default=0.0)

    def is_no_match(self, hits: Sequence[Hit]) -> bool:
        """No-match barrier: true when even the best hit is below the threshold."""
        return not hits or self.relevance(hits) < self.no_match_threshold
