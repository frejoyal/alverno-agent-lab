"""Law 25 layer shared by the modules: deterministic PI detection, redaction,
classification, refusal policy, access log, corpus checks, canaries and injection
screening (spec §3, ADR 0006).

This package depends on nothing above it: it never imports ``agents``, ``cli``,
``evals`` or the pipelines (a repository test checks it).
"""

from __future__ import annotations

from alverno_lab.privacy.access_log import AccessLog, AccessLogError, AccessLogRecord
from alverno_lab.privacy.canary import Canary, canary_in, scan_paths
from alverno_lab.privacy.classify import assess, screen
from alverno_lab.privacy.detect import PLACEHOLDERS, detect_pi, luhn_valid, redact
from alverno_lab.privacy.documents import check_corpus_privacy
from alverno_lab.privacy.guard import check_output
from alverno_lab.privacy.injection import CREDENTIAL_PATTERN, INJECTION_PATTERNS, is_injection
from alverno_lab.privacy.lexicon import build_vocabulary, normalize
from alverno_lab.privacy.policy import PI_LABELS, RefusalPolicy, hr_refusal_policy, redaction_note
from alverno_lab.privacy.roles import OrgRoles, Role, RoleMention
from alverno_lab.privacy.types import (
    PRIVACY_CODES,
    REFUSAL_CODES,
    SCREEN_CODES,
    Assessment,
    Basis,
    GuardVerdict,
    Kind,
    PISpan,
    PIType,
    PrivacyConfigError,
    PrivacyContext,
    Redaction,
    RefusalCode,
    ScreenCode,
    Screening,
    Subject,
)

__all__ = [
    "CREDENTIAL_PATTERN",
    "INJECTION_PATTERNS",
    "PI_LABELS",
    "PLACEHOLDERS",
    "PRIVACY_CODES",
    "REFUSAL_CODES",
    "SCREEN_CODES",
    "AccessLog",
    "AccessLogError",
    "AccessLogRecord",
    "Assessment",
    "Basis",
    "Canary",
    "GuardVerdict",
    "Kind",
    "OrgRoles",
    "PISpan",
    "PIType",
    "PrivacyConfigError",
    "PrivacyContext",
    "Redaction",
    "RefusalCode",
    "RefusalPolicy",
    "Role",
    "RoleMention",
    "ScreenCode",
    "Screening",
    "Subject",
    "assess",
    "build_vocabulary",
    "canary_in",
    "check_corpus_privacy",
    "check_output",
    "detect_pi",
    "hr_refusal_policy",
    "is_injection",
    "luhn_valid",
    "normalize",
    "redact",
    "redaction_note",
    "scan_paths",
    "screen",
]
