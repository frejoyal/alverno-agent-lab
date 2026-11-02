"""Deterministic routing of a 3a request (ADR 0012, « Routage déterministe »).

:func:`route_service` reads the redacted request and nothing else; it calls no model.

- Order ids are the ADR 0012 prefixed ids ``CMD-NNNN-NNNN``, read as whole tokens after
  ``fold_separators`` (a non-breaking hyphen or an en dash hides nothing). An id without
  its prefix (« commande 2026-0412 ») is not an order id, on purpose.
- No order id: refusal ``order_not_identified``. Two distinct ids or more: refusal
  ``multi_order`` (one request, one order, one account).
- One id and a word of the drafting family: ``customer_reply``. The family is closed and
  made of general French words: the verbs préparer, rédiger, écrire, répondre, envoyer,
  transmettre and composer (infinitive and imperative, with a hyphenated pronoun such as
  « rédige-lui »), and the nouns « une réponse », « un courriel », « un message » and
  « un brouillon ». Case and accents are ignored.
- One id otherwise: ``order_status``.

A deterministic route errs on paraphrases (« envoie » in a question about a shipment
routes to a draft); those errors are measured (``route_accuracy``, ``false_block_rate``),
not fixed by a model-chosen plan.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Literal

from alverno_lab.privacy.lexicon import clean_text, fold_separators, normalize

__all__ = [
    "ORDER_ID_RE",
    "RouteRefusal",
    "RouteResult",
    "ServiceFlow",
    "find_order_ids",
    "has_drafting_word",
    "route_service",
]

ServiceFlow = Literal["order_status", "customer_reply"]
RouteRefusal = Literal["order_not_identified", "multi_order"]

# [0-9], never \d (which matches non-ASCII digits); a whole token, not part of a longer id.
ORDER_ID_RE = re.compile(r"(?<![\w-])CMD-[0-9]{4}-[0-9]{4}(?![\w-])")

# Drafting verbs, normalised (no accents): infinitive, tu and vous imperatives.
_VERBS = (
    "prepare|preparer|preparez"
    "|redige|rediger|redigez"
    "|ecris|ecrire|ecrivez"
    "|reponds|repondre|repondez"
    "|envoie|envoyer|envoyez"
    "|transmets|transmettre|transmettez"
    "|compose|composer|composez"
)
# A hyphenated pronoun after an imperative (« rédige-lui », « prépare-moi », « envoie-la »).
_CLITICS = r"(?:-(?:moi|toi|lui|leur|nous|vous|la|le|les|en|y))*"
_DRAFTING_RE = re.compile(
    rf"(?<![\w-])(?:(?:{_VERBS}){_CLITICS}"
    r"|une reponse|un courriel|un message|un brouillon)(?![\w-])"
)
_SPACES_RE = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class RouteResult:
    """A flow and its order id, or a routing refusal; never both."""

    flow: ServiceFlow | None
    order_id: str | None
    refusal: RouteRefusal | None

    def __post_init__(self) -> None:
        routed = self.flow is not None and self.order_id is not None and self.refusal is None
        refused = self.refusal is not None and self.flow is None and self.order_id is None
        if not (routed or refused):
            raise ValueError("route:inconsistent")


def find_order_ids(text: str) -> list[str]:
    """The distinct prefixed order ids of ``text``, in order of appearance."""
    folded = fold_separators(clean_text(text))
    return list(dict.fromkeys(ORDER_ID_RE.findall(folded)))


def has_drafting_word(text: str) -> bool:
    """Whether ``text`` holds a word of the closed drafting family (see module docstring)."""
    flat = _SPACES_RE.sub(" ", normalize(fold_separators(clean_text(text))))
    return _DRAFTING_RE.search(flat) is not None


def route_service(request_redacted: str) -> RouteResult:
    """Route a redacted 3a request; pure, no model, no I/O."""
    ids = find_order_ids(request_redacted)
    if not ids:
        return RouteResult(flow=None, order_id=None, refusal="order_not_identified")
    if len(ids) > 1:
        return RouteResult(flow=None, order_id=None, refusal="multi_order")
    flow: ServiceFlow = "customer_reply" if has_drafting_word(request_redacted) else "order_status"
    return RouteResult(flow=flow, order_id=ids[0], refusal=None)
