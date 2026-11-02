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
- :func:`make_ops_fake_chat` wraps it in a ``FakeChat`` named ``fake-ops``.

Increment I9 adds ``ops_diagnose_echo`` (3b) here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from alverno_lab.agents.service.prompts import FACTS_HEADER, SALUTATION, SIGNATURE
from alverno_lab.llm import FakeChat

__all__ = ["FACT_SENTENCES", "FAKE_OPS_MODEL", "make_ops_fake_chat", "ops_draft_echo"]

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


def make_ops_fake_chat(model: str = FAKE_OPS_MODEL) -> FakeChat:
    """``FakeChat`` answering every call with :func:`ops_draft_echo`."""
    return FakeChat(responder=ops_draft_echo, model=model)
