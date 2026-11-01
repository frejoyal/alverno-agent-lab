"""HR assistant: a Law 25 envelope around the unchanged module-1 graph (spec §4, D1).

``HrAgent.ask`` runs, in order:

1. ``screen`` (privacy layer): detection, redaction and classification of the raw
   question; a refusal stops here, with no embedding, BM25 or LLM call;
2. the module-1 graph (retrieve -> gate -> answer -> verify, or refuse), built with the HR
   prompts and the no-op inner screen :func:`allow_all`, on the redacted text only and
   under ``tracing_context(enabled=False)``;
3. ``guard`` (``check_output``): an answer that may disclose personal information is
   withheld (``output_pi``);
4. compose: the answer plus the footer, or the policy message; plus the redaction note
   when personal information was removed from the question;
5. the access log: one metadata record, written before the text is returned. A failed
   write serves ``audit_unavailable`` instead (fail closed).

The raw question lives in memory only. :class:`HrAnswer` holds neither the question nor
its redacted form nor any raw model string (dropped citations and ungrounded numbers are
counts), and its trace is scrubbed (:func:`scrub_trace`).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypeVar, cast, get_args

from langsmith import tracing_context
from pydantic import BaseModel, Field

from alverno_lab.agents.hr.prompts import FOOTER, HR_POLICY, HR_PROMPTS
from alverno_lab.agents.quality.gate import RefusalReason, gate_decision
from alverno_lab.agents.quality.graph import (
    AnswerDraft,
    QualityState,
    TraceEvent,
    build_quality_agent,
    check_tracing_environment,
    unique,
)
from alverno_lab.agents.quality.prompts import format_extract
from alverno_lab.llm import ChatModel
from alverno_lab.ollama import OllamaError
from alverno_lab.privacy import (
    REFUSAL_CODES,
    AccessLog,
    AccessLogError,
    Assessment,
    PIType,
    PrivacyContext,
    RefusalCode,
    RefusalPolicy,
    Screening,
    check_output,
    redaction_note,
    screen,
)
from alverno_lab.privacy.access_log import PI_COUNT_CAP
from alverno_lab.retrieval import GateSignal, Hit, HybridRetriever

Channel = Literal["cli", "eval"]
Decision = Literal["answered", "refused", "error"]

# Level of every HR document: the corpus check refuses any other (spec §5.7).
HR_DOC_LEVEL = 2

_RULE_ID_RE = re.compile(r"^[a-z_]+(?:\.[a-z_]+)?$")
_CHUNK_ID_RE = re.compile(r"^[A-Z0-9-]+#\d{2,}$")
_MODEL_RE = re.compile(r"^[\w.:/-]{1,64}$")
_PI_TYPES = frozenset(t.value for t in PIType)
_SIGNALS = frozenset(get_args(GateSignal))
_M = TypeVar("_M", bound=BaseModel)


class HrAnswer(BaseModel):
    """Public result of :meth:`HrAgent.ask`: the served text and metadata only.

    No question, no redacted question and no raw model string: dropped citations and
    ungrounded numbers are counts, and the trace is scrubbed.
    """

    request_id: str
    answer: str
    refused: bool
    refusal_reason: RefusalCode | None = None
    citations: list[str] = Field(default_factory=list)
    cited_doc_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    relevance: float | None = None
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    pi_types: list[PIType] = Field(default_factory=list)
    assessment: Assessment
    n_dropped_citations: int = 0
    n_ungrounded_numbers: int = 0
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    trace: list[TraceEvent] = Field(default_factory=list)


def allow_all(question: str) -> RefusalReason | None:
    """Inner gate screen of the HR graph: the question was screened before (privacy layer)."""
    del question
    return None


# A chunk id with extra ``#NN`` suffixes glued on: gemma4:e4b writes « [RH-007#05#02] »,
# its own extract id plus the suffix of the prompt example « [RH-005#02] » (bench of
# 2026-11-01, docs/adr/0011). The first id is the extract the model meant.
_GLUED_ID_RE = re.compile(r"(?<![\w.#-])([A-Za-z0-9][\w.-]*#\d{2,})(?:#\d{2,})+(?![\w#])")


def repair_glued_ids(text: str) -> str:
    """Cut the extra ``#NN`` suffixes of a chunk id; ``verify`` still checks the result."""
    return _GLUED_ID_RE.sub(r"\1", text)


class _AttemptCounter:
    """A chat model that counts calls *before* delegating them.

    ``OllamaChat`` records its stats only after a reply, so a timeout or an HTTP error
    on the chat call itself would leave ``stats.calls`` unchanged although the redacted
    question reached the model (ADR 0008, field ``model``). An answer draft also gets
    its glued chunk ids repaired (:func:`repair_glued_ids`) before ``verify``.
    """

    def __init__(self, inner: ChatModel) -> None:
        self.inner = inner
        self.stats = inner.stats
        self.attempts = 0

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def complete_json(self, system: str, user: str, schema: type[_M]) -> _M:
        self.attempts += 1
        result = self.inner.complete_json(system, user, schema)
        if isinstance(result, AnswerDraft):
            return result.model_copy(
                update={
                    "answer": repair_glued_ids(result.answer),
                    "citations": [repair_glued_ids(c) for c in result.citations],
                }
            )
        return result


def _code(reason: str | None) -> RefusalCode | None:
    """``reason`` as a refusal code; an unknown value becomes ``no_match`` (fail closed)."""
    if reason is None:
        return None
    return cast(RefusalCode, reason) if reason in REFUSAL_CODES else "no_match"


def _number(value: object) -> float | None:
    """``value`` rounded to 6 decimals (M8: no long digit run in a saved output)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 6)


def _ms(started: float) -> float:
    """Milliseconds since ``started``, rounded to 3 decimals (M8, as the trace)."""
    return round((time.perf_counter() - started) * 1000, 3)


def _count(value: object) -> int:
    return len(value) if isinstance(value, list | tuple) else 0


# Any (here and in the scrubbers below): TraceEvent.detail is dict[str, Any] (module 1);
# each function narrows the raw values with isinstance or membership checks before
# keeping them.
def _counted(d: Mapping[str, Any], key: str, raw_key: str) -> int:
    """``d[key]`` when it already is a count (a scrubbed detail), else ``len(d[raw_key])``.

    Scrubbing twice must keep the counts: the writer of the trace file scrubs again.
    """
    value = d.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return _count(d.get(raw_key))


# Detail kept per node: each function reads the raw detail and returns only typed,
# value-free fields (spec §4 table).
def _scrub_screen(d: Mapping[str, Any]) -> dict[str, Any]:
    decision = d.get("decision")
    rule_ids = d.get("rule_ids", [])
    return {
        "decision": decision if decision in ("allowed", "refused") else None,
        "rule_ids": [r for r in rule_ids if isinstance(r, str) and _RULE_ID_RE.match(r)],
        "pi_types": [t for t in d.get("pi_types", []) if t in _PI_TYPES],
        "pi_count": min(int(d.get("pi_count", 0) or 0), PI_COUNT_CAP),
    }


def _scrub_retrieve(d: Mapping[str, Any]) -> dict[str, Any]:
    ids = d.get("chunk_ids", [])
    return {"chunk_ids": [c for c in ids if isinstance(c, str) and _CHUNK_ID_RE.match(c)]}


def _scrub_gate(d: Mapping[str, Any]) -> dict[str, Any]:
    reason = d.get("reason")
    signal = d.get("signal")
    return {
        "allowed": d.get("allowed") is True,
        "reason": reason if reason in REFUSAL_CODES else None,
        "relevance": _number(d.get("relevance")),
        "signal": signal if signal in _SIGNALS else None,
    }


def _scrub_answer(d: Mapping[str, Any]) -> dict[str, Any]:
    if "error" in d:
        return {"error": True}
    model = d.get("model")
    return {
        "model": model if isinstance(model, str) and _MODEL_RE.match(model) else None,
        "n_citations": _counted(d, "n_citations", "citations"),
    }


def _scrub_verify(d: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "n_valid": _counted(d, "n_valid", "valid"),
        "n_dropped": _counted(d, "n_dropped", "dropped"),
        "n_ungrounded": _counted(d, "n_ungrounded", "ungrounded"),
    }


def _scrub_refuse(d: Mapping[str, Any]) -> dict[str, Any]:
    reason = d.get("reason")
    return {"reason": reason if reason in REFUSAL_CODES else None}


def _scrub_guard(d: Mapping[str, Any]) -> dict[str, Any]:
    rule = d.get("rule")
    return {
        "ok": d.get("ok") is True,
        "rule": rule if rule in ("detected", "placeholder", "echo", "proper_noun") else None,
    }


_SCRUBBERS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "screen": _scrub_screen,
    "retrieve": _scrub_retrieve,
    "gate": _scrub_gate,
    "answer": _scrub_answer,
    "verify": _scrub_verify,
    "refuse": _scrub_refuse,
    "guard": _scrub_guard,
}


def scrub_trace(events: Sequence[TraceEvent]) -> list[TraceEvent]:
    """Trace events with only the value-free details of each node (spec §4 table).

    Model strings (citations as written, dropped ids, ungrounded numbers, error messages)
    become counts or flags; an unknown node keeps no detail. Scrubbing is idempotent: a
    scrubbed trace scrubbed again keeps its counts.
    """
    scrubbed: list[TraceEvent] = []
    for event in events:
        scrubber = _SCRUBBERS.get(event.node)
        detail = scrubber(event.detail) if scrubber is not None else {}
        scrubbed.append(
            TraceEvent(node=event.node, duration_ms=round(event.duration_ms, 3), detail=detail)
        )
    return scrubbed


def write_hr_trace_jsonl(answer: HrAnswer, path: Path) -> None:
    """Append the scrubbed trace of ``answer`` to a JSONL file, one event per line.

    Lines hold the random request id, the refusal reason, the node, its duration and its
    scrubbed detail: never the question, a digest, a prefix or a length of it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for event in scrub_trace(answer.trace):
            record = {
                "request_id": answer.request_id,
                "refusal_reason": answer.refusal_reason,
                "node": event.node,
                "duration_ms": event.duration_ms,
                "detail": event.detail,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _screen_event(screening: Screening, duration_ms: float) -> TraceEvent:
    return TraceEvent(
        node="screen",
        duration_ms=duration_ms,
        detail={
            "decision": "refused" if screening.assessment.refusal else "allowed",
            "rule_ids": list(screening.assessment.rule_ids),
            "pi_types": [t.value for t in _types(screening)],
            "pi_count": min(len(screening.spans), PI_COUNT_CAP),
        },
    )


def _types(screening: Screening) -> list[PIType]:
    return sorted({s.type for s in screening.spans}, key=lambda t: t.value)


class HrAgent:
    """The HR assistant: screen, inner module-1 graph, output guard, access log."""

    def __init__(
        self,
        retriever: HybridRetriever,
        chat: ChatModel,
        ctx: PrivacyContext,
        *,
        access_log: AccessLog,
        min_confidence: float = 0.2,
        policy: RefusalPolicy = HR_POLICY,
        doc_levels: Mapping[str, int] | None = None,
    ) -> None:
        self.retriever = retriever
        self.chat = chat
        self.ctx = ctx
        self.access_log = access_log
        self.min_confidence = min_confidence
        self.policy = policy
        self.doc_levels = dict(doc_levels or {})
        self._counter = _AttemptCounter(chat)
        self.graph = build_quality_agent(
            retriever,
            self._counter,
            min_confidence=min_confidence,
            prompts=HR_PROMPTS,
            screen=allow_all,
        )

    # ------------------------------------------------------------------ helpers
    def _compose(self, text: str, screening: Screening) -> str:
        note = redaction_note(_types(screening)) if screening.spans else ""
        return f"{text}\n{note}" if note else text

    def _max_level(self, doc_ids: Iterable[str]) -> int | None:
        levels = [self.doc_levels.get(d, HR_DOC_LEVEL) for d in doc_ids]
        return max(levels) if levels else None

    def _log(
        self,
        *,
        channel: Channel,
        decision: Decision,
        reason: RefusalCode | None,
        screening: Screening,
        doc_ids: Sequence[str],
        model: str | None,
        started: float,
    ) -> None:
        """Append one record; raises :class:`AccessLogError` (fail closed)."""
        assessment = screening.assessment
        record = self.access_log.record(
            channel=channel,
            decision=decision,
            reason=reason,
            kind=assessment.kind,
            subject=assessment.subject,
            requested_level=assessment.requested_level,
            pi_types=tuple(_types(screening)),
            pi_count=min(len(screening.spans), PI_COUNT_CAP),
            doc_ids=tuple(doc_ids),
            max_doc_level=self._max_level(doc_ids),
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        self.access_log.append(record)

    # ------------------------------------------------------------------ public API
    def ask(self, question: str, *, channel: Channel = "cli") -> HrAnswer:
        """Answer one question from the HR policies, or refuse; always log one record.

        A LangChain v1 tracing variable raises ``LegacyTracingError`` before anything
        runs. An ``OllamaError`` escaping the graph is logged (``decision="error"``) and
        re-raised. A failed log write serves ``audit_unavailable``.
        """
        check_tracing_environment()
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        prompt_before = self.chat.stats.prompt_tokens
        completion_before = self.chat.stats.completion_tokens
        attempts_before = self._counter.attempts
        screening = screen(question, self.ctx)
        events = [_screen_event(screening, (time.perf_counter() - started) * 1000)]
        reason: RefusalCode | None = screening.assessment.refusal
        final: QualityState = {}
        if reason is None:
            initial: QualityState = {"question": screening.redacted, "trace": []}
            try:
                # Law 25: no hosted tracing, whatever the environment says (as module 1).
                with tracing_context(enabled=False):
                    final = cast(QualityState, self.graph.invoke(initial))
            except OllamaError:
                # The model is named only when the LLM was called: an embedder failure
                # during retrieval never reached it (ADR 0008, field ``model``). Attempts,
                # not stats: a failed chat call records no stats but did reach the LLM.
                reached = self._counter.attempts > attempts_before
                self._log(
                    channel=channel,
                    decision="error",
                    reason=None,
                    screening=screening,
                    doc_ids=(),
                    model=self.chat.model_name if reached else None,
                    started=started,
                )
                raise
            events.extend(final.get("trace", []))
            if final.get("refused", False):
                reason = _code(final.get("refusal_reason") or "no_match")
        hits: list[Hit] = final.get("hits", [])
        by_id = {h.chunk.chunk_id: h for h in hits}
        citations = [] if reason is not None else list(final.get("citations", []))
        model_answer = final.get("answer", "")
        if reason is None:
            guard_started = time.perf_counter()
            verdict = check_output(
                model_answer,
                redacted_question=screening.redacted,
                raw_values=screening.raw_values,
                cited_extracts=[format_extract(by_id[c]) for c in citations if c in by_id],
                ctx=self.ctx,
            )
            events.append(
                TraceEvent(
                    node="guard",
                    duration_ms=(time.perf_counter() - guard_started) * 1000,
                    detail={"ok": verdict.ok, "rule": verdict.rule},
                )
            )
            if not verdict.ok:
                reason = "output_pi"
                citations = []
        cited_doc_ids = unique(by_id[c].chunk.doc_id for c in citations if c in by_id)
        if reason is None:
            served = self._compose(f"{model_answer}\n\n{FOOTER}", screening)
        else:
            served = self._compose(self.policy.message(reason), screening)
        model = self.chat.model_name if any(e.node == "answer" for e in events) else None
        try:
            self._log(
                channel=channel,
                decision="answered" if reason is None else "refused",
                reason=reason,
                screening=screening,
                doc_ids=cited_doc_ids,
                model=model,
                started=started,
            )
        except AccessLogError:
            reason = "audit_unavailable"
            citations, cited_doc_ids = [], []
            served = self._compose(self.policy.message(reason), screening)
        refused = reason is not None
        return HrAnswer(
            request_id=request_id,
            answer=served,
            refused=refused,
            refusal_reason=reason,
            citations=citations,
            cited_doc_ids=cited_doc_ids,
            confidence=0.0 if refused else _number(final.get("confidence", 0.0)) or 0.0,
            relevance=_number(final.get("relevance")),
            retrieved_chunk_ids=[h.chunk.chunk_id for h in hits],
            retrieved_doc_ids=unique(h.chunk.doc_id for h in hits),
            pi_types=_types(screening),
            assessment=screening.assessment,
            n_dropped_citations=len(final.get("dropped_citations", [])),
            n_ungrounded_numbers=len(final.get("ungrounded_numbers", [])),
            latency_ms=_ms(started),
            prompt_tokens=self.chat.stats.prompt_tokens - prompt_before,
            completion_tokens=self.chat.stats.completion_tokens - completion_before,
            trace=scrub_trace(events),
        )

    def preview(self, question: str) -> HrAnswer:
        """Retrieval mode: screen -> retrieve -> gate on the redacted text; no LLM, no log."""
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        screening = screen(question, self.ctx)
        events = [_screen_event(screening, (time.perf_counter() - started) * 1000)]
        reason: RefusalCode | None = screening.assessment.refusal
        hits: list[Hit] = []
        relevance: float | None = None
        if reason is None:
            step = time.perf_counter()
            hits = self.retriever.search(screening.redacted)
            events.append(
                TraceEvent(
                    node="retrieve",
                    duration_ms=(time.perf_counter() - step) * 1000,
                    detail={"chunk_ids": [h.chunk.chunk_id for h in hits]},
                )
            )
            step = time.perf_counter()
            decision = gate_decision(screening.redacted, hits, self.retriever, screen=allow_all)
            relevance = decision.relevance
            events.append(
                TraceEvent(
                    node="gate",
                    duration_ms=(time.perf_counter() - step) * 1000,
                    detail={
                        "allowed": decision.allowed,
                        "reason": decision.reason,
                        "relevance": decision.relevance,
                        "signal": self.retriever.gate_signal,
                    },
                )
            )
            reason = _code(decision.reason)
        return HrAnswer(
            request_id=request_id,
            answer=self.policy.message(reason) if reason is not None else "",
            refused=reason is not None,
            refusal_reason=reason,
            relevance=_number(relevance),
            retrieved_chunk_ids=[h.chunk.chunk_id for h in hits],
            retrieved_doc_ids=unique(h.chunk.doc_id for h in hits),
            pi_types=_types(screening),
            assessment=screening.assessment,
            latency_ms=_ms(started),
            trace=scrub_trace(events),
        )


__all__ = [
    "HR_DOC_LEVEL",
    "HrAgent",
    "HrAnswer",
    "allow_all",
    "scrub_trace",
    "write_hr_trace_jsonl",
]
