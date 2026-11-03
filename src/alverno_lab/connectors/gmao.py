"""GMAO connector protocol (ADR 0012, « Connecteurs simulés et projections liées à une fin »).

Its only implementation is :class:`alverno_lab.connectors.sim.SimGmao`. The reads serve
the ``diagnosis`` and ``work_request`` flows; ``create_work_request`` is called by the
``apply`` node only, through the executor, with an approval token (ADR 0013).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from alverno_lab.connectors.types import (
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    AssetId,
    AssetView,
    FailureCodeView,
    SymptomCode,
    WorkOrderView,
    WorkRequest,
    WriteReceipt,
    WriteToken,
)


class GmaoConnector(Protocol):
    """Read an asset, its recent history and the failure catalogue; open a work request."""

    def get_asset(self, asset_id: AssetId) -> AssetView | None:
        """The asset's view, or ``None`` when no such asset exists."""
        ...

    def list_work_orders(
        self,
        asset_id: AssetId,
        symptom_code: SymptomCode | None,
        since_days: int = DEFAULT_HISTORY_DAYS,
        limit: int = DEFAULT_WORK_ORDER_LIMIT,
    ) -> tuple[WorkOrderView, ...]:
        """Newest work orders of the asset in the window, filtered by symptom when given."""
        ...

    def symptom_labels(self) -> Mapping[SymptomCode, str]:
        """The label of each closed symptom code (read by the 3b routing code; not a tool)."""
        ...

    def failure_catalog(self) -> tuple[FailureCodeView, ...]:
        """The closed failure catalogue (no human-error category)."""
        ...

    def create_work_request(self, req: WorkRequest, *, token: WriteToken) -> WriteReceipt:
        """Open a corrective work request made of identifiers and closed codes only."""
        ...
