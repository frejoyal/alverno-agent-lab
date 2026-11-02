"""Shared human-in-the-loop layer of module 3 (ADR 0013): state, approval, runtime, probes.

The 3a (customer service) and 3b (maintenance) graphs share this package: the
JSON-primitive state (:mod:`~alverno_lab.agents.ops.state`), the approval tail
``approve ⇄ reguard → apply → handoff`` (:mod:`~alverno_lab.agents.ops.approval`), the
value-free trace entries (:mod:`~alverno_lab.agents.ops.scrub`), the evaluation probes
(:mod:`~alverno_lab.agents.ops.probes`) and the envelope that runs a request
(:mod:`~alverno_lab.agents.ops.runtime`). No node writes the access log or mints a
token: both belong to the envelope.
"""

from __future__ import annotations

from alverno_lab.agents.ops.approval import (
    APPLY_NODE,
    APPROVE_NODE,
    HANDOFF_NODE,
    INTERRUPT_KEYS,
    MAX_EDIT_ROUNDS,
    REGUARD_NODE,
    ApprovalDecision,
    ApproveNode,
    EditCheck,
    EditChecker,
    ToolCaller,
    add_approval_nodes,
    approve_node_rounds,
    make_apply_node,
    make_approve_node,
    make_handoff_node,
    make_reguard_node,
    parse_decision,
    route_after_approve,
    route_after_reguard,
    text_bound,
)
from alverno_lab.agents.ops.probes import (
    CountingChat,
    CountingConnector,
    CountingExecutor,
    FailingLog,
    ProbeChat,
    Timeline,
    TimelineEvent,
    TimelineLog,
)
from alverno_lab.agents.ops.runtime import (
    DRAFT_NOTICE,
    DraftHeader,
    ModelUnavailableError,
    OpsEnvelope,
    OpsRuntimeError,
    OpsTurn,
    OpsTurnKind,
)
from alverno_lab.agents.ops.scrub import scrub_entry
from alverno_lab.agents.ops.state import (
    OUTCOMES,
    OpsState,
    Outcome,
    ProposedAction,
    is_json_value,
    text_sha256,
)

__all__ = [
    "APPLY_NODE",
    "APPROVE_NODE",
    "DRAFT_NOTICE",
    "HANDOFF_NODE",
    "INTERRUPT_KEYS",
    "MAX_EDIT_ROUNDS",
    "OUTCOMES",
    "REGUARD_NODE",
    "ApprovalDecision",
    "ApproveNode",
    "CountingChat",
    "CountingConnector",
    "CountingExecutor",
    "DraftHeader",
    "EditCheck",
    "EditChecker",
    "FailingLog",
    "ModelUnavailableError",
    "OpsEnvelope",
    "OpsRuntimeError",
    "OpsState",
    "OpsTurn",
    "OpsTurnKind",
    "Outcome",
    "ProbeChat",
    "ProposedAction",
    "Timeline",
    "TimelineEvent",
    "TimelineLog",
    "ToolCaller",
    "add_approval_nodes",
    "approve_node_rounds",
    "is_json_value",
    "make_apply_node",
    "make_approve_node",
    "make_handoff_node",
    "make_reguard_node",
    "parse_decision",
    "route_after_approve",
    "route_after_reguard",
    "scrub_entry",
    "text_bound",
    "text_sha256",
]
