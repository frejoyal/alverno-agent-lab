"""ERP connector protocol (ADR 0012, « Connecteurs simulés et projections liées à une fin »).

Its only implementation is :class:`alverno_lab.connectors.sim.SimErp`. Who calls what:

- ``get_order``: the tool executor (tool ``erp.get_order``);
- ``get_order_contact``: code only, never a tool nor in the registry: the ``compose``
  node writes the draft with it; ``guard``, ``reguard`` and the operator header read it
  too, by the in-scope order id and in memory only (ADR 0012, I6 précisions);
- ``record_reply_prepared``: the ``apply`` node only, through the executor, with an
  approval token (ADR 0013);
- ``carrier_label`` and ``delay_label``: the facts block of the 3a graph (business
  vocabulary of the fixture, not personal information; ADR 0015 « Rendu des statuts et
  des dates »). They are not tools and read no record.
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
    """Read an order, its contact and two code labels; record that a reply was prepared."""

    def get_order(self, order_id: OrderId) -> OrderView | None:
        """The order's view, or ``None`` when no such order exists."""
        ...

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        """The order's contact, or ``None`` when no such order exists."""
        ...

    def carrier_label(self, code: str) -> str | None:
        """The label of a carrier code, or ``None`` for an unknown code."""
        ...

    def delay_label(self, code: str) -> str | None:
        """The label of a delay reason code, or ``None`` for an unknown code."""
        ...

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        """Record that the approved draft (by digest only) was prepared for this order."""
        ...
