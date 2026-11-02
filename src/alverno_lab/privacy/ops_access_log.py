"""Access-log record of module 3 (ADR 0014): one frozen record per event of a request.

:class:`OpsAccessLogRecord` holds exactly the fields of the ADR 0014 table, in its order.
Every field is a closed type or a pattern, so no field can carry free text: the request,
a draft, an edit, a contact or a technician value, a digest of a text (other than
``action_args_sha256``) or a length never fit. ``extra="forbid"`` rejects any other key
and ``hide_input_in_errors`` keeps rejected values out of pydantic errors.

The cross-field rules of ADR 0014 « Événements » and « Précisions » are checked by one
model validator; each broken rule is a pydantic error whose type names the rule
(``ops_log.<rule>``), never a value:

- ``module_flow``: ``service`` goes with ``order_status``/``customer_reply``, ``maint``
  with ``diagnosis``/``work_request``; ``flow`` may be empty only on ``refused``;
- ``event_flow``: ``answered`` on a flow without approval, the approval events
  (``drafted`` to ``abandoned``) on a flow with approval;
- ``account_ref``: always empty in ``maint``;
- ``approver``: ``"operator"`` if and only if the event is a decision (``approved``,
  ``edit_submitted``, ``rejected``);
- ``draft_id``: required from ``drafted`` on, empty on ``answered``;
- ``reason``: required on ``refused`` (a refusal before any composed text), ``withheld``
  (``ungrounded``, ``output_pi``, ``cross_account``, ``invalid_args``) and ``rejected``
  (``approval_rejected``, ``invalid_decision``); empty on ``answered`` and the approval
  events; ``error`` carries nothing or ``connector_unavailable``; ``audit_unavailable``
  is a served message, never a record reason;
- ``action``: ``drafted``, ``re_presented`` and ``approved`` carry ``action_tool`` and
  ``action_args_sha256``; ``edit_submitted`` has no digest (``reguard`` recomputes it);
- ``edit_round``: 0 on ``drafted``;
- ``model``: empty on a decision record.

Like the rest of the privacy layer, this module imports nothing above ``privacy/``: tool
names and ids are patterns here, never the ``connectors`` types.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from alverno_lab.privacy.access_log import MODEL_PATTERN, PI_COUNT_CAP
from alverno_lab.privacy.ops_types import OPS_REFUSAL_CODES, Flow, OpsRefusalCode
from alverno_lab.privacy.types import PIType

OpsEvent = Literal[
    "answered",
    "refused",
    "withheld",
    "drafted",
    "approved",
    "edit_submitted",
    "re_presented",
    "rejected",
    "abandoned",
    "error",
]
OPS_EVENTS: frozenset[str] = frozenset(get_args(OpsEvent))

# Field patterns ([0-9], never \d: pydantic's \d would accept non-ASCII digits).
HEX32_PATTERN = r"^[0-9a-f]{32}$"
HEX64_PATTERN = r"^[0-9a-f]{64}$"
HOUR_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:00Z$"
TOOL_PATTERN = r"^[a-z]+\.[a-z_]+$"
ENTITY_PATTERN = r"^CMD-[0-9]{4}-[0-9]{4}$|^EQ-[A-Z]{3}-[0-9]{2}$|^OT-[0-9]{4}-[0-9]{4}$"
ACCOUNT_PATTERN = r"^CLI-[0-9]{3}$"
FLAG_PATTERN = r"^[a-z_]+$"
MAX_ENTITY_REFS = 20

ToolRef = Annotated[str, Field(pattern=TOOL_PATTERN)]
EntityRef = Annotated[str, Field(pattern=ENTITY_PATTERN)]
QuarantineFlag = Annotated[str, Field(pattern=FLAG_PATTERN)]

MODULE_FLOWS: dict[str, frozenset[str]] = {
    "service": frozenset({"order_status", "customer_reply"}),
    "maint": frozenset({"diagnosis", "work_request"}),
}
APPROVAL_FLOWS: frozenset[str] = frozenset({"customer_reply", "work_request"})
DECISION_EVENTS: frozenset[str] = frozenset({"approved", "edit_submitted", "rejected"})
APPROVAL_EVENTS: frozenset[str] = frozenset(
    {"drafted", "approved", "edit_submitted", "re_presented", "rejected", "abandoned"}
)
WITHHELD_REASONS: frozenset[str] = frozenset(
    {"ungrounded", "output_pi", "cross_account", "invalid_args"}
)
REJECTED_REASONS: frozenset[str] = frozenset({"approval_rejected", "invalid_decision"})
# A refusal comes before any composed text: from the screen, the routing, the record or
# the scope. Codes of a later stage, and the two system codes, are never a refusal.
REFUSED_REASONS: frozenset[str] = (
    OPS_REFUSAL_CODES
    - (WITHHELD_REASONS - {"cross_account"})
    - REJECTED_REASONS
    - {"connector_unavailable", "audit_unavailable"}
)
ERROR_REASONS: frozenset[str] = frozenset({"connector_unavailable"})
# Events that carry the action presented or approved (tool and digest).
ACTION_EVENTS: frozenset[str] = frozenset({"drafted", "re_presented", "approved"})


class OpsAccessLogRecord(BaseModel):
    """One event of a module 3 request, as metadata. The time is rounded to the hour."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    v: Literal[1] = 1
    module: Literal["service", "maint"]
    event_id: str = Field(pattern=HEX32_PATTERN)
    draft_id: str | None = Field(default=None, pattern=HEX32_PATTERN)
    ts: str = Field(pattern=HOUR_PATTERN)
    channel: Literal["cli", "eval"]
    flow: Flow | None = None
    event: OpsEvent
    reason: OpsRefusalCode | None = None
    tool_calls: tuple[ToolRef, ...] = ()
    entity_refs: tuple[EntityRef, ...] = Field(default=(), max_length=MAX_ENTITY_REFS)
    account_ref: str | None = Field(default=None, pattern=ACCOUNT_PATTERN)
    pi_types: tuple[PIType, ...] = ()
    pi_count: int = Field(default=0, ge=0, le=PI_COUNT_CAP)
    quarantine_flags: tuple[QuarantineFlag, ...] = ()
    action_tool: str | None = Field(default=None, pattern=TOOL_PATTERN)
    action_args_sha256: str | None = Field(default=None, pattern=HEX64_PATTERN)
    executed_external_send: Literal[False] = False
    edit_round: int = Field(default=0, ge=0, le=2)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    latency_ms: int = Field(ge=0)
    approver: Literal["operator"] | None = None
    actor: None = None

    @property
    def day(self) -> dt.date:
        """UTC day of the record, which names its file."""
        return dt.date.fromisoformat(self.ts[:10])

    @model_validator(mode="after")
    def _check_event_rules(self) -> OpsAccessLogRecord:
        rule = next(broken_rules(self), None)
        if rule is not None:
            # The error type names the rule; the message holds no value either.
            raise PydanticCustomError(f"ops_log.{rule}", "broken access log rule")
        return self


def _reason_rules(rec: OpsAccessLogRecord) -> Iterator[str]:
    """The broken ``reason`` rules of ``rec``."""
    reason, event = rec.reason, rec.event
    if reason == "audit_unavailable":
        yield "reason.audit_unavailable"
    elif event == "refused" and reason not in REFUSED_REASONS:
        yield "reason.refused"
    elif event == "withheld" and reason not in WITHHELD_REASONS:
        yield "reason.withheld"
    elif event == "rejected" and reason not in REJECTED_REASONS:
        yield "reason.rejected"
    elif event == "error" and reason is not None and reason not in ERROR_REASONS:
        yield "reason.error"
    elif (event == "answered" or event in APPROVAL_EVENTS - {"rejected"}) and reason is not None:
        yield "reason.not_empty"


def broken_rules(rec: OpsAccessLogRecord) -> Iterator[str]:
    """The cross-field rules of ADR 0014 that ``rec`` breaks, in a fixed order."""
    event = rec.event
    if rec.flow is None:
        if event != "refused":
            yield "module_flow"
    elif rec.flow not in MODULE_FLOWS[rec.module]:
        yield "module_flow"
    elif (event == "answered") if rec.flow in APPROVAL_FLOWS else (event in APPROVAL_EVENTS):
        yield "event_flow"
    if rec.module == "maint" and rec.account_ref is not None:
        yield "account_ref"
    if (rec.approver == "operator") != (event in DECISION_EVENTS):
        yield "approver"
    if event in APPROVAL_EVENTS and rec.draft_id is None:
        yield "draft_id.missing"
    if event == "answered" and rec.draft_id is not None:
        yield "draft_id.not_empty"
    yield from _reason_rules(rec)
    if event in ACTION_EVENTS and (rec.action_tool is None or rec.action_args_sha256 is None):
        yield "action.missing"
    if event == "edit_submitted" and rec.action_args_sha256 is not None:
        yield "action.digest_on_edit"
    if event == "drafted" and rec.edit_round != 0:
        yield "edit_round"
    if event in DECISION_EVENTS and rec.model is not None:
        yield "model"
