"""Gold evaluation set loader (``data/eval/quality_gold.jsonl``)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

KNOWN_TYPES = frozenset(
    {
        "factual",
        "procedural",
        "numeric",
        "multi_doc",
        "version",
        "trap_absent",
        "trap_injection",
        "trap_out_of_scope",
    }
)


class DatasetError(ValueError):
    """Raised for an unreadable or invalid gold set."""


class GoldItem(BaseModel):
    """One gold question."""

    # A misspelt key ("must_includes") must fail instead of scoring the item 1.0 by default.
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    type: str
    expected_doc_ids: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    should_refuse: bool = False
    notes: str = ""

    @property
    def is_trap(self) -> bool:
        """Traps are questions the agent must refuse."""
        return self.should_refuse


def load_dataset(path: Path) -> list[GoldItem]:
    """Read a JSONL gold set; blank lines are ignored, ids must be unique.

    An answerable item needs expected documents and key facts: without key facts, both
    recall metrics would score it 1.0 whatever the answer.
    """
    if not path.exists():
        raise DatasetError(f"evaluation set not found: {path}")
    items: list[GoldItem] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = GoldItem.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(f"{path}:{lineno}: invalid item: {exc}") from exc
        if item.id in seen:
            raise DatasetError(f"{path}:{lineno}: duplicate id {item.id}")
        if not item.should_refuse and not item.expected_doc_ids:
            raise DatasetError(f"{path}:{lineno}: answerable item without expected_doc_ids")
        if not item.should_refuse and not item.must_include:
            raise DatasetError(f"{path}:{lineno}: answerable item without must_include")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise DatasetError(f"evaluation set is empty: {path}")
    return items
