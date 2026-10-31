"""Pure evaluation metrics."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from alverno_lab.text import normalize_for_match


def hit_at_k(ranked_doc_ids: Sequence[str], expected: Sequence[str], k: int) -> float:
    """1.0 if any expected document is in the top ``k``, else 0.0."""
    wanted = set(expected)
    return 1.0 if any(d in wanted for d in ranked_doc_ids[:k]) else 0.0


def reciprocal_rank(ranked_doc_ids: Sequence[str], expected: Sequence[str]) -> float:
    """1 / rank of the first expected document (0.0 if absent)."""
    wanted = set(expected)
    for rank, doc_id in enumerate(ranked_doc_ids, start=1):
        if doc_id in wanted:
            return 1.0 / rank
    return 0.0


def key_fact_recall(answer: str, must_include: Sequence[str]) -> float:
    """Share of ``must_include`` facts found in the answer after normalisation."""
    if not must_include:
        return 1.0
    text = normalize_for_match(answer)
    found = sum(1 for fact in must_include if fact_in_text(normalize_for_match(fact), text))
    return found / len(must_include)


def context_recall(contexts: Sequence[str], must_include: Sequence[str]) -> float:
    """Share of ``must_include`` facts present in the retrieved extracts.

    Document-level hit@k can be perfect while the chunk that holds the answer is cut
    from the context given to the LLM; this chunk-level check catches it without an LLM.
    """
    return key_fact_recall("\n".join(contexts), must_include)


def fact_in_text(fact: str, text: str) -> bool:
    """Bounded match: ``6 mois`` must not match inside ``16 mois``, nor ``0.5`` in ``10.5``."""
    if not fact:
        return True
    pattern = r"(?<![\w.])" + re.escape(fact) + r"(?!\w|\.\d)"
    return re.search(pattern, text) is not None


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in [0, 100]); 0.0 for an empty list."""
    if not values:
        return 0.0
    if not 0 <= q <= 100:
        raise ValueError("q must be in [0, 100]")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    low, high = math.floor(pos), math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean; 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def safe_ratio(numerator: float, denominator: float) -> float:
    """``numerator / denominator`` or 0.0 when the denominator is zero."""
    return numerator / denominator if denominator else 0.0
