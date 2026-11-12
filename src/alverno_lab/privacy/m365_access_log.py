"""Access-log record of the Quality API for Microsoft 365 (ADR 0026, after ADR 0008).

One record per call: who (a salted hash of the token's ``oid``, never the identifier
itself), the decision, the refusal or rejection code, the number of citations and the
latency. Never the question nor the answer. Every field is a closed type or a pattern;
``extra="forbid"`` rejects any other key and ``hide_input_in_errors`` keeps a rejected
value out of pydantic errors.

Cross-field rules (``m365_log.<rule>``): ``reason`` is required unless the call was
answered; ``user`` is required unless the token was rejected (``unauthorized``).

Like the rest of the privacy layer, this module imports nothing above ``privacy/``.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

__all__ = ["M365AccessLogRecord", "M365Decision"]

M365Decision = Literal["answered", "refused", "unauthorized", "forbidden", "invalid", "error"]
HEX32_PATTERN = r"^[0-9a-f]{32}$"
HOUR_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:00Z$"
CODE_PATTERN = r"^[a-z][a-z_]{0,39}$"


class M365AccessLogRecord(BaseModel):
    """One API call, as metadata. The time is rounded to the hour."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    v: Literal[1] = 1
    event_id: str = Field(pattern=HEX32_PATTERN)
    ts: str = Field(pattern=HOUR_PATTERN)
    module: Literal["m365"] = "m365"
    channel: Literal["api", "eval"]
    user: str | None = Field(default=None, pattern=HEX32_PATTERN)
    decision: M365Decision
    reason: str | None = Field(default=None, pattern=CODE_PATTERN)
    n_citations: int = Field(default=0, ge=0, le=50)
    latency_ms: int = Field(default=0, ge=0, le=3_600_000)

    @model_validator(mode="after")
    def _rules(self) -> Self:
        if (self.decision == "answered") != (self.reason is None):
            raise PydanticCustomError(
                "m365_log.reason", "a reason is required unless the call was answered"
            )
        if (self.decision == "unauthorized") != (self.user is None):
            raise PydanticCustomError(
                "m365_log.user", "the user is required unless the token was rejected"
            )
        return self

    @property
    def day(self) -> dt.date:
        """UTC day of the record, which names its file."""
        return dt.date.fromisoformat(self.ts[:10])
