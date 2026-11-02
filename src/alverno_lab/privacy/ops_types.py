"""Refusal codes and purposes of module 3 (customer service 3a, maintenance 3b).

``OpsRefusalCode`` lists every code of the « Politique de refus » table of the module 3
assessment (``docs/efvp/ops-module.md``), in table order; a test parses the table and
compares. These codes are distinct from the HR ``RefusalCode``: the HR package API
(``privacy/__init__.py``) does not re-export this module and stays byte-identical.

``Flow`` names the four purposes of ADR 0012 that justify a tool call. Like the rest of
the privacy layer, this module imports nothing from ``alverno_lab``.
"""

from __future__ import annotations

from typing import Literal, get_args

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
