"""Types of the Law 25 layer: PI spans, assessments, refusal codes and the privacy context.

No type here ever holds a detected value in a field that is printed: spans carry a type,
offsets, a basis and a rule id, and the raw values kept for the output guard are excluded
from ``repr``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from alverno_lab.privacy.roles import OrgRoles


class PrivacyConfigError(ValueError):
    """A privacy configuration file (roles, contacts, lexicon) is missing or malformed."""


class PIType(StrEnum):
    """Personal information types the detector covers (spec §5.2)."""

    NAS = "nas"
    NAM = "nam"
    COURRIEL = "courriel"
    TELEPHONE = "telephone"
    MATRICULE = "matricule"
    CODE_POSTAL = "code_postal"
    ADRESSE = "adresse"
    DATE_NAISSANCE = "date_naissance"
    COMPTE_BANCAIRE = "compte_bancaire"
    NOM = "nom"
    NUMERO = "numero"


# Types compared as digits only (canaries, output echo check).
NUMERIC_TYPES: frozenset[PIType] = frozenset(
    {PIType.NAS, PIType.NAM, PIType.TELEPHONE, PIType.COMPTE_BANCAIRE, PIType.NUMERO}
)

# Why a span was emitted. Overlaps are resolved in this order (strongest first).
Basis = Literal["validated", "pattern", "context", "heuristic"]
BASIS_RANK: dict[str, int] = {"validated": 0, "pattern": 1, "context": 2, "heuristic": 3}


@dataclass(frozen=True)
class PISpan:
    """A detected span of personal information: never the value, only where and why."""

    type: PIType
    start: int
    end: int
    basis: Basis
    rule: str

    @property
    def length(self) -> int:
        """Number of characters covered."""
        return self.end - self.start

    def overlaps(self, other: PISpan) -> bool:
        """True when the two spans share at least one character."""
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class Redaction:
    """A text whose spans were replaced by typed placeholders (spec §5.3)."""

    text: str
    spans: tuple[PISpan, ...]

    @property
    def types(self) -> list[PIType]:
        """Sorted unique PI types that were removed."""
        return sorted({s.type for s in self.spans}, key=lambda t: t.value)


Subject = Literal["none", "self", "other", "small_group", "group"]
Kind = Literal[
    "policy", "record", "aggregate", "restricted", "legal", "injection", "empty", "foreign"
]
ScreenCode = Literal[
    "empty_question",
    "prompt_injection",
    "unsupported_language",
    "person_targeted",
    "own_record",
    "restricted_info",
    "legal_advice",
]
RefusalCode = Literal[
    "empty_question",
    "prompt_injection",
    "unsupported_language",
    "person_targeted",
    "own_record",
    "restricted_info",
    "legal_advice",
    "no_match",
    "no_valid_citation",
    "low_confidence",
    "llm_error",
    "output_pi",
    "audit_unavailable",
]
SCREEN_CODES: frozenset[str] = frozenset(get_args(ScreenCode))
REFUSAL_CODES: frozenset[str] = frozenset(get_args(RefusalCode))
PRIVACY_CODES: frozenset[str] = frozenset({"person_targeted", "own_record"})


class Assessment(BaseModel):
    """What a question asks for: kind, subject, requested level and the matched rules."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    subject: Subject
    requested_level: int = Field(ge=1, le=5)
    refusal: ScreenCode | None
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class Screening:
    """Outcome of the pure screen: redacted text, spans, assessment.

    ``raw_values`` lives in memory only, for the output guard's echo check; it is kept out
    of ``repr`` and of equality, so that a log line, a traceback or a failing assertion
    (pytest compares fields one by one) never prints it.
    """

    redacted: str
    spans: tuple[PISpan, ...]
    assessment: Assessment
    raw_values: tuple[str, ...] = field(default=(), repr=False, compare=False)


GuardRule = Literal["detected", "placeholder", "echo", "proper_noun"]


@dataclass(frozen=True)
class GuardVerdict:
    """Output guard decision; ``rule`` names the first rule that fired."""

    ok: bool
    rule: GuardRule | None = None


@dataclass(frozen=True)
class PrivacyContext:
    """Everything the deterministic rules need, loaded once per module.

    ``allowlist`` and ``vocabulary`` hold normalised forms (``lexicon.normalize``);
    ``role_mailboxes`` holds casefolded addresses that are not personal information.
    """

    allowlist: frozenset[str]
    role_mailboxes: frozenset[str]
    roles: OrgRoles
    vocabulary: frozenset[str]
    k: int = 5
