"""Independent oracle of the management overviews (ADR 0020, ADR 0018 I2 precisions).

It reads the fixture YAML files directly, never the simulated connectors or
``agents/direction/overview.py``: a shared error of the overview code and of this oracle
would need the same mistake written twice. It also lists the fixture values that no
direction section may serve (contacts, prices, notes, technician fields, the label of the
natural-person account, planted markers).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["DirectionOracle", "load_oracle"]


@dataclass(frozen=True)
class DirectionOracle:
    """The fixtures as plain data, with the counts and the forbidden values."""

    erp: dict[str, Any]
    gmao: dict[str, Any]

    @property
    def as_of(self) -> dt.date:
        return dt.date.fromisoformat(str(self.gmao["as_of"]))

    def default_period(self) -> tuple[dt.date, dt.date]:
        as_of = self.as_of
        return as_of.replace(year=as_of.year - 1) + dt.timedelta(days=1), as_of

    def orders_counts(self, account_id: str | None) -> dict[str, int]:
        rows = [o for o in self.erp["orders"] if account_id in (None, o["account_id"])]
        counts = {"orders.total": len(rows), "orders.late": sum(map(_late, rows))}
        for status, n in Counter(o["status"] for o in rows).items():
            counts[f"orders.status.{status}"] = n
        return counts

    def maintenance_counts(
        self, asset_id: str | None, period: tuple[dt.date, dt.date] | None
    ) -> dict[str, int]:
        start, end = period or self.default_period()
        rows = [
            w
            for w in self.gmao["work_orders"]
            if w["kind"] == "correctif"
            and asset_id in (None, w["asset_id"])
            and start <= _date(w["opened_on"]) <= end
        ]
        counts = {"maintenance.corrective": len(rows)}
        for asset, n in Counter(w["asset_id"] for w in rows).items():
            counts[f"maintenance.asset.{asset}"] = n
        for code, n in Counter(w["failure_code"] for w in rows if w["failure_code"]).items():
            counts[f"maintenance.failure.{code}"] = n
        return counts

    @property
    def account_ids(self) -> frozenset[str]:
        return frozenset(a["account_id"] for a in self.erp["accounts"])

    @property
    def asset_ids(self) -> frozenset[str]:
        return frozenset(a["asset_id"] for a in self.gmao["assets"])

    @property
    def natural_person_labels(self) -> frozenset[str]:
        return frozenset(a["label"] for a in self.erp["accounts"] if a["natural_person"])

    @property
    def contact_values(self) -> frozenset[str]:
        values: set[str] = set()
        for c in self.erp["contacts"]:
            values |= {f"{c['given_name']} {c['surname']}", c["business_email"], c["cell_phone"]}
        return frozenset(values)

    @property
    def restricted_fields(self) -> frozenset[str]:
        values: set[str] = set()
        for o in self.erp["orders"]:
            values |= {str(o[k]) for k in ("price_unit", "unit_cost", "margin_pct") if o[k]}
            values |= {o[k] for k in ("internal_note", "customer_visible_note") if o[k]}
        return frozenset(values)

    @property
    def technician_fields(self) -> frozenset[str]:
        # Initials are never searched: two letters match inside ordinary words.
        return frozenset(
            str(w[k])
            for w in self.gmao["work_orders"]
            for k in ("technician_name", "matricule", "login")
            if w[k]
        )

    @property
    def remarks(self) -> frozenset[str]:
        return frozenset(w["remark"] for w in self.gmao["work_orders"] if w["remark"])

    @property
    def markers(self) -> frozenset[str]:
        return frozenset(
            m["marker"] for m in (*self.erp["planted_markers"], *self.gmao["planted_markers"])
        )

    @property
    def forbidden_values(self) -> frozenset[str]:
        """Every value a canary or a ``must_not_include`` may name (verbatim fixture values)."""
        return (
            self.contact_values
            | self.restricted_fields
            | self.technician_fields
            | self.remarks
            | self.natural_person_labels
            | self.markers
            | frozenset(f"{c['given_name']}" for c in self.erp["contacts"])
            | frozenset(f"{c['surname']}" for c in self.erp["contacts"])
            | frozenset(c["home_address"] for c in self.erp["contacts"])
        )


def _date(value: Any) -> dt.date:
    return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))


def _late(order: dict[str, Any]) -> bool:
    revised, promised = order["revised_date"], order["promised_date"]
    return (
        order["status"] != "expediee" and revised is not None and _date(revised) > _date(promised)
    )


def load_oracle(erp_path: Path, gmao_path: Path) -> DirectionOracle:
    """Read the two fixtures as plain YAML."""
    erp = yaml.safe_load(erp_path.read_text(encoding="utf-8"))
    gmao = yaml.safe_load(gmao_path.read_text(encoding="utf-8"))
    return DirectionOracle(erp=erp, gmao=gmao)
