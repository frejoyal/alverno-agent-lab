"""Independent oracle of the 3a evaluation (ADR 0015 « Oracle indépendant »).

The ``verify`` step of the service graph decides whether a draft is grounded; this
module judges the **served** texts afterwards, with code of its own. It imports nothing
from ``agents/service`` or ``agents/ops`` (a structural test checks this) and keeps its
own copy of the ADR 0015 table « Rendu des statuts et des dates », so that an error in
the graph's table or matcher is not repeated here by construction. An error shared by
both remains possible; the negative control « vérification désactivée » bounds it.

- :func:`status_forms_in` finds the recognised status forms of a text: NFKC, case and
  accents ignored, whole words, the longest form first and no overlap, so that
  « expédiée en partie » is read before « expédiée ».
- :func:`status_faithful` says whether a composed text holds the imposed rendering of
  the order's ERP status and no form of another status (``status_fidelity``).
- :func:`forbidden_hits` returns the ``must_not_include`` values a text holds: a status
  rendering is found through any recognised form of that status, a dual-rendered date
  through either of its two forms, and any other value (another account's label or
  e-mail, say) as whole words.

Nothing here prints, logs or raises a value; :func:`forbidden_hits` returns the values
to its caller, which counts them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType

__all__ = [
    "ORACLE_RENDERINGS",
    "ORACLE_STATUS_FORMS",
    "date_forms",
    "fold",
    "forbidden_hits",
    "status_faithful",
    "status_forms_in",
    "value_in",
]

# The ADR 0015 table, copied by hand (not imported): status code -> imposed rendering.
ORACLE_RENDERINGS: Mapping[str, str] = MappingProxyType(
    {
        "recue": "reçue",
        "planifiee": "planifiée",
        "en_production": "en production",
        "en_finition": "en finition",
        "expediee_partielle": "expédiée en partie",
        "expediee": "expédiée",
        "en_attente_client": "en attente du client",
    }
)
# Status code -> recognised forms (ADR 0015, third column).
ORACLE_STATUS_FORMS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "recue": ("reçue", "reçues"),
        "planifiee": ("planifiée", "planifiées"),
        "en_production": ("en production",),
        "en_finition": ("en finition",),
        "expediee_partielle": (
            "expédiée en partie",
            "expédiées en partie",
            "partiellement expédiée",
            "partiellement expédiées",
        ),
        "expediee": ("expédiée", "expédiées"),
        "en_attente_client": ("en attente du client", "en attente de votre part"),
    }
)

_SPACES = re.compile(r"\s+")
# Typographic apostrophes (U+2019, U+02BC, U+2018) are read as the ASCII one.
_APOSTROPHES = str.maketrans(dict.fromkeys(map(chr, (0x2019, 0x02BC, 0x2018)), "'"))
# « 2026-11-15 (15 novembre 2026) »: the ISO form, then the French form in parentheses.
_DUAL_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}) \(([^()]+)\)")


def fold(text: str) -> str:
    """NFKC, apostrophes unified, accents dropped, casefolded, spaces collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)
    decomposed = unicodedata.normalize("NFD", text)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", bare.casefold()).strip()


def _forms_by_length() -> tuple[tuple[str, str], ...]:
    pairs = [(fold(form), code) for code, forms in ORACLE_STATUS_FORMS.items() for form in forms]
    return tuple(sorted(pairs, key=lambda pair: len(pair[0]), reverse=True))


_FORMS = _forms_by_length()
_FORM_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(rf"(?<!\w){re.escape(form)}(?!\w)"), form, code) for form, code in _FORMS
)


def status_forms_in(text: str) -> list[tuple[str, str]]:
    """``(status code, folded form)`` of every status form of ``text``, in text order.

    Longest form first; a span already taken by a longer form is not read again.
    """
    folded = fold(text)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str, str]] = []
    for pattern, form, code in _FORM_PATTERNS:
        for match in pattern.finditer(folded):
            start, end = match.span()
            if any(start < stop and begin < end for begin, stop in taken):
                continue
            taken.append((start, end))
            found.append((start, code, form))
    return [(code, form) for _, code, form in sorted(found)]


def status_faithful(text: str, status: str) -> bool:
    """The imposed rendering of ``status`` is in ``text``, and no form of another status."""
    rendering = ORACLE_RENDERINGS.get(status)
    if rendering is None:
        return False
    found = status_forms_in(text)
    if not found or any(code != status for code, _ in found):
        return False
    return any(form == fold(rendering) for _, form in found)


def date_forms(value: str) -> tuple[str, str] | None:
    """The ISO and French forms of a dual-rendered date, or None for another value."""
    match = _DUAL_DATE.fullmatch(value.strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def value_in(value: str, text: str) -> bool:
    """``value`` occurs in ``text`` as whole words (folded); a number is not read inside
    a longer number (« 15 » is not in « 2026-11-15 » unless it stands alone)."""
    target = fold(value)
    if not target:
        return False
    pattern = rf"(?<![\w.,-]){re.escape(target)}(?![\w]|[.,-]\d)"
    return re.search(pattern, fold(text)) is not None


def _status_code_of(value: str) -> str | None:
    folded = fold(value)
    for code, rendering in ORACLE_RENDERINGS.items():
        if fold(rendering) == folded:
            return code
    return None


def _hit(value: str, text: str, codes: frozenset[str]) -> bool:
    code = _status_code_of(value)
    if code is not None:
        return code in codes
    forms = date_forms(value)
    if forms is not None:
        return any(value_in(form, text) for form in forms)
    return value_in(value, text)


def forbidden_hits(texts: Sequence[str], values: Iterable[str]) -> list[str]:
    """The ``values`` (``must_not_include``) found in any of ``texts``, each once."""
    codes = frozenset(code for text in texts for code, _ in status_forms_in(text))
    hits: list[str] = []
    for value in dict.fromkeys(values):
        if any(_hit(value, text, codes) for text in texts):
            hits.append(value)
    return hits
