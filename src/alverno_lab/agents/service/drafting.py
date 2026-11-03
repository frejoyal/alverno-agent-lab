"""Facts block and tools-mode drafter of the 3a graph (ADR 0012 « Graphes », ADR 0015).

:func:`order_facts` turns an :class:`~alverno_lab.connectors.types.OrderView` into
:class:`OrderFacts`: the order id, the part reference, the quantities (digits), the
imposed status rendering, the promised and revised dates (dual rendering), the carrier
label and tracking reference, the delay reason label and the customer-visible note
(already quarantined). Labels come from the simulated ERP (``carrier_label``,
``delay_label``), never from a gold set. :attr:`OrderFacts.block` is the « Faits de la
commande » block: one line per fact, a line only when its value exists.

The drafter receives the facts only: no account label, no contact, no request. Its body
may use two placeholders, ``{salutation}`` and ``{signature}``, which the ``compose``
node fills by code. :class:`TemplateDrafter` is the drafter of the tools mode: a fixed
French template per status family that restates every fact present, with
``{salutation}`` first, ``{signature}`` last and the order id as its only citation. It
calls no model. Its wording avoids every status form of the ADR 0015 table but the
imposed rendering of the order's own status.

:class:`ChatDrafter` is the drafter of the full mode (I7 spec decision 1): one
``complete_json`` call with the system prompt of
:mod:`~alverno_lab.agents.service.prompts` and, as the user message, the facts block and
nothing else. Its failures carry no value and no chain:

- a model that cannot serve any draft (the server is down,
  :class:`~alverno_lab.ollama.OllamaUnavailableError`, or the model is not pulled,
  :class:`~alverno_lab.ollama.OllamaModelNotFoundError`) raises
  :class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError`, a
  ``ConnectorError``: the ``draft`` node lets it escape, and the envelope logs ``error``
  with ``connector_unavailable`` and raises ``<step>:model_unavailable``, as
  ``runtime._classify`` does for a connector failure. Every later item would fail the
  same way, so the evaluation stops there (as the module 1 and 2 runners do);
- any other failure raises :class:`DraftError`, and the ``draft`` node turns it into its
  ``draft_failed`` error (``outcome="error"``, no refusal reason), which counts in
  ``error_rate`` for that item only: an invalid model output
  (:class:`~alverno_lab.llm.LLMOutputError`, after the one repair retry of the chat
  model) is ``draft:invalid_output``, a timeout
  (:class:`~alverno_lab.ollama.OllamaTimeoutError`) is ``draft:timeout``, and any other
  exception (an HTTP or protocol error included) is ``draft:chat_error``. One slow
  answer must not throw away a whole run.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from alverno_lab.agents.ops.rendering import STATUS_RENDERINGS, render_date
from alverno_lab.agents.ops.runtime import ModelUnavailableError
from alverno_lab.agents.service.prompts import (
    DRAFT_SYSTEM_PROMPT,
    FACTS_HEADER,
    MAX_BODY_CHARS,
    SALUTATION,
    SIGNATURE,
)
from alverno_lab.connectors.types import OrderId, OrderStatus, OrderView
from alverno_lab.llm import ChatModel, LLMOutputError
from alverno_lab.ollama import (
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)

__all__ = [
    "DRAFT_SYSTEM_PROMPT",
    "FACTS_HEADER",
    "MAX_BODY_CHARS",
    "PLACEHOLDERS",
    "SALUTATION",
    "SIGNATURE",
    "ChatDrafter",
    "DraftBody",
    "DraftError",
    "Drafter",
    "LabelSource",
    "OrderFacts",
    "TemplateDrafter",
    "facts_block",
    "order_facts",
]

PLACEHOLDERS: frozenset[str] = frozenset({SALUTATION, SIGNATURE})

_SPACES_RE = re.compile(r"\s+")

# The closing sentence of each status family; no number, no other status form.
_FAMILY_SENTENCES: Mapping[OrderStatus, str] = MappingProxyType(
    {
        "recue": "Nous vous écrirons de nouveau dès que sa fabrication commencera.",
        "planifiee": "Nous vous écrirons de nouveau dès que sa fabrication commencera.",
        "en_production": "Nous suivons sa fabrication de près et vous tiendrons au courant.",
        "en_finition": "Nous suivons les dernières étapes de près et vous tiendrons au courant.",
        "expediee_partielle": "Le solde de la commande suivra dans un prochain envoi.",
        "expediee": "N'hésitez pas à nous écrire si un élément de la livraison pose problème.",
        "en_attente_client": "Nous attendons votre retour pour poursuivre le traitement.",
    }
)


class LabelSource(Protocol):
    """What the facts need from the ERP: the two read-only code labels."""

    def carrier_label(self, code: str) -> str | None:
        """The label of a carrier code, or ``None``."""
        ...

    def delay_label(self, code: str) -> str | None:
        """The label of a delay reason code, or ``None``."""
        ...


class OrderFacts(BaseModel):
    """The facts of one order, as the drafter and ``verify`` see them.

    The labels and the note are business texts of the fixture; they stay out of
    ``repr`` all the same, like every text of this module.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    order_id: OrderId
    part_ref: str
    qty_ordered: int
    qty_shipped: int
    status: OrderStatus
    promised_date: dt.date
    revised_date: dt.date | None = None
    carrier_label: str | None = Field(default=None, repr=False)
    tracking_ref: str | None = None
    delay_label: str | None = Field(default=None, repr=False)
    note: str | None = Field(default=None, repr=False)

    @property
    def status_text(self) -> str:
        """The imposed rendering of the status (ADR 0015)."""
        return STATUS_RENDERINGS[self.status]

    def lines(self) -> list[str]:
        """One ``Libellé : valeur`` line per fact present, in a fixed order."""
        found = [
            f"Commande : {self.order_id}",
            f"Pièce : {self.part_ref}",
            f"Quantité commandée : {self.qty_ordered}",
            f"Quantité déjà envoyée : {self.qty_shipped}",
            f"Statut : {self.status_text}",
            f"Date promise : {render_date(self.promised_date)}",
        ]
        if self.revised_date is not None:
            found.append(f"Date révisée : {render_date(self.revised_date)}")
        if self.carrier_label is not None:
            found.append(f"Transporteur : {self.carrier_label}")
        if self.tracking_ref is not None:
            found.append(f"Numéro de suivi : {self.tracking_ref}")
        if self.delay_label is not None:
            found.append(f"Motif du retard : {self.delay_label}")
        if self.note is not None:
            found.append(f"Note visible par le client : {self.note}")
        return found

    @property
    def block(self) -> str:
        """The « Faits de la commande » block: the header, then one line per fact."""
        return "\n".join([FACTS_HEADER, *self.lines()])


def _label(read: Callable[[str], str | None], code: str | None) -> str | None:
    if code is None:
        return None
    label = read(code)
    return label if isinstance(label, str) and label.strip() else None


def _note(order: OrderView) -> str | None:
    note = order.customer_visible_note
    if note is None:
        return None
    flat = _SPACES_RE.sub(" ", note.text).strip()
    return flat or None


def order_facts(order: OrderView, erp: LabelSource) -> OrderFacts:
    """The facts of ``order``; labels are read from ``erp``, a missing label is omitted."""
    return OrderFacts(
        order_id=order.order_id,
        part_ref=order.part_ref,
        qty_ordered=order.qty_ordered,
        qty_shipped=order.qty_shipped,
        status=order.status,
        promised_date=order.promised_date,
        revised_date=order.revised_date,
        carrier_label=_label(erp.carrier_label, order.carrier_code),
        tracking_ref=order.tracking_ref,
        delay_label=_label(erp.delay_label, order.delay_reason_code),
        note=_note(order),
    )


def facts_block(order: OrderView, erp: LabelSource) -> str:
    """The « Faits de la commande » block of ``order`` (see :class:`OrderFacts`)."""
    return order_facts(order, erp).block


class DraftBody(BaseModel):
    """A drafted body and the order ids it cites (ADR 0012, ``DraftBody``)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS, repr=False)
    cited_ids: list[OrderId] = Field(default_factory=list, max_length=4)


class Drafter(Protocol):
    """Writes a body from the facts only (tools mode: a template; full mode: a model)."""

    def draft(self, facts: OrderFacts) -> DraftBody:
        """The body and its citations; raise on failure."""
        ...


class TemplateDrafter:
    """The tools-mode drafter: a fixed French template that restates every fact present."""

    def draft(self, facts: OrderFacts) -> DraftBody:
        """One sentence per fact present, framed by ``{salutation}`` and ``{signature}``."""
        lines = [f"Voici le suivi de votre commande {facts.order_id} (pièce {facts.part_ref})."]
        lines.append(f"Statut : {facts.status_text}.")
        lines.append(
            f"Quantité commandée : {facts.qty_ordered} ; "
            f"quantité déjà envoyée : {facts.qty_shipped}."
        )
        lines.append(f"Date promise : {render_date(facts.promised_date)}.")
        if facts.revised_date is not None:
            lines.append(f"Nouvelle date prévue : {render_date(facts.revised_date)}.")
        if facts.delay_label is not None:
            lines.append(f"Motif du retard : {facts.delay_label}.")
        if facts.carrier_label is not None:
            lines.append(f"Transporteur : {facts.carrier_label}.")
        if facts.tracking_ref is not None:
            lines.append(f"Numéro de suivi : {facts.tracking_ref}.")
        if facts.note is not None:
            lines.append(f"Précision au dossier : {facts.note}")
        body = "\n\n".join(
            [
                SALUTATION,
                "\n".join(lines),
                f"{_FAMILY_SENTENCES[facts.status]} Merci de votre confiance.",
                SIGNATURE,
            ]
        )
        return DraftBody(body=body, cited_ids=[facts.order_id])


class DraftError(RuntimeError):
    """The model gave no usable draft. The message is ``draft:<rule>``, never a value."""


class ChatDrafter:
    """The full-mode drafter: one ``complete_json`` call on the facts block only.

    ``chat`` is the counted, probed model of the rig. The user message is exactly
    :attr:`OrderFacts.block`: no account label, no contact, no request text.
    """

    def __init__(self, chat: ChatModel) -> None:
        self.chat = chat

    def draft(self, facts: OrderFacts) -> DraftBody:
        """The model's body and citations; raise :class:`DraftError` or
        :class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError` (module doc)."""
        rule: str | None = None
        drafted: object = None
        try:
            drafted = self.chat.complete_json(DRAFT_SYSTEM_PROMPT, facts.block, DraftBody)
        # Never chained: the model's error may quote its output or the facts.
        except LLMOutputError:
            rule = "invalid_output"
        except (OllamaUnavailableError, OllamaModelNotFoundError):
            # Server down or model not pulled: every draft would fail the same way.
            rule = "model_unavailable"
        except OllamaTimeoutError:
            rule = "timeout"
        except Exception:
            rule = "chat_error"
        if rule == "model_unavailable":
            raise ModelUnavailableError("draft:model_unavailable")
        if rule is not None or not isinstance(drafted, DraftBody):
            raise DraftError(f"draft:{rule or 'invalid_output'}")
        return drafted
