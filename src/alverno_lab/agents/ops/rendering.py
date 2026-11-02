"""The ADR 0015 rendering of statuses and dates, shared by the graphs and the evaluation.

ADR 0015 « Rendu des statuts et des dates » fixes one table before any measure; the
facts block of the 3a graph, the tools-mode drafter, the ``verify`` step and the gold
loaders all read it from here (``evals/ops_gold.py`` re-exports it, so the loaders keep
their imports). The independent oracle of the evaluation keeps its own copy, by design.

- Dates have a dual rendering, ``2026-11-15 (15 novembre 2026)``: the day without a
  leading zero, « 1er » for the first day of a month, the month in lower case
  (:func:`render_date`).
- Each ERP status has one imposed rendering (:data:`STATUS_RENDERINGS`) and a closed set
  of recognised forms (:data:`STATUS_FORMS`, longest first).
- :func:`find_status_forms` reads a text for those forms: NFKC, case and accents
  ignored, whole words, the longest form first, so « expédiée en partie » is read before
  « expédiée » and an ``expediee`` form is never taken inside an ``expediee_partielle``
  one. A form used in another sense (« votre demande a été reçue ») still counts: that
  false positive is accepted and published (ADR 0015), not a reason to widen the table.

Carrier and delay-reason labels are not here: they come from the simulated ERP
(``SimErp.carrier_label`` and ``SimErp.delay_label``), never from a gold set.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from types import MappingProxyType

from alverno_lab.connectors.types import OrderStatus
from alverno_lab.privacy.lexicon import normalize

__all__ = [
    "FORM_STATUS",
    "MONTHS_FR",
    "STATUS_FORMS",
    "STATUS_RENDERINGS",
    "find_status_forms",
    "render_date",
]

# The rendering each ERP status must have (ADR 0015 table, second column).
STATUS_RENDERINGS: Mapping[OrderStatus, str] = MappingProxyType(
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

# The forms recognised by ``verify`` and the gold loaders (ADR 0015 table, « Formes
# reconnues »), longest first within each status.
STATUS_FORMS: Mapping[OrderStatus, tuple[str, ...]] = MappingProxyType(
    {
        "recue": ("reçues", "reçue"),
        "planifiee": ("planifiées", "planifiée"),
        "en_production": ("en production",),
        "en_finition": ("en finition",),
        "expediee_partielle": (
            "partiellement expédiées",
            "partiellement expédiée",
            "expédiées en partie",
            "expédiée en partie",
        ),
        "expediee": ("expédiées", "expédiée"),
        "en_attente_client": ("en attente de votre part", "en attente du client"),
    }
)

# Each recognised form and the status it renders.
FORM_STATUS: Mapping[str, OrderStatus] = MappingProxyType(
    {form: status for status, forms in STATUS_FORMS.items() for form in forms}
)

MONTHS_FR: tuple[str, ...] = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_WHITESPACE_RE = re.compile(r"\s+")
# Normalised form -> table form; the alternation tries the longest form first, so at a
# given position « expediee en partie » wins over « expediee ».
_BY_NORMAL: Mapping[str, str] = MappingProxyType({normalize(form): form for form in FORM_STATUS})
_FORMS_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(form) for form in sorted(_BY_NORMAL, key=len, reverse=True))
    + r")(?!\w)"
)


def render_date(day: dt.date) -> str:
    """The dual rendering ``2026-11-15 (15 novembre 2026)``; day 1 is written « 1er »."""
    number = "1er" if day.day == 1 else str(day.day)
    return f"{day.isoformat()} ({number} {MONTHS_FR[day.month - 1]} {day.year})"


def find_status_forms(text: str) -> list[str]:
    """The status forms of ``text``, in order, as written in the ADR 0015 table.

    Matching is on ``normalize``d text (NFKC, case and accents folded), with every run of
    white space read as one space, on whole words, the longest form first; matches do not
    overlap.
    """
    flat = _WHITESPACE_RE.sub(" ", normalize(text))
    return [_BY_NORMAL[match.group(0)] for match in _FORMS_RE.finditer(flat)]
