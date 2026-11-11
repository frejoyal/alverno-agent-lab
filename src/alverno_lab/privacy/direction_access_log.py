"""Access-log record of the management orchestrator (ADR 0018, after ADR 0014).

One record per request: the purposes routed and the outcome of each section, never the
question, an order, an account, an equipment or a count. Every field is a closed type or a
pattern; ``extra="forbid"`` rejects any other key and ``hide_input_in_errors`` keeps a
rejected value out of pydantic errors.

Cross-field rules, each a pydantic error whose type names the rule (``direction_log.<rule>``):

- ``reason``: required on ``refused`` (the direction screen or routing), empty otherwise;
- ``sections``: one outcome per routed purpose, in the same order; none on ``refused``.

Like the rest of the privacy layer, this module imports nothing above ``privacy/``.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from alverno_lab.privacy.access_log import MODEL_PATTERN
from alverno_lab.privacy.types import Channel

__all__ = [
    "DIRECTION_PURPOSES",
    "DIRECTION_REFUSAL_CODES",
    "DirectionAccessLogRecord",
    "DirectionPurpose",
    "DirectionRefusalCode",
    "SectionOutcome",
]

DirectionPurpose = Literal["quality_policy", "hr_policy", "orders_overview", "maintenance_overview"]
DIRECTION_PURPOSES: tuple[DirectionPurpose, ...] = get_args(DirectionPurpose)
DirectionRefusalCode = Literal[
    "person_join", "profiling", "restricted_info", "sensitive_out_of_scope", "out_of_scope"
]
DIRECTION_REFUSAL_CODES: tuple[DirectionRefusalCode, ...] = get_args(DirectionRefusalCode)
SectionOutcome = Literal["served", "refused", "error"]
HEX32_PATTERN = r"^[0-9a-f]{32}$"
HOUR_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:00Z$"


class DirectionAccessLogRecord(BaseModel):
    """One management request, as metadata. The time is rounded to the hour."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    v: Literal[1] = 1
    event_id: str = Field(pattern=HEX32_PATTERN)
    ts: str = Field(pattern=HOUR_PATTERN)
    module: Literal["direction"] = "direction"
    channel: Channel
    decision: Literal["answered", "refused", "error"]
    reason: DirectionRefusalCode | None = None
    purposes: tuple[DirectionPurpose, ...] = Field(default=(), max_length=4)
    sections: tuple[SectionOutcome, ...] = Field(default=(), max_length=4)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    latency_ms: int = Field(ge=0)
    actor: None = None

    @property
    def day(self) -> dt.date:
        """UTC day of the record, which names its file."""
        return dt.date.fromisoformat(self.ts[:10])

    @model_validator(mode="after")
    def _rules(self) -> DirectionAccessLogRecord:
        if (self.decision == "refused") != (self.reason is not None):
            raise PydanticCustomError("direction_log.reason", "reason")
        if self.decision == "refused" and self.sections:
            raise PydanticCustomError("direction_log.sections", "sections")
        if self.decision == "answered" and len(self.sections) != len(self.purposes):
            raise PydanticCustomError("direction_log.sections", "sections")
        if len(set(self.purposes)) != len(self.purposes):
            raise PydanticCustomError("direction_log.purposes", "purposes")
        return self
