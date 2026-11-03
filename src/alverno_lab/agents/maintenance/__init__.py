"""Maintenance (module 3b): deterministic routing, diagnosis and graph (ADR 0012).

- :mod:`~alverno_lab.agents.maintenance.route`: ``route_maintenance`` and
  ``symptom_of``, pure, no model;
- :mod:`~alverno_lab.agents.maintenance.diagnosis`: the ``Diagnosis`` schema, the
  history lines, the BM25 baseline (``rank_failure_codes``), the tools-mode
  ``BM25Diagnoser``, the full-mode ``ChatDiagnoser`` and its user message
  (``diagnosis_prompt``), ``verify_diagnosis`` and the answer text;
- :mod:`~alverno_lab.agents.maintenance.prompts`: the system prompt of the full mode;
- :mod:`~alverno_lab.agents.maintenance.graph`: ``build_maint_graph``, the builder an
  :class:`~alverno_lab.agents.ops.runtime.OpsEnvelope` compiles with its checkpointer;
- :mod:`~alverno_lab.agents.maintenance.notice`: the art. 8 notice of ``maint ask``.

The wiring (connectors, executor, authority, screen, policy, header) lives in
``ops_pipeline.py``.
"""

from __future__ import annotations

from alverno_lab.agents.maintenance.diagnosis import (
    MAX_CHECK_CHARS,
    MAX_CHECKS,
    MAX_CITATIONS,
    MAX_CODES,
    BM25Diagnoser,
    ChatDiagnoser,
    Diagnoser,
    Diagnosis,
    DiagnosisCase,
    DiagnosisError,
    VerifiedDiagnosis,
    catalog_line,
    diagnosis_prompt,
    diagnosis_text,
    failure_code_scores,
    history_line,
    rank_failure_codes,
    verify_diagnosis,
)
from alverno_lab.agents.maintenance.graph import (
    MAINT_NODES,
    MIN_WORK_ORDERS,
    build_maint_graph,
    guard_rule,
    make_check_edit,
    work_request_args,
    work_request_summary,
)
from alverno_lab.agents.maintenance.notice import MAINT_NOTICE, maint_notice
from alverno_lab.agents.maintenance.prompts import (
    DIAGNOSE_SYSTEM_PROMPT,
    MAINT_DATA_RULE,
    MAX_HISTORY_LINES,
)
from alverno_lab.agents.maintenance.route import (
    ASSET_ID_RE,
    SYMPTOM_SYNONYMS,
    MaintFlow,
    MaintRouteRefusal,
    MaintRouteResult,
    find_asset_ids,
    has_work_request_words,
    route_maintenance,
    symptom_of,
)

__all__ = [
    "ASSET_ID_RE",
    "DIAGNOSE_SYSTEM_PROMPT",
    "MAINT_DATA_RULE",
    "MAINT_NODES",
    "MAINT_NOTICE",
    "MAX_CHECKS",
    "MAX_CHECK_CHARS",
    "MAX_CITATIONS",
    "MAX_CODES",
    "MAX_HISTORY_LINES",
    "MIN_WORK_ORDERS",
    "SYMPTOM_SYNONYMS",
    "BM25Diagnoser",
    "ChatDiagnoser",
    "Diagnoser",
    "Diagnosis",
    "DiagnosisCase",
    "DiagnosisError",
    "MaintFlow",
    "MaintRouteRefusal",
    "MaintRouteResult",
    "VerifiedDiagnosis",
    "build_maint_graph",
    "catalog_line",
    "diagnosis_prompt",
    "diagnosis_text",
    "failure_code_scores",
    "find_asset_ids",
    "guard_rule",
    "has_work_request_words",
    "history_line",
    "maint_notice",
    "make_check_edit",
    "rank_failure_codes",
    "route_maintenance",
    "symptom_of",
    "verify_diagnosis",
    "work_request_args",
    "work_request_summary",
]
