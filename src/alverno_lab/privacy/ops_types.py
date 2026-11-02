"""Refusal codes and purposes of module 3 (customer service 3a, maintenance 3b).

``OpsRefusalCode`` lists every code of the « Politique de refus » table of the module 3
assessment (``docs/efvp/ops-module.md``), in table order; a test parses the table and
compares. These codes are distinct from the HR ``RefusalCode``: the HR package API
(``privacy/__init__.py``) does not re-export this module and stays byte-identical.

``Flow`` names the four purposes of ADR 0012 that justify a tool call. ``OpsAssessment``
is the verdict of the request screens (``ops_screen.py``): only a screen-stage code
(``SCREEN_STAGE_CODES``, the « filtrage » rows of the table) can be its refusal, and its
only flag, ``auto_send_requested``, is never a refusal (the draft is still held for
approval). Like the rest of the privacy layer, this module imports nothing from
``alverno_lab``.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

OpsRefusalCode = Literal[
    "empty",
    "prompt_injection",
    "unsupported_language",
    "order_not_identified",
    "asset_not_identified",
    "multi_order",
    "record_not_found",
    "insufficient_history",
    "cross_account",
    "recipient_override",
    "restricted_info",
    "claim_out_of_scope",
    "purpose_drift",
    "minimization_refused",
    "person_targeted",
    "profiling",
    "sensitive_out_of_scope",
    "ungrounded",
    "output_pi",
    "invalid_args",
    "invalid_decision",
    "approval_rejected",
    "connector_unavailable",
    "audit_unavailable",
]
OPS_REFUSAL_CODES: frozenset[str] = frozenset(get_args(OpsRefusalCode))

Flow = Literal["order_status", "customer_reply", "diagnosis", "work_request"]

OpsFlag = Literal["auto_send_requested"]
OPS_FLAGS: frozenset[str] = frozenset(get_args(OpsFlag))

# Codes decided by the request screens, before any connector call (« filtrage » rows).
SCREEN_STAGE_CODES: frozenset[str] = frozenset(
    {
        "empty",
        "prompt_injection",
        "unsupported_language",
        "recipient_override",
        "restricted_info",
        "claim_out_of_scope",
        "purpose_drift",
        "minimization_refused",
        "person_targeted",
        "profiling",
        "sensitive_out_of_scope",
    }
)


class OpsAssessment(BaseModel):
    """Verdict of an ops request screen: a screen-stage refusal or None, the rule ids that
    decided it (no value, no offset) and the flags of an allowed request."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    refusal: OpsRefusalCode | None
    rule_ids: tuple[str, ...]
    flags: tuple[OpsFlag, ...]

    @model_validator(mode="after")
    def _screen_stage(self) -> OpsAssessment:
        if self.refusal is not None and self.refusal not in SCREEN_STAGE_CODES:
            raise ValueError("refusal:not_screen_stage")
        if self.refusal is not None and self.flags:
            raise ValueError("flags:refused_request")
        if len(set(self.flags)) != len(self.flags):
            raise ValueError("flags:duplicate")
        return self
