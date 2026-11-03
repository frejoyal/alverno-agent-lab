"""Customer-service gold set (module 3a, ADR 0015): item model, loader and preconditions.

:func:`load_svc_gold` reads ``data/eval/svc_gold.jsonl`` (or the holdout) against a
:class:`~alverno_lab.connectors.sim.SimErp` and checks the schema and existence rules
of :mod:`alverno_lab.evals.ops_gold`: order and account ids exist (the order id of a
``record_not_found`` item must not), expected calls are ERP calls that pass the
registry args models, canaries and contact canaries are verbatim fixture values,
forbidden values are fixture values or the ADR 0015 renderings of a fixture status or
date, and injection markers are part of a fixture text. Counts are not checked here:
the holdout has its own. :func:`svc_preconditions` computes the ADR 0015 preconditions
for the runner.

The request, the edited texts, the facts, the forbidden values, the canaries and the
markers stay out of ``repr``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Sequence
from functools import partial
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from alverno_lab.connectors.sim import SimErp, fixture_strings
from alverno_lab.connectors.types import AccountId, OrderId
from alverno_lab.evals.ops_gold import (
    STATUS_RENDERINGS,
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
    render_date,
    value_rules,
)
from alverno_lab.privacy.ops_types import OpsRefusalCode
from alverno_lab.tools.registry import GetOrderArgs, RecordReplyPreparedArgs

__all__ = [
    "SVC_ANSWERABLE_FAMILIES",
    "SVC_FAMILIES",
    "SvcDecision",
    "SvcFamily",
    "SvcFlag",
    "SvcGoldItem",
    "SvcRoute",
    "load_svc_gold",
    "svc_preconditions",
]

SvcFamily = Literal[
    "order_status",
    "customer_reply",
    "cross_account",
    "recipient_override",
    "auto_send",
    "restricted_info",
    "purpose_drift",
    "minimization_refused",
    "record_not_found",
    "unprefixed_id",
    "multi_order",
]
SVC_FAMILIES: frozenset[str] = frozenset(get_args(SvcFamily))
SVC_ANSWERABLE_FAMILIES: frozenset[str] = frozenset({"order_status", "customer_reply"})
SvcRoute = Literal["order_status", "customer_reply", "order_not_identified", "multi_order"]
SvcFlag = Literal["auto_send_requested"]


class SvcDecision(BaseModel):
    """One step of an approval script; ``decision`` may be invalid on purpose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: str = Field(min_length=1)
    edited_text: str | None = Field(default=None, repr=False)


class SvcGoldItem(BaseModel):
    """One 3a gold item, keys in the order of the gold schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ItemId
    family: SvcFamily
    request: str = Field(min_length=1, repr=False)
    route: SvcRoute | None
    order_id: OrderId | None
    account_id: AccountId | None
    expected_outcome: GoldOutcome
    expected_reason: OpsRefusalCode | None
    flags: tuple[SvcFlag, ...]
    expected_actions: tuple[ToolCall, ...]
    expected_proposal: ToolCall | None
    approval_script: tuple[SvcDecision, ...]
    must_include: tuple[GoldText, ...] = Field(repr=False)
    must_not_include: tuple[GoldText, ...] = Field(repr=False)
    canaries: tuple[GoldText, ...] = Field(repr=False)
    contact_canaries: tuple[GoldText, ...] = Field(repr=False)
    injection_markers: tuple[GoldText, ...] = Field(repr=False)
    pair_id: ItemId | None
    notes: str

    @property
    def is_answerable(self) -> bool:
        """``order_status`` and ``customer_reply`` items; every other family is a trap."""
        return self.family in SVC_ANSWERABLE_FAMILIES


@dataclasses.dataclass(frozen=True)
class _ErpFacts:
    """What the rules compare with, read once per load."""

    order_ids: frozenset[str]
    account_ids: frozenset[str]
    texts: frozenset[str]  # every string leaf of the fixture
    forbidden: frozenset[str]  # admissible must_not_include values

    @classmethod
    def of(cls, erp: SimErp) -> _ErpFacts:
        texts = fixture_strings(erp)
        # Dates are not strings in the fixture: they are read from the order views and
        # admitted in their ADR 0015 dual rendering, like the imposed status renderings.
        # Quantities are not admitted: a bare number would match almost any text.
        rendered: set[str] = set(STATUS_RENDERINGS.values())
        for order_id in erp.order_ids:
            view = erp.get_order(order_id)
            if view is None:  # pragma: no cover - every listed id has a view
                continue
            rendered |= {render_date(d) for d in (view.promised_date, view.revised_date) if d}
        return cls(erp.order_ids, erp.account_ids, texts, texts | frozenset(rendered))


def _order_ids(facts: _ErpFacts, absent: bool, checked: CheckedCall) -> Iterator[str]:
    if isinstance(checked.args, GetOrderArgs | RecordReplyPreparedArgs):
        yield from id_rules("order_id", checked.args.order_id, facts.order_ids, absent=absent)


def _broken_rules(facts: _ErpFacts, item: SvcGoldItem) -> Iterator[str]:
    absent = item.expected_reason == "record_not_found"
    id_check = partial(_order_ids, facts, absent)
    yield from outcome_rules(item.expected_outcome, item.expected_reason)
    yield from id_rules("order_id", item.order_id, facts.order_ids, absent=absent)
    yield from id_rules("account_id", item.account_id, facts.account_ids, absent=False)
    for index, call in enumerate(item.expected_actions):
        yield from call_rules(f"expected_actions[{index}]", call, "erp", id_check)
    if item.expected_proposal is not None:
        yield from call_rules("expected_proposal", item.expected_proposal, "erp", id_check)
    yield from proposal_rules(item.expected_proposal, len(item.approval_script))
    for index, step in enumerate(item.approval_script):
        if step.decision == "edit" and not (step.edited_text or "").strip():
            yield f"approval_script[{index}].edit_without_text"
        if step.decision != "edit" and step.edited_text is not None:
            yield f"approval_script[{index}].text_without_edit"
    yield from value_rules("must_not_include", item.must_not_include, facts.forbidden)
    yield from value_rules("canaries", item.canaries, facts.texts)
    yield from value_rules("contact_canaries", item.contact_canaries, facts.texts)
    yield from marker_rules(item.injection_markers, facts.texts)


def load_svc_gold(path: Path, erp: SimErp) -> list[SvcGoldItem]:
    """Read a 3a JSONL set; raise :class:`~alverno_lab.evals.dataset.DatasetError`
    (``<file>:<line>:<rule>``, no value) at the first broken rule."""
    facts = _ErpFacts.of(erp)
    return load_items(path, SvcGoldItem.model_validate, partial(_broken_rules, facts))


def _is_reject(script: Sequence[SvcDecision]) -> bool:
    return len(script) == 1 and script[0].decision == "reject" and script[0].edited_text is None


def svc_preconditions(items: Sequence[SvcGoldItem]) -> dict[str, int]:
    """The ADR 0015 « Préconditions exactes » of a 3a set, as the runner reports them."""
    scripts = [item.approval_script for item in items]
    return {
        "n_items": len(items),
        "n_traps": sum(not item.is_answerable for item in items),
        "n_pairs": len({item.pair_id for item in items if item.pair_id is not None}),
        "n_approval_items": sum(bool(script) for script in scripts),
        "n_reject": sum(_is_reject(script) for script in scripts),
        "n_edit": sum(any(step.decision == "edit" for step in script) for script in scripts),
        "n_injection_items": sum(bool(item.injection_markers) for item in items),
        "n_canary_items": sum(bool(item.canaries) for item in items),
    }
