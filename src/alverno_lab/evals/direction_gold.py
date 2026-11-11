"""Loader of the management sets ``dir_gold`` and ``dir_holdout`` (ADR 0020, ADR 0018 I2).

Each line is validated by a frozen model with ``extra="forbid"``; then the rules below are
checked against the independent oracle (:mod:`alverno_lab.evals.direction_oracle`). An
error is a :class:`~alverno_lab.evals.dataset.DatasetError` ``<file>:<line>:<rule>``,
never a value: the same loader reads the holdout, whose questions stay unseen.

Rules: outcome, reason and purposes follow the family; the scope names known ids and a
valid period, only on the purposes that use it; the expected counts are a subset of the
oracle's, with at least the totals of each routed overview; canaries and forbidden values
are verbatim fixture values; a fact is never a forbidden value; each pair joins one
``person_join`` trap and one answerable twin.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from alverno_lab.evals.dataset import DatasetError
from alverno_lab.evals.direction_oracle import DirectionOracle
from alverno_lab.evals.ops_gold import GoldText, ItemId, load_items
from alverno_lab.privacy.direction_access_log import DirectionPurpose, DirectionRefusalCode

__all__ = [
    "ANSWERABLE_PURPOSES",
    "DirGoldItem",
    "DirPeriod",
    "DirScope",
    "load_dir_set",
]

AnswerableFamily = Literal[
    "quality_policy", "hr_policy", "orders_overview", "maintenance_overview", "consolidated"
]
DirFamily = AnswerableFamily | DirectionRefusalCode
ANSWERABLE_PURPOSES: dict[str, tuple[DirectionPurpose, ...]] = {
    "quality_policy": ("quality_policy",),
    "hr_policy": ("hr_policy",),
    "orders_overview": ("orders_overview",),
    "maintenance_overview": ("maintenance_overview",),
    "consolidated": ("orders_overview", "maintenance_overview"),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class DirPeriod(_Frozen):
    start: dt.date
    end: dt.date


class DirScope(_Frozen):
    account_id: Annotated[str, StringConstraints(pattern=r"^CLI-\d{3}$")] | None
    asset_id: Annotated[str, StringConstraints(pattern=r"^EQ-[A-Z]{3}-\d{2}$")] | None
    period: DirPeriod | None


class DirGoldItem(_Frozen):
    """One management item (ADR 0018 « Précisions de l'étape I2 »)."""

    id: ItemId
    family: DirFamily
    question: Annotated[str, StringConstraints(min_length=1)]
    expected_outcome: Literal["answered", "refused"]
    expected_reason: DirectionRefusalCode | None
    expected_purposes: tuple[DirectionPurpose, ...]
    scope: DirScope
    pair_id: ItemId | None
    expected_counts: dict[str, int]
    must_include: tuple[GoldText, ...]
    must_not_include: tuple[GoldText, ...]
    canaries: tuple[GoldText, ...]

    @property
    def is_answerable(self) -> bool:
        return self.expected_outcome == "answered"

    def period(self) -> tuple[dt.date, dt.date] | None:
        p = self.scope.period
        return None if p is None else (p.start, p.end)


def _rules(item: DirGoldItem, oracle: DirectionOracle) -> Iterator[str]:
    family = item.family
    if family in ANSWERABLE_PURPOSES:
        if item.expected_outcome != "answered" or item.expected_reason is not None:
            yield "outcome.answerable"
        if item.expected_purposes != ANSWERABLE_PURPOSES[family]:
            yield "purposes.family"
    else:
        if item.expected_outcome != "refused" or item.expected_reason != family:
            yield "outcome.trap"
        if item.expected_purposes or item.expected_counts:
            yield "purposes.trap"
    purposes = set(item.expected_purposes)
    scope = item.scope
    if scope.account_id is not None and (
        scope.account_id not in oracle.account_ids or "orders_overview" not in purposes
    ):
        yield "scope.account"
    if scope.asset_id is not None and (
        scope.asset_id not in oracle.asset_ids or "maintenance_overview" not in purposes
    ):
        yield "scope.asset"
    if scope.period is not None and (
        scope.period.start > scope.period.end or "maintenance_overview" not in purposes
    ):
        yield "scope.period"
    expected: dict[str, int] = {}
    if "orders_overview" in purposes:
        expected |= oracle.orders_counts(scope.account_id)
        if not {"orders.total", "orders.late"} <= set(item.expected_counts):
            yield "expected_counts.orders_totals"
    if "maintenance_overview" in purposes:
        expected |= oracle.maintenance_counts(scope.asset_id, item.period())
        if "maintenance.corrective" not in item.expected_counts:
            yield "expected_counts.maintenance_total"
    if any(expected.get(k, 0) != v for k, v in item.expected_counts.items()):
        yield "expected_counts.not_oracle"
    forbidden = oracle.forbidden_values
    if any(v not in forbidden for v in (*item.canaries, *item.must_not_include)):
        yield "values.not_fixture_value"
    if any(v in forbidden for v in item.must_include):
        yield "must_include.forbidden_value"


def load_dir_set(path: Path, oracle: DirectionOracle) -> list[DirGoldItem]:
    """Read a management set; raise ``DatasetError`` at the first broken rule.

    ``load_items`` checks that each pair joins one trap and one answerable twin; this
    loader adds that the trap is a ``person_join`` (ADR 0020).
    """
    items = load_items(path, DirGoldItem.model_validate, lambda i: _rules(i, oracle))
    pairs: dict[str, list[DirGoldItem]] = {}
    for item in items:
        if item.pair_id is not None:
            pairs.setdefault(item.pair_id, []).append(item)
    for members in pairs.values():
        if not any(m.family == "person_join" for m in members):
            raise DatasetError(f"{path}:0:pair_id.not_person_join") from None
    return items
