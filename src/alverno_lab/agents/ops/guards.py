"""Output checks shared by the 3a and 3b graphs (ADR 0012 « guard », ADR 0013 « reguard »).

- :func:`quarantine_stop`: the quarantine rules applied to a served text, as ``reguard``
  applies them to an edit (an injection, an action request or a credential).
- :func:`person_spans`: whether the detector finds a name (``NOM``) or a matricule
  (``MATRICULE``) anywhere in a text, cited extracts or not (the strict 3b guard).
- :func:`small_role_sentence`: whether one sentence or line of a text ties a small group
  of roles to a duration, a fault or a result (``attributes_small_role``).
- :func:`technician_ref`: whether the quarantine's identifier rules find initials or a
  login-shaped token in a text (flag ``technician_ref``: « MT », « J.T. », « jtremblay2 »,
  a word after « compte » or « login »…), outside the fixture ids and codes. The strict
  3b guard stops on it; the published misses of the quarantine stay misses here.

Each returns a boolean, never a value.
"""

from __future__ import annotations

import re

from alverno_lab.privacy.detect import detect_pi
from alverno_lab.privacy.quarantine import attributes_small_role, scrub_tool_text
from alverno_lab.privacy.types import PIType, PrivacyContext

__all__ = [
    "STOP_FLAGS",
    "person_spans",
    "quarantine_stop",
    "small_role_sentence",
    "technician_ref",
]

# Quarantine flags that withhold a served text or an edit: an order, an action request
# or a secret.
STOP_FLAGS: frozenset[str] = frozenset({"injection", "action_request", "credential"})
_PERSON_TYPES: frozenset[PIType] = frozenset({PIType.NOM, PIType.MATRICULE})
# A sentence ends at . ? ! ; followed by a space, or at a line break.
_SENTENCE_RE = re.compile(r"(?<=[.?!;])\s+|\n+")


def quarantine_stop(text: str, ctx: PrivacyContext) -> bool:
    """Whether the quarantine rules flag ``text``: an injection, an action request or a
    credential (the same rules for a model text in ``guard`` and an edit in ``reguard``)."""
    return bool(set(scrub_tool_text(text, ctx).flags) & STOP_FLAGS)


def person_spans(text: str, ctx: PrivacyContext) -> bool:
    """Whether the detector (document mode) finds a ``NOM`` or ``MATRICULE`` span in ``text``."""
    return any(span.type in _PERSON_TYPES for span in detect_pi(text, ctx, mode="document"))


def small_role_sentence(text: str, ctx: PrivacyContext) -> bool:
    """Whether one sentence of ``text`` ties a small group to a duration, fault or result."""
    return any(
        attributes_small_role(sentence, ctx)
        for sentence in _SENTENCE_RE.split(text)
        if sentence.strip()
    )


def technician_ref(text: str, ctx: PrivacyContext) -> bool:
    """Whether the quarantine flags initials or a login in ``text`` (``technician_ref``)."""
    return "technician_ref" in scrub_tool_text(text, ctx).flags
