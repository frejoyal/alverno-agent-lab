"""Diagnosis of a 3b request: schema, history lines, BM25 ranking, verify, answer text.

ADR 0012 « Graphes » (3b) and I9 spec decisions 3 to 5.

- :class:`Diagnosis` is the schema a diagnoser returns (and the full-mode model fills
  through ``complete_json``): at most 3 closed failure codes, at most 4 cited work-order
  ids, at most 4 next checks of 160 characters at most, a confidence in [0, 1]. Static
  ``Literal`` codes, no union.
- :class:`DiagnosisCase` is what a diagnoser receives: the asset view, the symptom code
  and its label, the fetched work orders (projected views, no technician field) and the
  closed catalogue. Never the request.
- :func:`history_line` renders one work order as ``wo_id | date | kind | symptom |
  failure | action | remark`` (the remark is the quarantined text).
- :func:`failure_code_scores` and :func:`rank_failure_codes` are the pure BM25 baseline
  (``rank_bm25.BM25Plus``, whose scores are never negative): one document per history
  line, the query is the symptom code and the words of its label, and a code scores the
  sum of the lines that carry it. Ties: more lines first, then the most recent line, then
  the code. Without a query, every line scores the same (a frequency ranking).
- :class:`BM25Diagnoser` (tools mode, no model): the top 3 codes, and for each the most
  recent fetched work order that carries it (4 citations at most), one next check per
  code read from the catalogue label.
- :func:`verify_diagnosis` (the ``verify`` node): cited ids that were not fetched are
  dropped; duplicates are removed, order kept; ``dropped`` counts every citation removed
  (unread ids and repeats, counted on the cited list before deduplication, ADR 0015); a
  code that no remaining cited work order carries becomes ``INDETERMINE``.
- :func:`diagnosis_text` writes the served French answer from the verified diagnosis:
  codes with their catalogue labels, cited work orders with their dates, kinds and
  codes (never their remark), next checks. It talks about the machine only.

- :func:`diagnosis_prompt` writes the user message of the full mode: the machine, the
  symptom code and its closed label, the most recent history lines (8 at most, each on
  one line) and the closed catalogue. Never the request, never the BM25 ranking, never
  a technician field.
- :class:`ChatDiagnoser` (full mode): one ``complete_json`` call with the system prompt
  of :mod:`~alverno_lab.agents.maintenance.prompts` and that user message. Its failures
  carry no value and no chain, as those of
  :class:`~alverno_lab.agents.service.drafting.ChatDrafter`: a model that cannot serve
  any diagnosis (server down, model not pulled) raises
  :class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError`
  ``diagnose:model_unavailable``, which the ``diagnose`` node lets escape to the
  envelope; an invalid output (after the chat model's one repair retry) raises
  :class:`DiagnosisError` ``diagnose:invalid_output``, a timeout ``diagnose:timeout`` and
  any other failure ``diagnose:chat_error``: the node turns them into its
  ``diagnosis_failed`` error for that item only.

A diagnoser is injected into the graph with the :class:`Diagnoser` protocol.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints
from rank_bm25 import BM25Plus

from alverno_lab.agents.maintenance.prompts import (
    ASSET_LABEL,
    CATALOG_HEADER,
    DIAGNOSE_SYSTEM_PROMPT,
    HISTORY_HEADER,
    MAX_HISTORY_LINES,
    NO_SYMPTOM,
    SYMPTOM_LABEL,
)
from alverno_lab.agents.ops.runtime import ModelUnavailableError
from alverno_lab.connectors.types import (
    INDETERMINATE_CODE,
    AssetView,
    FailureCode,
    FailureCodeView,
    SymptomCode,
    WorkOrderId,
    WorkOrderView,
)
from alverno_lab.llm import ChatModel, LLMOutputError
from alverno_lab.ollama import (
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    OllamaUnavailableError,
)
from alverno_lab.text import tokenize

__all__ = [
    "MAX_CHECKS",
    "MAX_CHECK_CHARS",
    "MAX_CITATIONS",
    "MAX_CODES",
    "BM25Diagnoser",
    "ChatDiagnoser",
    "Diagnoser",
    "Diagnosis",
    "DiagnosisCase",
    "DiagnosisError",
    "VerifiedDiagnosis",
    "catalog_line",
    "diagnosis_prompt",
    "diagnosis_text",
    "failure_code_scores",
    "history_line",
    "rank_failure_codes",
    "verify_diagnosis",
]

MAX_CODES = 3
MAX_CITATIONS = 4
MAX_CHECKS = 4
MAX_CHECK_CHARS = 160
_SEPARATOR = " | "
_NO_CARRIER = "aucun bon de travail cité ne le porte."
_NOT_READ = "bon de travail absent de l'historique lu."
_EMPTY = "-"
# Scores are rounded before ties are broken, so float noise never decides an order.
_SCORE_DIGITS = 9

NextCheck = Annotated[str, StringConstraints(min_length=1, max_length=MAX_CHECK_CHARS)]


class DiagnosisError(ValueError):
    """A diagnoser could not produce a diagnosis; the message is a code, never a value."""


class Diagnosis(BaseModel):
    """A diagnosis as a diagnoser returns it (before ``verify``)."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    codes: list[FailureCode] = Field(max_length=MAX_CODES)
    cited_wo_ids: list[WorkOrderId] = Field(max_length=MAX_CITATIONS)
    next_checks: list[NextCheck] = Field(max_length=MAX_CHECKS, repr=False)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclasses.dataclass(frozen=True)
class DiagnosisCase:
    """What a diagnoser reads: the machine, the symptom, its history and the catalogue."""

    asset: AssetView
    symptom: SymptomCode | None
    symptom_label: str | None
    work_orders: tuple[WorkOrderView, ...]
    catalog: tuple[FailureCodeView, ...]


class Diagnoser(Protocol):
    """Returns a :class:`Diagnosis` for one case; raises :class:`DiagnosisError` on failure.

    A model that cannot be reached raises
    :class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError` instead.
    """

    def diagnose(self, case: DiagnosisCase) -> Diagnosis:
        """Diagnose ``case``."""
        ...


@dataclasses.dataclass(frozen=True)
class VerifiedDiagnosis:
    """The diagnosis after ``verify``: codes, valid citations, the dropped count, checks."""

    codes: tuple[str, ...]
    cited_wo_ids: tuple[str, ...]
    dropped: int
    next_checks: tuple[str, ...] = dataclasses.field(repr=False)
    confidence: float

    def as_state(self) -> dict[str, JsonValue]:
        """JSON primitives for ``OpsState.diagnosis``."""
        return {
            "codes": list(self.codes),
            "cited_wo_ids": list(self.cited_wo_ids),
            "dropped": self.dropped,
            "next_checks": list(self.next_checks),
            "confidence": self.confidence,
        }

    @classmethod
    def from_state(cls, value: object) -> VerifiedDiagnosis | None:
        """The verified diagnosis kept in state, or None when it is missing or broken."""
        if not isinstance(value, dict):
            return None
        codes, cited = value.get("codes"), value.get("cited_wo_ids")
        checks, dropped = value.get("next_checks"), value.get("dropped")
        confidence = value.get("confidence")
        if not (
            _strings(codes)
            and _strings(cited)
            and _strings(checks)
            and type(dropped) is int
            and isinstance(confidence, int | float)
        ):
            return None
        return cls(
            codes=tuple(codes),  # type: ignore[arg-type]  # checked by _strings
            cited_wo_ids=tuple(cited),  # type: ignore[arg-type]  # checked by _strings
            dropped=dropped,
            next_checks=tuple(checks),  # type: ignore[arg-type]  # checked by _strings
            confidence=float(confidence),
        )


def _strings(value: object) -> bool:
    return type(value) is list and all(type(item) is str for item in value)


# --------------------------------------------------------------------------- lines
def history_line(wo: WorkOrderView, *, with_remark: bool = True) -> str:
    """``wo_id | date | kind | symptom | failure | action | remark`` (remark quarantined).

    ``with_remark=False`` stops after the action code: the closed fields only, which is
    what the strict guard accepts as a cited extract (a remark backs no word).
    """
    fields = [
        wo.wo_id,
        wo.opened_on.isoformat(),
        wo.kind,
        wo.symptom_code or _EMPTY,
        wo.failure_code or _EMPTY,
        wo.action_code,
    ]
    if with_remark:
        fields.append(wo.remark.text if wo.remark is not None else "")
    return _SEPARATOR.join(fields)


def catalog_line(entry: FailureCodeView) -> str:
    """``code | category | label``."""
    return _SEPARATOR.join([entry.code, entry.category, entry.label])


def _newest_first(work_orders: Sequence[WorkOrderView]) -> list[WorkOrderView]:
    return sorted(work_orders, key=lambda wo: (wo.opened_on, wo.wo_id), reverse=True)


# --------------------------------------------------------------------------- BM25
def failure_code_scores(
    symptom: SymptomCode | None,
    work_orders: Sequence[WorkOrderView],
    *,
    label: str | None = None,
) -> list[tuple[FailureCode, float]]:
    """``(code, score)`` of every failure code of the history lines, best first.

    Pure and deterministic (see the module docstring). Lines without a failure code (a
    preventive work order) carry no code.
    """
    lines = [wo for wo in work_orders if wo.failure_code is not None]
    if not lines:
        return []
    documents = [tokenize(history_line(wo)) for wo in lines]
    query = tokenize(" ".join(part for part in (symptom, label) if part))
    if query and all(documents):
        raw = BM25Plus(documents).get_scores(query)
        scores = [float(value) for value in raw]
    else:
        scores = [1.0] * len(lines)
    totals: dict[FailureCode, float] = {}
    counts: dict[FailureCode, int] = {}
    latest: dict[FailureCode, tuple[str, str]] = {}
    for wo, score in zip(lines, scores, strict=True):
        code = wo.failure_code
        if code is None or not math.isfinite(score):  # pragma: no cover - filtered above
            continue
        totals[code] = totals.get(code, 0.0) + score
        counts[code] = counts.get(code, 0) + 1
        stamp = (wo.opened_on.isoformat(), wo.wo_id)
        latest[code] = max(latest.get(code, stamp), stamp)
    rounded = {code: round(total, _SCORE_DIGITS) for code, total in totals.items()}
    # Stable sorts, last key first: code, then the most recent line, then score and count.
    order = sorted(rounded)
    order.sort(key=lambda c: latest[c], reverse=True)
    order.sort(key=lambda c: (-rounded[c], -counts[c]))
    return [(code, rounded[code]) for code in order]


def rank_failure_codes(
    symptom: SymptomCode | None,
    work_orders: Sequence[WorkOrderView],
    *,
    label: str | None = None,
) -> list[FailureCode]:
    """The failure codes of the history lines, best first (BM25 baseline, pure)."""
    return [code for code, _ in failure_code_scores(symptom, work_orders, label=label)]


class BM25Diagnoser:
    """Tools-mode diagnoser: the BM25 ranking, no model (see the module docstring)."""

    def diagnose(self, case: DiagnosisCase) -> Diagnosis:
        """The top 3 codes, their most recent carrying work order, one check per code."""
        scored = failure_code_scores(case.symptom, case.work_orders, label=case.symptom_label)
        top = scored[:MAX_CODES]
        codes = [code for code, _ in top]
        newest = _newest_first(case.work_orders)
        cited: list[str] = []
        for code in codes:
            carrier = next((wo.wo_id for wo in newest if wo.failure_code == code), None)
            if carrier is not None and carrier not in cited:
                cited.append(carrier)
        labels = {entry.code: entry.label for entry in case.catalog}
        checks = [
            f"Vérifier : {labels[code]}."[:MAX_CHECK_CHARS] for code in codes if code in labels
        ]
        total = sum(score for _, score in scored)
        confidence = round(top[0][1] / total, 2) if top and total > 0 else 0.0
        return Diagnosis(
            codes=codes,
            cited_wo_ids=cited[:MAX_CITATIONS],
            next_checks=checks[:MAX_CHECKS],
            confidence=min(max(confidence, 0.0), 1.0),
        )


# --------------------------------------------------------------------------- full mode
def _one_line(text: str) -> str:
    return " ".join(text.split())


def diagnosis_prompt(case: DiagnosisCase) -> str:
    """The user message of :class:`ChatDiagnoser` (see the module docstring)."""
    asset = case.asset
    if case.symptom is not None:
        symptom = _SEPARATOR.join(part for part in (case.symptom, case.symptom_label) if part)
    else:
        symptom = NO_SYMPTOM
    recent = _newest_first(case.work_orders)[:MAX_HISTORY_LINES]
    lines = [
        f"{ASSET_LABEL} : "
        + _one_line(_SEPARATOR.join([asset.asset_id, asset.name, asset.kind, asset.line])),
        f"{SYMPTOM_LABEL} : {_one_line(symptom)}",
        "",
        f"{HISTORY_HEADER} :",
        *(_one_line(history_line(wo)) for wo in recent),
        "",
        f"{CATALOG_HEADER} :",
        *(_one_line(catalog_line(entry)) for entry in case.catalog),
    ]
    return "\n".join(lines)


class ChatDiagnoser:
    """The full-mode diagnoser: one ``complete_json`` call on :func:`diagnosis_prompt`.

    ``chat`` is the counted, probed model of the rig.
    """

    def __init__(self, chat: ChatModel) -> None:
        self.chat = chat

    def diagnose(self, case: DiagnosisCase) -> Diagnosis:
        """The model's diagnosis; raise :class:`DiagnosisError` or
        :class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError` (module doc)."""
        rule: str | None = None
        produced: object = None
        try:
            produced = self.chat.complete_json(
                DIAGNOSE_SYSTEM_PROMPT, diagnosis_prompt(case), Diagnosis
            )
        # Never chained: the model's error may quote its output or the history.
        except LLMOutputError:
            rule = "invalid_output"
        except (OllamaUnavailableError, OllamaModelNotFoundError):
            # Server down or model not pulled: every diagnosis would fail the same way.
            rule = "model_unavailable"
        except OllamaTimeoutError:
            rule = "timeout"
        except Exception:
            rule = "chat_error"
        if rule == "model_unavailable":
            raise ModelUnavailableError("diagnose:model_unavailable")
        if rule is not None or not isinstance(produced, Diagnosis):
            raise DiagnosisError(f"diagnose:{rule or 'invalid_output'}")
        return produced


# --------------------------------------------------------------------------- verify
def verify_diagnosis(
    diagnosis: Diagnosis, work_orders: Sequence[WorkOrderView]
) -> VerifiedDiagnosis:
    """ADR 0012 ``verify`` for 3b (see the module docstring); pure."""
    fetched = {wo.wo_id: wo for wo in work_orders}
    raw = [str(wo_id) for wo_id in diagnosis.cited_wo_ids]
    valid = [wo_id for wo_id in dict.fromkeys(raw) if wo_id in fetched]
    carried = {fetched[wo_id].failure_code for wo_id in valid}
    codes = [
        code if code != INDETERMINATE_CODE and code in carried else INDETERMINATE_CODE
        for code in diagnosis.codes
    ]
    return VerifiedDiagnosis(
        codes=tuple(dict.fromkeys(codes)),
        cited_wo_ids=tuple(valid),
        # Counted before duplicates go: a repeated id is a citation verify removes too.
        dropped=len(raw) - len(valid),
        next_checks=tuple(diagnosis.next_checks),
        confidence=diagnosis.confidence,
    )


# --------------------------------------------------------------------------- answer
def diagnosis_text(
    asset: AssetView,
    symptom: SymptomCode | None,
    symptom_label: str | None,
    verified: VerifiedDiagnosis,
    work_orders: Sequence[WorkOrderView],
    catalog: Sequence[FailureCodeView],
) -> str:
    """The served French answer of a diagnosis (about the machine only)."""
    labels: Mapping[str, str] = {entry.code: entry.label for entry in catalog}
    by_id = {wo.wo_id: wo for wo in work_orders}
    lines = [f"Diagnostic de l'équipement {asset.asset_id} : {asset.name}, ligne {asset.line}."]
    if symptom is not None and symptom_label:
        lines.append(f"Symptôme : {symptom_label} ({symptom}).")
    else:
        lines.append("Symptôme : non précisé ; tout l'historique récent a été lu.")
    if verified.codes:
        lines.append("Codes de défaillance retenus, du plus probable au moins probable :")
        for rank, code in enumerate(verified.codes, start=1):
            label = labels.get(code)
            if code == INDETERMINATE_CODE or label is None:
                lines.append(f"{rank}. {INDETERMINATE_CODE} : {_NO_CARRIER}")
            else:
                lines.append(f"{rank}. {code} : {label}.")
    else:
        lines.append("Aucun code de défaillance n'est retenu.")
    if verified.cited_wo_ids:
        lines.append("Bons de travail cités :")
        for wo_id in verified.cited_wo_ids:
            wo = by_id.get(wo_id)
            if wo is None:
                # Never after ``verify``, which drops every id it did not read; written
                # all the same so the text stays total (and the evaluation can count it).
                lines.append(f"- {wo_id} : {_NOT_READ}")
                continue
            failure = wo.failure_code or "aucun"
            # Closed fields only: a remark is never served (I9 spec decision 5).
            lines.append(
                f"- {wo.wo_id} du {wo.opened_on.isoformat()} : {wo.kind}, "
                f"code {failure}, action {wo.action_code}."
            )
    else:
        lines.append("Aucun bon de travail lu n'est cité.")
    if verified.next_checks:
        lines.append("Vérifications suggérées :")
        lines.extend(f"- {check}" for check in verified.next_checks)
    lines.append(
        "Cette aide porte sur la machine seulement ; elle ne remplace pas l'inspection sur place."
    )
    return "\n".join(lines)
