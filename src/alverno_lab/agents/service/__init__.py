"""Customer service (module 3a): deterministic routing, facts, drafter and graph (ADR 0012).

- :mod:`~alverno_lab.agents.service.route`: ``route_service``, pure, no model;
- :mod:`~alverno_lab.agents.service.drafting`: the « Faits de la commande » block and the
  tools-mode ``TemplateDrafter``;
- :mod:`~alverno_lab.agents.service.graph`: ``build_service_graph``, the builder an
  :class:`~alverno_lab.agents.ops.runtime.OpsEnvelope` compiles with its checkpointer.

The wiring (connectors, executor, authority, screen, policy, header) lives in
``ops_pipeline.py``.
"""

from __future__ import annotations

from alverno_lab.agents.service.drafting import (
    FACTS_HEADER,
    MAX_BODY_CHARS,
    DraftBody,
    Drafter,
    LabelSource,
    OrderFacts,
    TemplateDrafter,
    facts_block,
    order_facts,
)
from alverno_lab.agents.service.graph import (
    NEUTRAL_SALUTATION,
    SIGNATURE_TEXT,
    TO_PREFIX,
    build_service_graph,
    compose_text,
    contact_extract,
    make_check_edit,
    salutation_for,
    service_answer,
    verify_body,
)
from alverno_lab.agents.service.route import (
    ORDER_ID_RE,
    RouteRefusal,
    RouteResult,
    ServiceFlow,
    find_order_ids,
    has_drafting_word,
    route_service,
)

__all__ = [
    "FACTS_HEADER",
    "MAX_BODY_CHARS",
    "NEUTRAL_SALUTATION",
    "ORDER_ID_RE",
    "SIGNATURE_TEXT",
    "TO_PREFIX",
    "DraftBody",
    "Drafter",
    "LabelSource",
    "OrderFacts",
    "RouteRefusal",
    "RouteResult",
    "ServiceFlow",
    "TemplateDrafter",
    "build_service_graph",
    "compose_text",
    "contact_extract",
    "facts_block",
    "find_order_ids",
    "has_drafting_word",
    "make_check_edit",
    "order_facts",
    "route_service",
    "salutation_for",
    "service_answer",
    "verify_body",
]
