"""Value-free trace entries of the module 3 graphs (ADR 0013, « État en primitives JSON »).

Every node of an ops graph appends exactly one entry to ``OpsState.trace``, built here by
:func:`scrub_entry`. An entry keeps the node name and, for a known node, only the keys of
its allowlist (:data:`NODE_KEYS`) whose value is one of:

- a boolean;
- a small non-negative integer (a count or a round, at most :data:`MAX_COUNT`: a long
  digit run never reaches a trace);
- a code matching ``^[a-z_]{1,40}$`` (a flow, a decision, a refusal or an error code);
- a registered tool name;
- a fixture id (order, asset or work order, the ADR 0014 ``entity_refs`` patterns);
- a closed failure or symptom code of the GMAO (``HYD-FLEX``, ``SYM-FUITE``: a static
  ``Literal``, never a value);
- a list of at most :data:`MAX_LIST` codes, tool names or fixture ids (flags, ids read).

Anything else is dropped without a trace of it: a text, an args dict, a token, an
interrupt payload, a digest, a contact reference or a ``None``. An unknown node keeps
no detail at all, only ``{"node": node}``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import JsonValue

from alverno_lab.connectors.types import FAILURE_CODES, SYMPTOM_CODES
from alverno_lab.tools.registry import TOOL_NAMES

__all__ = ["MAX_COUNT", "MAX_LIST", "NODE_KEYS", "UNKNOWN_NODE", "scrub_entry"]

MAX_COUNT = 9999
MAX_LIST = 20
UNKNOWN_NODE = "unknown"

_CODE = re.compile(r"[a-z_]{1,40}")
# The ADR 0014 entity patterns ([0-9], never \d, which matches non-ASCII digits).
_ID = re.compile(r"CMD-[0-9]{4}-[0-9]{4}|EQ-[A-Z]{3}-[0-9]{2}|OT-[0-9]{4}-[0-9]{4}")
_TOOLS: frozenset[str] = frozenset(TOOL_NAMES)
# The closed codes of 3b (failure codes, INDETERMINE included, and symptom codes).
_CLOSED_CODES: frozenset[str] = FAILURE_CODES | SYMPTOM_CODES

# Per-node allowlists. The approval nodes are fixed by the I5 spec; the other names are
# the nodes of the 3a and 3b graphs (module plan §2.4), which the flow increments fill.
NODE_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "route": frozenset({"flow", "refusal", "ids", "id_count", "symptom"}),
        "fetch": frozenset({"tool", "ok", "error", "refusal", "record_count", "ids"}),
        "scope_check": frozenset({"ok", "refusal"}),
        "gate": frozenset({"ok", "refusal", "record_count"}),
        "quarantine": frozenset({"flags", "flag_count"}),
        "draft": frozenset({"ok", "error", "citation_count"}),
        "diagnose": frozenset({"ok", "error", "code_count", "citation_count"}),
        "verify": frozenset({"ok", "refusal", "dropped_count", "citation_count", "codes", "ids"}),
        "compose": frozenset({"ok", "refusal"}),
        "compose_internal": frozenset({"ok", "refusal"}),
        "guard": frozenset({"ok", "refusal", "rule"}),
        "propose": frozenset({"tool", "edit_round", "citation_count", "ids"}),
        "refuse": frozenset({"refusal"}),
        "approve": frozenset({"decision", "edit_round", "valid", "reason"}),
        "reguard": frozenset({"ok", "reason", "edit_round"}),
        "apply": frozenset({"tool", "ok", "error"}),
        "handoff": frozenset({"handed_off"}),
    }
)


def _atom(value: object) -> bool:
    """Whether a string is a code, a registered tool name, a fixture id or a closed code."""
    if type(value) is not str:
        return False
    return (
        _CODE.fullmatch(value) is not None
        or value in _TOOLS
        or _ID.fullmatch(value) is not None
        or value in _CLOSED_CODES
    )


def _kept(value: object) -> JsonValue | None:
    """``value`` when it is value-free by the rules above, else ``None`` (dropped)."""
    if type(value) is bool:
        return value
    if type(value) is int:
        return value if 0 <= value <= MAX_COUNT else None
    if _atom(value):
        return str(value)
    # Exact list or tuple: a subclass could lie about its length or items.
    if isinstance(value, list | tuple) and type(value) in (list, tuple):
        items = list(value)
        if len(items) <= MAX_LIST and all(_atom(item) for item in items):
            return [str(item) for item in items]
    return None


def scrub_entry(node: str, detail: Mapping[str, object]) -> dict[str, JsonValue]:
    """One trace entry for ``node``: its name and the allowed, value-free details only."""
    if type(node) is not str or _CODE.fullmatch(node) is None:
        return {"node": UNKNOWN_NODE}
    keys = NODE_KEYS.get(node)
    entry: dict[str, JsonValue] = {"node": node}
    if keys is None:
        return entry
    for key in sorted(keys):
        if key in detail:
            value = _kept(detail[key])
            if value is not None:
                entry[key] = value
    return entry
