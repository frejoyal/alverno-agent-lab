"""ERP connector protocol (ADR 0012, « Connecteurs simulés et projections liées à une fin »).

Its only implementation is :class:`alverno_lab.connectors.sim.SimErp`. Who calls what:

- ``get_order``: the tool executor (tool ``erp.get_order``);
- ``get_order_contact``: the ``compose`` node only; it is not a tool and never enters
  the registry;
- ``record_reply_prepared``: the ``apply`` node only, through the executor, with an
  approval token (ADR 0013).
"""

from __future__ import annotations

from typing import Protocol

from alverno_lab.connectors.types import (
    ContactView,
    OrderId,
    OrderView,
    WriteReceipt,
    WriteToken,
)


class ErpConnector(Protocol):
    """Read an order, read its contact, record that a reply was prepared."""

    def get_order(self, order_id: OrderId) -> OrderView | None:
        """The order's view, or ``None`` when no such order exists."""
        ...

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        """The order's contact, or ``None`` when no such order exists."""
        ...

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        """Record that the approved draft (by digest only) was prepared for this order."""
        ...
