"""Orders and maintenance overviews (D3, D4) and their facts block (ADR 0018).

The connectors compute closed projections (``OrdersOverview``, ``MaintenanceOverview``):
no contact, price, note, technician field, remark or duration ever enters them. This module
renders them as a facts block, the same in tools and full mode; in full mode the model may
add one summary paragraph above it, which ``verify`` checks against the block.

The block writes each count as ``<label> : <n>`` on its own line or list item, so an
evaluation reads them back with :func:`read_counts` and compares them with its oracle.
"""

from __future__ import annotations

import re

from alverno_lab.agents.ops.rendering import STATUS_RENDERINGS
from alverno_lab.connectors.types import MaintenanceOverview, OrdersOverview

__all__ = [
    "maintenance_block",
    "orders_block",
    "read_counts",
]

_ORDERS_TOTAL = "Commandes au total"
_ORDERS_LATE = "Commandes en retard"
_STATUS = "Statut"
_CORRECTIVE = "Bons correctifs"
_ASSET = "Équipement"
_FAILURE = "Code de défaillance"


def orders_block(view: OrdersOverview) -> str:
    """The D3 facts block: counts by status, then the late orders, oldest revision first."""
    scope = f" (compte {view.account_id})" if view.account_id else ""
    lines = [f"Synthèse des commandes{scope}, selon l'ERP au {view.as_of.isoformat()}."]
    lines.append(f"- {_ORDERS_TOTAL} : {view.total}")
    for status, count in view.by_status:
        lines.append(f"- {_STATUS} {STATUS_RENDERINGS[status]} : {count}")
    lines.append(f"- {_ORDERS_LATE} : {len(view.late)}")
    for row in view.late:
        who = row.account_label or row.account_id
        lines.append(
            f"  - {row.order_id} — {who} — promise le {row.promised_date.isoformat()}, "
            f"révisée au {row.revised_date.isoformat()} — motif : {row.delay_label}"
        )
    return "\n".join(lines)


def maintenance_block(view: MaintenanceOverview) -> str:
    """The D4 facts block: corrective work orders by equipment and by failure code."""
    scope = f" pour {view.asset_id}" if view.asset_id else ""
    lines = [
        f"Synthèse de l'entretien{scope}, du {view.start.isoformat()} au {view.end.isoformat()}, "
        "selon la GMAO."
    ]
    lines.append(f"- {_CORRECTIVE} : {view.corrective}")
    for asset_id, name, count in view.by_asset:
        lines.append(f"- {_ASSET} {asset_id} ({name}) : {count}")
    for code, label, count in view.by_failure:
        lines.append(f"- {_FAILURE} {code} ({label}) : {count}")
    if not view.corrective:
        lines.append("Aucun bon de travail correctif n'a été ouvert pendant cette période.")
    return "\n".join(lines)


_STATUS_KEYS = {rendering: status for status, rendering in STATUS_RENDERINGS.items()}
_LINE = re.compile(r"^- (?P<label>.+?) : (?P<count>\d+)$", re.MULTILINE)
_ASSET_LINE = re.compile(rf"^{_ASSET} (EQ-[A-Z]{{3}}-\d{{2}}) \(")
_FAILURE_LINE = re.compile(rf"^{_FAILURE} ([A-Z]{{3}}-[A-Z]+) \(")


def read_counts(block: str) -> dict[str, int]:
    """The counts of a facts block, under the oracle's keys (ADR 0018, I2 precisions)."""
    counts: dict[str, int] = {}
    for match in _LINE.finditer(block):
        label, count = match.group("label"), int(match.group("count"))
        if label == _ORDERS_TOTAL:
            counts["orders.total"] = count
        elif label == _ORDERS_LATE:
            counts["orders.late"] = count
        elif label.startswith(f"{_STATUS} ") and label[len(_STATUS) + 1 :] in _STATUS_KEYS:
            counts[f"orders.status.{_STATUS_KEYS[label[len(_STATUS) + 1 :]]}"] = count
        elif label == _CORRECTIVE:
            counts["maintenance.corrective"] = count
        elif asset := _ASSET_LINE.match(label):
            counts[f"maintenance.asset.{asset.group(1)}"] = count
        elif failure := _FAILURE_LINE.match(label):
            counts[f"maintenance.failure.{failure.group(1)}"] = count
    return counts
