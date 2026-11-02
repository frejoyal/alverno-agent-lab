"""Tool registry of module 3: six typed tools, each bound to a purpose (ADR 0012).

The code chooses every tool and fills every argument; the model never does. This module
declares what may be called, with which arguments and for which purpose:

- :data:`ToolName`: 4 reads and 2 simulated internal writes. :data:`ToolKind` has no
  sending kind, and no tool name speaks of sending: nothing in the module sends.
- One args model per tool, frozen and with ``extra="forbid"``. Identifiers are checked by
  the ADR 0012 patterns and codes by static ``Literal`` types; integers are strict, so a
  string or a boolean never passes for a count.
- :data:`TOOLS` maps each name to its :class:`ToolSpec`: kind, args model, the purposes
  (``Flow``) that justify it and the highest sensitivity level (lab scale 1 to 5, module 3
  EFVP inventory) of what it returns or writes.
- :data:`FLOW_PLANS` is the fixed table of reads per purpose. The planner reads it; the
  gold-set loaders never do, so that the expected-calls metric is not a tautology
  (ADR 0015).

``canonical_json`` and ``args_sha256`` are re-exported from the connectors, which compute
the digest of each executed write themselves: approval tokens, executor records and
side-effect records therefore share one definition of « these exact arguments ».
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from alverno_lab.connectors.types import (
    CREATE_WORK_REQUEST_TOOL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    MAX_WORK_ORDER_LIMIT,
    RECORD_REPLY_TOOL,
    AssetId,
    OrderId,
    ReplyPrepared,
    SymptomCode,
    WorkRequest,
    args_sha256,
    canonical_json,
)
from alverno_lab.privacy.ops_types import Flow

__all__ = [
    "FLOW_PLANS",
    "READ_TOOLS",
    "TOOLS",
    "TOOL_KINDS",
    "TOOL_NAMES",
    "WRITE_TOOLS",
    "CreateWorkRequestArgs",
    "FailureCatalogArgs",
    "GetAssetArgs",
    "GetOrderArgs",
    "ListWorkOrdersArgs",
    "RecordReplyPreparedArgs",
    "ToolKind",
    "ToolName",
    "ToolSpec",
    "args_sha256",
    "canonical_json",
    "registry_sha256",
]

ToolName = Literal[
    "erp.get_order",
    "gmao.get_asset",
    "gmao.list_work_orders",
    "gmao.failure_catalog",
    "erp.record_reply_prepared",
    "gmao.create_work_request",
]
TOOL_NAMES: tuple[ToolName, ...] = get_args(ToolName)

# « write_internal »: a simulated write inside the company's own systems. No kind sends.
ToolKind = Literal["read", "write_internal"]
TOOL_KINDS: tuple[ToolKind, ...] = get_args(ToolKind)


# --------------------------------------------------------------------------- args models
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GetOrderArgs(_Args):
    """``erp.get_order``: one order, by its prefixed id."""

    order_id: OrderId


class GetAssetArgs(_Args):
    """``gmao.get_asset``: one piece of equipment, by its prefixed id."""

    asset_id: AssetId


class ListWorkOrdersArgs(_Args):
    """``gmao.list_work_orders``: the asset's recent history, filtered by symptom or not.

    ``symptom_code`` is required (``None`` means no filter), so that the planner always
    states whether it filters. The bounds follow the configuration plan (548 days, 8 work
    orders, at most 12).
    """

    asset_id: AssetId
    symptom_code: SymptomCode | None
    since_days: int = Field(default=DEFAULT_HISTORY_DAYS, ge=1, strict=True)
    limit: int = Field(default=DEFAULT_WORK_ORDER_LIMIT, ge=1, le=MAX_WORK_ORDER_LIMIT, strict=True)


class FailureCatalogArgs(_Args):
    """``gmao.failure_catalog``: no argument."""


class RecordReplyPreparedArgs(ReplyPrepared):
    """``erp.record_reply_prepared``: the order and the approved draft's SHA-256, no text.

    Same fields as the connector payload, so both compute the same ``args_sha256``.
    """


class CreateWorkRequestArgs(WorkRequest):
    """``gmao.create_work_request``: asset, symptom, failure code, 1 to 4 cited work orders.

    ``failure_code`` accepts ``INDETERMINE`` as well as the 16 catalogue codes. Passed to
    the connector as is: it is a :class:`~alverno_lab.connectors.types.WorkRequest`.
    """


# --------------------------------------------------------------------------- specs
@dataclasses.dataclass(frozen=True)
class ToolSpec:
    """One registered tool.

    ``purpose`` lists the flows that justify the call (ADR 0012); ``max_level`` is the
    highest sensitivity level of what the tool returns or writes, from the module 3 EFVP
    inventory.
    """

    name: ToolName
    kind: ToolKind
    args_model: type[BaseModel]
    purpose: tuple[Flow, ...]
    max_level: int


_ORDER_FLOWS: tuple[Flow, ...] = ("order_status", "customer_reply")
_MAINT_FLOWS: tuple[Flow, ...] = ("diagnosis", "work_request")

_SPECS = (
    # Order facts: level 2 (prices, costs and margins, level 3, are removed by the view).
    ToolSpec("erp.get_order", "read", GetOrderArgs, _ORDER_FLOWS, 2),
    # Name, kind and line of a machine: no personal information.
    ToolSpec("gmao.get_asset", "read", GetAssetArgs, _MAINT_FLOWS, 1),
    # Level 4 even after projection: the maintenance department is under k = 5, so a
    # history line can still be tied to one of its few members.
    ToolSpec("gmao.list_work_orders", "read", ListWorkOrdersArgs, _MAINT_FLOWS, 4),
    # A closed list of codes and labels.
    ToolSpec("gmao.failure_catalog", "read", FailureCatalogArgs, _MAINT_FLOWS, 1),
    # Writes hold identifiers, closed codes and a digest: level 2 (EFVP inventory).
    ToolSpec(RECORD_REPLY_TOOL, "write_internal", RecordReplyPreparedArgs, ("customer_reply",), 2),
    ToolSpec(
        CREATE_WORK_REQUEST_TOOL, "write_internal", CreateWorkRequestArgs, ("work_request",), 2
    ),
)

TOOLS: Mapping[ToolName, ToolSpec] = MappingProxyType({spec.name: spec for spec in _SPECS})

# The fixed plan of reads per purpose (ADR 0012 table). Writes are not planned: the
# ``apply`` node calls one, once, with an approval token (ADR 0013).
FLOW_PLANS: Mapping[Flow, tuple[ToolName, ...]] = MappingProxyType(
    {
        "order_status": ("erp.get_order",),
        "customer_reply": ("erp.get_order",),
        "diagnosis": ("gmao.get_asset", "gmao.list_work_orders", "gmao.failure_catalog"),
        "work_request": ("gmao.get_asset", "gmao.list_work_orders", "gmao.failure_catalog"),
    }
)

# Derived from the specs; a test checks it equals the connectors' ``WriteToolName``.
WRITE_TOOLS: frozenset[ToolName] = frozenset(
    name for name, spec in TOOLS.items() if spec.kind == "write_internal"
)
READ_TOOLS: frozenset[ToolName] = frozenset(
    name for name, spec in TOOLS.items() if spec.kind == "read"
)


def registry_sha256() -> str:
    """SHA-256 (hex) of the tool registry and :data:`FLOW_PLANS` (ADR 0015 « Banc d'essai »).

    The digest of the canonical JSON (sorted keys, no spaces, UTF-8 characters kept) of
    ``{"tools": [...], "flow_plans": {...}}``: one entry per tool, in :data:`TOOL_NAMES`
    order, with its name, kind, purposes, highest sensitivity level and the JSON schema
    of its args model (fields, types, patterns, bounds and docstrings); then the planned
    reads of each flow, in order. Any change to a tool, an argument rule or a plan changes
    it; the bench note records it next to the threshold and fixture digests.
    """
    payload = {
        "tools": [
            {
                "name": spec.name,
                "kind": spec.kind,
                "purpose": list(spec.purpose),
                "max_level": spec.max_level,
                "args_schema": spec.args_model.model_json_schema(),
            }
            for spec in (TOOLS[name] for name in TOOL_NAMES)
        ],
        "flow_plans": {flow: list(plan) for flow, plan in FLOW_PLANS.items()},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
