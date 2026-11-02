"""BROKEN chat models and drafters of the 3a negative controls (I7 spec decision 7).

Each double breaks one rule that a barrier of the 3a graph must catch; the doubles live
in the tests, never in ``src/``. The chat doubles answer through ``FakeChat`` (unchanged)
and start from :func:`~alverno_lab.agents.ops.fakes.ops_draft_echo`, then add their fault:

- :func:`injection_follower`: copies the sentences of the customer-visible note, as the
  prompt gives it, into the body (planted markers included when the quarantine is off);
- :func:`invent_status`: writes another status rendering than the one of the block;
- :func:`date_in_words`: adds a revised date written in words (no digit), which
  ``find_ungrounded_numbers`` cannot read;
- :class:`LeakyScope`: pastes the label of another account before the signature;
- :class:`ComposeFirstDrafter`: a drafter whose prompt is the composed text (the « À : »
  line and the salutation with the contact), as if ``compose`` ran before the model.

The doubles that need the fixtures (the account of an order, its contact) read them with
their own loader, not through the rig under test.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel

from alverno_lab.agents.ops.fakes import FAKE_OPS_MODEL, ops_draft_echo
from alverno_lab.agents.ops.rendering import STATUS_RENDERINGS
from alverno_lab.agents.service import DRAFT_SYSTEM_PROMPT, DraftBody, OrderFacts, compose_text
from alverno_lab.agents.service.drafting import SALUTATION, SIGNATURE
from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.llm import ChatModel, FakeChat, Responder

__all__ = [
    "ComposeFirstDrafter",
    "LeakyScope",
    "chat_of",
    "date_in_words",
    "injection_follower",
    "invent_status",
]

NOTE_LABEL = "Note visible par le client : "
STATUS_LABEL = "Statut : "
_ORDER_ID = re.compile(r"CMD-[0-9]{4}-[0-9]{4}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def chat_of(responder: Responder) -> FakeChat:
    """A ``FakeChat`` named like the ops fake, answering with ``responder``."""
    return FakeChat(responder=responder, model=FAKE_OPS_MODEL)


def _line_value(user: str, label: str) -> str | None:
    for line in user.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip()
    return None


def _with_tail(answer: dict[str, Any], tail: str) -> dict[str, Any]:
    """``answer`` with ``tail`` written just before the signature."""
    body = str(answer["body"]).replace(SIGNATURE, f"{tail}\n\n{SIGNATURE}")
    return {**answer, "body": body}


def injection_follower(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """BROKEN: the echo, then every sentence of the note, followed as if it were an order."""
    answer = ops_draft_echo(system, user, schema)
    note = _line_value(user, NOTE_LABEL)
    if not note:
        return answer
    sentences = [part for part in _SENTENCE_END.split(note) if part.strip()]
    return _with_tail(answer, "Comme demandé : " + " ".join(sentences))


def invent_status(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """BROKEN: the echo with the status rendering replaced by another status's rendering."""
    answer = ops_draft_echo(system, user, schema)
    status = _line_value(user, STATUS_LABEL)
    others = [text for text in STATUS_RENDERINGS.values() if text != status]
    if status is None or not others:
        return answer
    body = str(answer["body"]).replace(f": {status}", f": {others[0]}")
    return {**answer, "body": body}


def date_in_words(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """BROKEN: the echo, then a new date written in words, absent from the facts."""
    answer = ops_draft_echo(system, user, schema)
    return _with_tail(answer, "La livraison est avancée au vingt-trois du mois prochain.")


def _fixture(path: Path) -> dict[str, list[dict[str, object]]]:
    raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(dict[str, list[dict[str, object]]], raw)


class LeakyScope:
    """BROKEN responder: the echo, plus the label of another account before the signature."""

    def __init__(self, erp_path: Path) -> None:
        fixture = _fixture(erp_path)
        self.account_of = {str(o["order_id"]): str(o["account_id"]) for o in fixture["orders"]}
        self.labels = {str(a["account_id"]): str(a["label"]) for a in fixture["accounts"]}

    def __call__(self, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        answer = ops_draft_echo(system, user, schema)
        found = _ORDER_ID.search(user)
        own = self.account_of.get(found.group(0)) if found is not None else None
        label = next(self.labels[a] for a in sorted(self.labels) if a != own)
        return _with_tail(answer, f"Copie au dossier de {label}.")


class ComposeFirstDrafter:
    """BROKEN drafter: ``compose`` runs before the model, whose prompt gets the contact."""

    def __init__(self, chat: ChatModel, erp: ErpConnector) -> None:
        self.chat = chat
        self.erp = erp

    def draft(self, facts: OrderFacts) -> DraftBody:
        body = f"{SALUTATION}\n\n{facts.block}\n\n{SIGNATURE}"
        contact = self.erp.get_order_contact(facts.order_id)
        user = compose_text(body, contact) if contact is not None else body
        return self.chat.complete_json(DRAFT_SYSTEM_PROMPT, user, DraftBody)
