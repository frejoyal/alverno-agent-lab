"""Fake responders of the module 3 full mode (ADR 0015 « Moteurs simulés et mode outils »).

``FakeChat`` of ``llm.py`` does not change; these responders are plugged into it. They read
the prompt only, never an evaluation item, a gold field or a fixture file:

- :func:`ops_draft_echo` (3a): parses the « Faits de la commande » block of the user
  message and returns a ``DraftBody`` mapping whose body is ``{salutation}``, one French
  sentence per fact line with the value copied verbatim (dates in their dual rendering,
  the status rendering, quantities as digits, labels, the note), then ``{signature}``; it
  cites the order id read on the block's « Commande » line. Its own wording holds no digit
  and no status form, so ``verify`` grounds every number and reads only the status the
  block gives.
- :func:`ops_diagnose_echo` (3b, I9 spec decision 8): parses the symptom line and the
  history lines of the user message
  (:func:`~alverno_lab.agents.maintenance.diagnosis.diagnosis_prompt`) and returns a
  ``Diagnosis`` mapping: the most frequent failure code among the lines that carry the
  reported symptom (all the lines when none does or no symptom is given; ties go to the
  code of the most recent line, then to the code), up to 4 lines carrying it as
  citations (most recent first) and one generic next check. A frequency ranking: it
  misses the frequency traps of ADR 0015 by design.
- :func:`ops_echo` dispatches on the schema: ``Diagnosis`` goes to
  :func:`ops_diagnose_echo`, every other schema to :func:`ops_draft_echo` (the 3a calls
  are answered exactly as before).
- :func:`make_ops_fake_chat` wraps :func:`ops_echo` in a ``FakeChat`` named ``fake-ops``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from alverno_lab.agents.maintenance.diagnosis import MAX_CITATIONS, Diagnosis
from alverno_lab.agents.maintenance.prompts import HISTORY_HEADER, SYMPTOM_LABEL
from alverno_lab.agents.service.prompts import FACTS_HEADER, SALUTATION, SIGNATURE
from alverno_lab.connectors.types import SYMPTOM_CODES
from alverno_lab.llm import FakeChat

__all__ = [
    "DIAGNOSE_CHECK",
    "FACT_SENTENCES",
    "FAKE_OPS_MODEL",
    "make_ops_fake_chat",
    "ops_diagnose_echo",
    "ops_draft_echo",
    "ops_echo",
]

FAKE_OPS_MODEL = "fake-ops"
ORDER_LABEL = "Commande"
_SEPARATOR = " : "
_ORDER_ID = re.compile(r"CMD-[0-9]{4}-[0-9]{4}")
_END_PUNCTUATION = (".", "!", "?", "…")

# One sentence per fact label of the block; ``{value}`` is the value as the block writes
# it. No digit and no status form in the wording.
FACT_SENTENCES: Mapping[str, str] = MappingProxyType(
    {
        ORDER_LABEL: "Ce message porte sur votre commande {value}",
        "Pièce": "Elle vise la pièce {value}",
        "Quantité commandée": "La quantité commandée est de {value}",
        "Quantité déjà envoyée": "La quantité déjà envoyée est de {value}",
        "Statut": "Son statut actuel : {value}",
        "Date promise": "La date promise était le {value}",
        "Date révisée": "La nouvelle date prévue est le {value}",
        "Transporteur": "Le transporteur retenu est {value}",
        "Numéro de suivi": "Le numéro de suivi est {value}",
        "Motif du retard": "Motif du retard : {value}",
        "Note visible par le client": "Précision au dossier : {value}",
    }
)


def _facts(user: str) -> list[tuple[str, str]]:
    """``(label, value)`` of every « Libellé : valeur » line after the block header."""
    lines = user.splitlines()
    try:
        start = [line.strip() for line in lines].index(FACTS_HEADER) + 1
    except ValueError:
        return []
    found: list[tuple[str, str]] = []
    for line in lines[start:]:
        label, separator, value = line.partition(_SEPARATOR)
        if separator and label.strip() and value.strip():
            found.append((label.strip(), value.strip()))
    return found


def _sentence(label: str, value: str) -> str:
    template = FACT_SENTENCES.get(label)
    text = template.format(value=value) if template is not None else f"{label}{_SEPARATOR}{value}"
    return text if text.endswith(_END_PUNCTUATION) else f"{text}."


def ops_draft_echo(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """A ``DraftBody`` mapping that restates every fact line of ``user`` (module doc)."""
    del system, schema
    facts = _facts(user)
    sentences = [_sentence(label, value) for label, value in facts]
    cited = [
        value
        for label, value in facts
        if label == ORDER_LABEL and _ORDER_ID.fullmatch(value) is not None
    ][:1]
    parts = [SALUTATION, "\n".join(sentences), SIGNATURE] if sentences else [SALUTATION, SIGNATURE]
    return {"body": "\n\n".join(parts), "cited_ids": cited}


# --------------------------------------------------------------------------- 3b
# The one next check of the diagnosis echo: about the machine, no digit, no value.
DIAGNOSE_CHECK = "Inspecter la machine selon le code retenu avant toute remise en service."
_HISTORY_FIELDS = 7
_NO_VALUE = "-"


def _reported_symptom(user: str) -> str | None:
    """The symptom code of the « Symptôme signalé » line (None when not given)."""
    prefix = f"{SYMPTOM_LABEL}{_SEPARATOR}"
    for line in user.splitlines():
        if line.startswith(prefix):
            code = line[len(prefix) :].split(" | ", 1)[0].strip()
            return code if code in SYMPTOM_CODES else None
    return None


def _history(user: str) -> list[tuple[str, str, str, str]]:
    """``(wo_id, date, symptom, failure)`` of every history line of ``user``."""
    lines = user.splitlines()
    try:
        start = [line.strip() for line in lines].index(f"{HISTORY_HEADER} :") + 1
    except ValueError:
        return []
    found: list[tuple[str, str, str, str]] = []
    for line in lines[start:]:
        if not line.strip():
            break
        # An empty remark leaves the line ending on « | »: split on the bare bar.
        fields = [part.strip() for part in line.split("|", _HISTORY_FIELDS - 1)]
        if len(fields) >= _HISTORY_FIELDS - 1:
            wo_id, date, _kind, symptom, failure = fields[:5]
            found.append((wo_id, date, symptom, failure))
    return found


def ops_diagnose_echo(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """A ``Diagnosis`` mapping: the most frequent code of the history (module doc)."""
    del system, schema
    symptom = _reported_symptom(user)
    rows = [row for row in _history(user) if row[3] != _NO_VALUE]
    carrying = [row for row in rows if symptom is not None and row[2] == symptom]
    considered = carrying or rows
    newest = sorted(considered, key=lambda row: (row[1], row[0]), reverse=True)
    counts: dict[str, int] = {}
    for row in newest:
        counts[row[3]] = counts.get(row[3], 0) + 1
    # Stable sorts, last key first: the code, then the most recent line, then the count.
    order = sorted(counts)
    order.sort(key=lambda code: next(i for i, row in enumerate(newest) if row[3] == code))
    order.sort(key=lambda code: -counts[code])
    if not order:
        return {"codes": [], "cited_wo_ids": [], "next_checks": [DIAGNOSE_CHECK], "confidence": 0.0}
    top = order[0]
    cited = [row[0] for row in newest if row[3] == top][:MAX_CITATIONS]
    return {
        "codes": [top],
        "cited_wo_ids": cited,
        "next_checks": [DIAGNOSE_CHECK],
        "confidence": round(counts[top] / len(newest), 2),
    }


def ops_echo(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """:func:`ops_diagnose_echo` for a ``Diagnosis``, :func:`ops_draft_echo` otherwise."""
    if issubclass(schema, Diagnosis):
        return ops_diagnose_echo(system, user, schema)
    return ops_draft_echo(system, user, schema)


def make_ops_fake_chat(model: str = FAKE_OPS_MODEL) -> FakeChat:
    """``FakeChat`` answering every call with :func:`ops_echo` (3a and 3b)."""
    return FakeChat(responder=ops_echo, model=model)
