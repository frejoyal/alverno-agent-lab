"""Detector bench (``data/eval/pii_detect.jsonl``, profile ``privacy-detector``).

Each line gives a text and its gold spans as values; the loader turns each value into
offsets (it must occur exactly once). Reports keep item ids, types, counts and metrics
only (amendment M12): never a span value nor an item text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.evals.dataset import DatasetError
from alverno_lab.privacy import PISpan, PIType, PrivacyContext, detect_pi

Difficulty = Literal["standard", "hard"]
NameRule = Literal["N1", "N2", "N3", "N4"]
HardKind = Literal["lowercase", "common_word", "initials", "nickname"]


class GoldSpanSpec(BaseModel):
    """A gold span as written in the JSONL file (the value is never printed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: PIType
    value: str = Field(min_length=1, repr=False)
    difficulty: Difficulty
    rule: NameRule | None = None
    hard_kind: HardKind | None = None


class GoldSpan(BaseModel):
    """A gold span located in its item's text."""

    model_config = ConfigDict(frozen=True)

    type: PIType
    start: int
    end: int
    difficulty: Difficulty
    rule: NameRule | None = None
    hard_kind: HardKind | None = None


class DetectorItem(BaseModel):
    """One bench text with its located gold spans; an empty list marks a negative."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str = Field(repr=False)
    spans: tuple[GoldSpan, ...]

    @property
    def is_negative(self) -> bool:
        """Texts without any personal information."""
        return not self.spans


class _RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1, repr=False)
    spans: list[GoldSpanSpec]


def load_detector_set(path: Path) -> list[DetectorItem]:
    """Read the bench; errors name the line and the rule, never a value."""
    if not path.exists():
        raise DatasetError(f"detector set not found: {path}")
    items: list[DetectorItem] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        # No chaining below: the chained errors would show span values and item texts.
        try:
            raw = _RawItem.model_validate(json.loads(line))
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{lineno}: invalid JSON ({exc.msg})") from None
        except ValidationError as exc:
            locs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors())
            raise DatasetError(f"{path}:{lineno}: invalid item ({locs})") from None
        if raw.id in seen:
            raise DatasetError(f"{path}:{lineno}: duplicate id {raw.id}")
        spans: list[GoldSpan] = []
        for spec in raw.spans:
            if raw.text.count(spec.value) != 1:
                raise DatasetError(
                    f"{path}:{lineno}: {raw.id}: a {spec.type.value} value must occur "
                    "exactly once in the text"
                )
            start = raw.text.index(spec.value)
            spans.append(
                GoldSpan(
                    type=spec.type,
                    start=start,
                    end=start + len(spec.value),
                    difficulty=spec.difficulty,
                    rule=spec.rule,
                    hard_kind=spec.hard_kind,
                )
            )
        seen.add(raw.id)
        items.append(DetectorItem(id=raw.id, text=raw.text, spans=tuple(spans)))
    if not items:
        raise DatasetError(f"detector set is empty: {path}")
    return items


class DetectorItemResult(BaseModel):
    """Per-item outcome: types and counts only."""

    id: str
    gold_types: list[str]
    predicted_types: list[str]
    n_missed: int
    n_false: int


class DetectorReport(BaseModel):
    """Outcome of :func:`run_detector_bench`; ``profile`` is ``privacy-detector``."""

    module: Literal["privacy"] = "privacy"
    created_at: str
    dataset: str
    dataset_sha256: str
    n_items: int
    n_negatives: int
    metrics: dict[str, float]
    counts: dict[str, int]
    items: list[DetectorItemResult]

    @property
    def profile(self) -> str:
        """Threshold profile name."""
        return "privacy-detector"


def _covered(gold: GoldSpan, predicted: Sequence[PISpan]) -> bool:
    return any(p.start <= gold.start and gold.end <= p.end for p in predicted)


def _overlaps(pred: PISpan, gold: Sequence[GoldSpan]) -> bool:
    return any(pred.start < g.end and g.start < pred.end for g in gold)


def _rate(numerator: int, denominator: int) -> float:
    # An empty denominator scores 0: a metric over nothing must not pass a min bound.
    return round(numerator / denominator, 6) if denominator else 0.0


def run_detector_bench(
    items: Sequence[DetectorItem], ctx: PrivacyContext, *, dataset: str = "", sha256: str = ""
) -> DetectorReport:
    """Run :func:`detect_pi` (question mode) on every item and compute the §7 metrics."""
    tally: Counter[str] = Counter()
    per_type: Counter[tuple[str, str]] = Counter()
    results: list[DetectorItemResult] = []
    for item in items:
        predicted = detect_pi(item.text, ctx, mode="question")
        missed = 0
        for gold in item.spans:
            covered = _covered(gold, predicted)
            missed += not covered
            if gold.type is PIType.NOM:
                group = "name_standard" if gold.difficulty == "standard" else "name_hard"
            else:
                group = "structured"
            tally[f"{group}_gold"] += 1
            tally[f"{group}_covered"] += covered
            per_type[(gold.type.value, "gold")] += 1
            per_type[(gold.type.value, "covered")] += covered
        false = 0
        for pred in predicted:
            group = "name" if pred.type is PIType.NOM else "structured"
            hit = _overlaps(pred, item.spans)
            false += not hit
            tally[f"{group}_predicted"] += 1
            tally[f"{group}_true"] += hit
        if item.is_negative:
            tally["negatives"] += 1
            tally["negatives_flagged"] += bool(predicted)
        results.append(
            DetectorItemResult(
                id=item.id,
                gold_types=sorted(s.type.value for s in item.spans),
                predicted_types=sorted(p.type.value for p in predicted),
                n_missed=missed,
                n_false=false,
            )
        )
    gold_types = sorted({t for t, kind in per_type if kind == "gold"})
    metrics = {
        "structured_recall": _rate(tally["structured_covered"], tally["structured_gold"]),
        "structured_precision": _rate(tally["structured_true"], tally["structured_predicted"]),
        "name_recall_standard": _rate(tally["name_standard_covered"], tally["name_standard_gold"]),
        "name_recall_hard": _rate(tally["name_hard_covered"], tally["name_hard_gold"]),
        "name_precision": _rate(tally["name_true"], tally["name_predicted"]),
        # A max-bounded rate over no negative scores 1: it must not pass vacuously.
        "negative_flag_rate": (
            _rate(tally["negatives_flagged"], tally["negatives"]) if tally["negatives"] else 1.0
        ),
        "n_items": float(len(items)),
        "n_types": float(len(gold_types)),
    }
    for ptype in gold_types:
        metrics[f"recall_{ptype}"] = _rate(per_type[(ptype, "covered")], per_type[(ptype, "gold")])
    return DetectorReport(
        created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        dataset=dataset,
        dataset_sha256=sha256,
        n_items=len(items),
        n_negatives=tally["negatives"],
        metrics=metrics,
        counts=dict(sorted(tally.items())),
        items=results,
    )


def file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes (the ADR 0010 ledger method)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detector_markdown(report: DetectorReport) -> str:
    """Markdown summary: metrics and the ids of the items with a miss or a false span."""
    lines = [
        "# Banc du détecteur de renseignements personnels",
        "",
        f"- Created: {report.created_at}",
        f"- Dataset: `{report.dataset}` (SHA-256 `{report.dataset_sha256}`)",
        f"- Items: {report.n_items} ({report.n_negatives} negatives)",
        "",
        "| Metric | Value |",
        "|---|---|",
        *(f"| {key} | {value:.3f} |" for key, value in report.metrics.items()),
        "",
    ]
    missed = [r.id for r in report.items if r.n_missed]
    false = [r.id for r in report.items if r.n_false]
    lines.append(f"Items with a missed span: {', '.join(missed) or 'none'}.")
    lines.append(f"Items with a false span: {', '.join(false) or 'none'}.")
    return "\n".join(lines) + "\n"


def write_detector_report(report: DetectorReport, out_dir: Path) -> tuple[Path, Path]:
    """Write ``eval-privacy-detector.{json,md}`` (LF, final newline) into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"eval-{report.profile}.json"
    md_path = out_dir / f"eval-{report.profile}.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    md_path.write_text(detector_markdown(report), encoding="utf-8", newline="\n")
    return json_path, md_path
