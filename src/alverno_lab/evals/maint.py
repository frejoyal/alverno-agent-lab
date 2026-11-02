"""Maintenance gold set (module 3b, ADR 0015): item model, loader and preconditions.

:func:`load_maint_gold` reads ``data/eval/maint_gold.jsonl`` (or the holdout) against a
:class:`~alverno_lab.connectors.sim.SimGmao` and checks the schema and existence rules
of :mod:`alverno_lab.evals.ops_gold`: asset ids exist (the asset id of a
``record_not_found`` item must not), expected calls are GMAO calls that pass the
registry args models, cited work orders exist, an edit changes only fields of the work
request (its values are not checked: one edit is invalid on purpose), canaries and
forbidden values are verbatim fixture values, and injection markers are part of a
fixture text. Counts are not checked here: the holdout has its own.
:func:`maint_preconditions` computes the ADR 0015 preconditions for the runner.

The request, the facts, the forbidden values, the canaries and the markers stay out of
``repr``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from alverno_lab.connectors.sim import SimGmao, fixture_strings
from alverno_lab.connectors.types import AssetId, FailureCode, SymptomCode
from alverno_lab.evals.ops_gold import (
    CheckedCall,
    GoldOutcome,
    GoldText,
    ItemId,
    ToolCall,
    call_rules,
    id_rules,
    load_items,
    marker_rules,
    outcome_rules,
    proposal_rules,
    value_rules,
)
from alverno_lab.privacy.ops_types import OpsRefusalCode
from alverno_lab.tools.registry import (
    CreateWorkRequestArgs,
    GetAssetArgs,
    ListWorkOrdersArgs,
)

__all__ = [
    "EDITABLE_FIELDS",
    "MAINT_ANSWERABLE_FAMILIES",
    "MAINT_FAMILIES",
    "MaintDecision",
    "MaintFamily",
    "MaintGoldItem",
    "MaintRoute",
    "load_maint_gold",
    "maint_preconditions",
]

MaintFamily = Literal[
    "diagnosis",
    "work_request",
    "technician_identification",
    "small_role_performance",
    "fault_attribution",
    "profiling",
    "injury",
    "service_duration_aggregate",
    "discipline",
    "unknown_asset",
]
MAINT_FAMILIES: frozenset[str] = frozenset(get_args(MaintFamily))
MAINT_ANSWERABLE_FAMILIES: frozenset[str] = frozenset({"diagnosis", "work_request"})
MaintRoute = Literal["diagnosis", "work_request", "asset_not_identified"]
# An edit of a proposed work request may change these arguments only.
EDITABLE_FIELDS: frozenset[str] = frozenset(CreateWorkRequestArgs.model_fields)


class MaintDecision(BaseModel):
    """One step of an approval script; an edit carries structured arguments, no text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: str = Field(min_length=1)
    edited_args: dict[str, JsonValue] | None = None


class MaintGoldItem(BaseModel):
    """One 3b gold item, keys in the order of the gold schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ItemId
    family: MaintFamily
    request: str = Field(min_length=1, repr=False)
    route: MaintRoute | None
    asset_id: AssetId | None
    symptom_code: SymptomCode | None
    expected_outcome: GoldOutcome
    expected_reason: OpsRefusalCode | None
    expected_code: FailureCode | None
    frequency_trap: bool = Field(strict=True)
    expected_actions: tuple[ToolCall, ...]
    expected_proposal: ToolCall | None
    approval_script: tuple[MaintDecision, ...]
    must_include: tuple[GoldText, ...] = Field(repr=False)
    must_not_include: tuple[GoldText, ...] = Field(repr=False)
    canaries: tuple[GoldText, ...] = Field(repr=False)
    injection_markers: tuple[GoldText, ...] = Field(repr=False)
    pair_id: ItemId | None
    notes: str

    @property
    def is_answerable(self) -> bool:
        """``diagnosis`` and ``work_request`` items; every other family is a trap."""
        return self.family in MAINT_ANSWERABLE_FAMILIES


@dataclasses.dataclass(frozen=True)
class _GmaoFacts:
    """What the rules compare with, read once per load."""

    asset_ids: frozenset[str]
    work_order_ids: frozenset[str]
    texts: frozenset[str]  # every string leaf of the fixture

    @classmethod
    def of(cls, gmao: SimGmao) -> _GmaoFacts:
        return cls(gmao.asset_ids, gmao.work_order_ids, fixture_strings(gmao))


def _call_ids(facts: _GmaoFacts, absent: bool, checked: CheckedCall) -> Iterator[str]:
    args = checked.args
    if isinstance(args, GetAssetArgs | ListWorkOrdersArgs | CreateWorkRequestArgs):
        yield from id_rules("asset_id", args.asset_id, facts.asset_ids, absent=absent)
    if (
        isinstance(args, CreateWorkRequestArgs)
        and "cited_wo_ids" not in checked.placeholders
        and any(wo_id not in facts.work_order_ids for wo_id in args.cited_wo_ids)
    ):
        yield "cited_wo_ids.unknown"


def _broken_rules(facts: _GmaoFacts, item: MaintGoldItem) -> Iterator[str]:
    absent = item.expected_reason == "record_not_found"
    id_check = partial(_call_ids, facts, absent)
    yield from outcome_rules(item.expected_outcome, item.expected_reason)
    yield from id_rules("asset_id", item.asset_id, facts.asset_ids, absent=absent)
    for index, call in enumerate(item.expected_actions):
        yield from call_rules(f"expected_actions[{index}]", call, "gmao", id_check)
    if item.expected_proposal is not None:
        yield from call_rules("expected_proposal", item.expected_proposal, "gmao", id_check)
    yield from proposal_rules(item.expected_proposal, len(item.approval_script))
    for index, step in enumerate(item.approval_script):
        if step.decision == "edit" and not step.edited_args:
            yield f"approval_script[{index}].edit_without_args"
        if step.decision != "edit" and step.edited_args is not None:
            yield f"approval_script[{index}].args_without_edit"
        if step.edited_args and not set(step.edited_args) <= EDITABLE_FIELDS:
            yield f"approval_script[{index}].edited_args.unknown_field"
    yield from value_rules("must_not_include", item.must_not_include, facts.texts)
    yield from value_rules("canaries", item.canaries, facts.texts)
    yield from marker_rules(item.injection_markers, facts.texts)


def load_maint_gold(path: Path, gmao: SimGmao) -> list[MaintGoldItem]:
    """Read a 3b JSONL set; raise :class:`~alverno_lab.evals.dataset.DatasetError`
    (``<file>:<line>:<rule>``, no value) at the first broken rule."""
    facts = _GmaoFacts.of(gmao)
    return load_items(path, MaintGoldItem.model_validate, partial(_broken_rules, facts))


def maint_preconditions(items: Sequence[MaintGoldItem]) -> dict[str, int]:
    """The ADR 0015 « Préconditions exactes » of a 3b set, as the runner reports them."""
    scripts = [item.approval_script for item in items]
    return {
        "n_items": len(items),
        "n_traps": sum(not item.is_answerable for item in items),
        "n_pairs": len({item.pair_id for item in items if item.pair_id is not None}),
        "n_approval_items": sum(bool(script) for script in scripts),
        "n_edit": sum(any(step.decision == "edit" for step in script) for script in scripts),
        "n_freq_traps": sum(item.frequency_trap for item in items),
        "n_injection_items": sum(bool(item.injection_markers) for item in items),
        "n_canary_items": sum(bool(item.canaries) for item in items),
    }
