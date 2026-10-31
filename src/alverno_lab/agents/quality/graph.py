"""LangGraph Quality agent: retrieve -> gate -> answer -> verify, with a refuse branch."""

from __future__ import annotations

import hashlib
import json
import operator
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langsmith import tracing_context
from pydantic import BaseModel, Field

from alverno_lab.agents.quality.gate import RefusalReason, gate_decision
from alverno_lab.agents.quality.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
    format_extract,
    refusal_message,
)
from alverno_lab.llm import ChatModel, LLMOutputError
from alverno_lab.retrieval import Hit, HybridRetriever

# Any bracket group; only groups that hold a chunk-id-like token are citations, so
# grouped citations such as "[QP-006#09, QP-999#01]" are validated id by id.
_BRACKET_GROUP_RE = re.compile(r"\[([^\[\]]+)\]")
_CITATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9][\w.-]*#\d{2,}$")
_CITATION_SPLIT_RE = re.compile(r"[,;/\s]+")
# A chunk id anywhere in the text, with or without brackets.
_CHUNK_ID = r"(?<![\w.#-])[A-Za-z0-9][\w.-]*#\d{2,}(?![\w#])"
_CHUNK_ID_ANY_RE = re.compile(_CHUNK_ID)
# Spaces around a citation; the French non-breaking spaces count.
_SPACE = r"[ \t\u00a0\u202f]"
# Ids written without brackets, alone or as a list: "IT-021#02", "(IT-021#02, QP-004#03)".
_BARE_CITATIONS_RE = re.compile(
    rf"{_CHUNK_ID}(?:(?:{_SPACE}*[,;/]{_SPACE}*|{_SPACE}+et{_SPACE}+){_CHUNK_ID})*"
)
# A removed citation is first replaced by this mark. The mark then goes with the wrapper
# it empties (brackets, Markdown bold, italics or code), with the spaces before it and,
# before a punctuation mark, a closing bracket or a line end, with the list separator
# before it: the removal adds no "()", "[]", "****", " ." or ",.".
_REMOVED = "\x00"
_EMPTIED_GROUP_RE = re.compile(
    rf"\({_SPACE}*\x00(?:{_SPACE}|\x00)*\)|\[{_SPACE}*\x00(?:{_SPACE}|\x00)*\]|(\*\*|\*|`)\x00+\1"
)
# A match starts at the first space of a run (lookbehind): linear time on long runs.
_REMOVED_RE = re.compile(
    rf"(?<!{_SPACE})(?:{_SPACE}*(?:[,;/]{_SPACE}*)?\x00+"
    rf"(?={_SPACE}*(?:[.,;:!?)\]*`\u00bb\"\r\n]|$))|{_SPACE}*\x00+(?=\s))|\x00+"
)
# ISO dates count as one value, so "2026-02-02" is not read as "2026" and "02".
_NUMBER_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d+(?:[.,]\d+)?")
_DOC_ID_RE = re.compile(r"\b[A-Z]{2,6}-[A-Z0-9-]*\d+\b")


class TraceEvent(BaseModel):
    """One node execution, exportable as a JSONL line."""

    node: str
    duration_ms: float
    # Per-node details are small heterogeneous JSON scalars or lists.
    detail: dict[str, Any] = Field(default_factory=dict)


class AnswerDraft(BaseModel):
    """JSON schema the LLM must follow."""

    answer: str
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class QualityState(TypedDict, total=False):
    """Graph state."""

    question: str
    hits: list[Hit]
    decision: Literal["answer", "refuse"]
    refusal_reason: RefusalReason | None
    relevance: float | None
    answer: str
    citations: list[str]
    dropped_citations: list[str]
    ungrounded_numbers: list[str]
    confidence: float
    refused: bool
    trace: Annotated[list[TraceEvent], operator.add]


class QualityAnswer(BaseModel):
    """Public result of :meth:`QualityAgent.ask`."""

    question: str
    answer: str
    citations: list[str]
    cited_doc_ids: list[str]
    dropped_citations: list[str] = Field(default_factory=list)
    ungrounded_numbers: list[str] = Field(default_factory=list)
    confidence: float
    refused: bool
    refusal_reason: RefusalReason | None = None
    relevance: float | None = None
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    retrieved_doc_ids: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    trace: list[TraceEvent] = Field(default_factory=list)


# A node returns a partial QualityState plus an optional "_detail" dict for its trace
# event; the value type depends on the key, hence Any.
NodeUpdate = dict[str, Any]


class Node(Protocol):
    """A graph node: takes the state, returns a partial update."""

    def __call__(self, state: QualityState) -> NodeUpdate:
        """Run the node."""
        ...


def _traced(name: str, fn: Node) -> Node:
    """Wrap a node so that it appends a :class:`TraceEvent` with its duration."""

    def wrapper(state: QualityState) -> NodeUpdate:
        started = time.perf_counter()
        update = fn(state)
        detail = update.pop("_detail", {})
        event = TraceEvent(
            node=name, duration_ms=(time.perf_counter() - started) * 1000, detail=detail
        )
        update["trace"] = [event]
        return update

    return wrapper


def unique(items: Iterable[str]) -> list[str]:
    """Deduplicate while keeping order."""
    return list(dict.fromkeys(items))


def normalize_citation(citation: str) -> str:
    """Accept ``[QP-007#02]`` as well as ``QP-007#02`` from the model."""
    return citation.strip().strip("[]").strip()


def _citation_tokens(group: str) -> list[str]:
    """Chunk-id-like tokens of one bracket group (empty when it is not a citation)."""
    return [t for t in _CITATION_SPLIT_RE.split(group.strip()) if _CITATION_TOKEN_RE.match(t)]


def rewrite_citations(text: str, allowed: set[str]) -> str:
    """Keep only allowed chunk ids, between brackets or not; drop what is left empty."""

    def in_brackets(match: re.Match[str]) -> str:
        tokens = _citation_tokens(match.group(1))
        if not tokens:
            return match.group(0)
        kept = [t for t in tokens if t in allowed]
        return f"[{', '.join(kept)}]" if kept else _REMOVED

    def bare(match: re.Match[str]) -> str:
        ids = _CHUNK_ID_ANY_RE.findall(match.group(0))
        kept = [c for c in ids if c in allowed]
        if len(kept) == len(ids):
            return match.group(0)
        return ", ".join(kept) if kept else _REMOVED

    text = _BARE_CITATIONS_RE.sub(bare, _BRACKET_GROUP_RE.sub(in_brackets, text))
    while (emptied := _EMPTIED_GROUP_RE.sub(_REMOVED, text)) != text:
        text = emptied
    return _REMOVED_RE.sub("", text)


def _strip_citations(text: str) -> str:
    return _BRACKET_GROUP_RE.sub(
        lambda m: " " if _citation_tokens(m.group(1)) else m.group(0), text
    )


def find_ungrounded_numbers(answer: str, sources: Iterable[str]) -> list[str]:
    """Numbers in ``answer`` that appear in none of the cited source texts."""

    def strip_ids(text: str) -> str:
        return _DOC_ID_RE.sub(" ", _CHUNK_ID_ANY_RE.sub(" ", _strip_citations(text)))

    cleaned = strip_ids(answer)
    source_numbers = {
        n.replace(",", ".") for text in sources for n in _NUMBER_RE.findall(strip_ids(text))
    }
    found = unique(n.replace(",", ".") for n in _NUMBER_RE.findall(cleaned))
    return [n for n in found if n not in source_numbers]


def build_quality_agent(
    retriever: HybridRetriever,
    chat: ChatModel,
    *,
    min_confidence: float = 0.2,
) -> CompiledStateGraph[QualityState, None, QualityState, QualityState]:
    """Compile the Quality agent graph.

    :meth:`QualityAgent.ask` runs it with LangSmith tracing off; a caller that invokes
    the compiled graph directly should do the same (``langsmith.tracing_context``).
    """

    def retrieve(state: QualityState) -> NodeUpdate:
        hits = retriever.search(state["question"])
        return {
            "hits": hits,
            "_detail": {"chunk_ids": [h.chunk.chunk_id for h in hits]},
        }

    def gate(state: QualityState) -> NodeUpdate:
        decision = gate_decision(state["question"], state.get("hits", []), retriever)
        return {
            "decision": "answer" if decision.allowed else "refuse",
            "refusal_reason": decision.reason,
            "relevance": decision.relevance,
            "_detail": {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "relevance": decision.relevance,
                "signal": retriever.gate_signal,
            },
        }

    def answer(state: QualityState) -> NodeUpdate:
        hits = state.get("hits", [])
        try:
            draft = chat.complete_json(
                SYSTEM_PROMPT, build_user_prompt(state["question"], hits), AnswerDraft
            )
        except LLMOutputError as exc:
            return {
                "answer": "",
                "citations": [],
                "confidence": 0.0,
                "refusal_reason": "llm_error",
                "_detail": {"error": str(exc)},
            }
        return {
            "answer": draft.answer.strip(),
            "citations": draft.citations,
            "confidence": draft.confidence,
            "_detail": {"model": chat.model_name, "citations": draft.citations},
        }

    def verify(state: QualityState) -> NodeUpdate:
        hits = state.get("hits", [])
        allowed = {h.chunk.chunk_id: h for h in hits}
        text = state.get("answer", "")
        listed = [normalize_citation(c) for c in state.get("citations", [])]
        proposed = unique([c for c in listed if c] + _CHUNK_ID_ANY_RE.findall(text))
        valid = [c for c in proposed if c in allowed]
        dropped = [c for c in proposed if c not in allowed]
        text = rewrite_citations(text, set(allowed))
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        confidence = state.get("confidence", 0.0)
        # Sources are the extracts exactly as the model saw them (version, date, section).
        extracts = [format_extract(allowed[c]) for c in valid]
        ungrounded = find_ungrounded_numbers(text, extracts)
        # A number repeated from the question ("pour une cote de 75 mm") is context, but only
        # when the answer also gives a value found in the extracts: "Oui, 3 mm" to "La
        # tolérance est-elle de 3 mm ?" stays ungrounded.
        if ungrounded and len(ungrounded) < len(find_ungrounded_numbers(text, [])):
            ungrounded = find_ungrounded_numbers(text, [state["question"], *extracts])
        if ungrounded:
            confidence = round(confidence * 0.5, 3)
        reason: RefusalReason | None = state.get("refusal_reason")
        if reason is None and (not valid or not text):
            reason = "no_valid_citation"
        # Checked after the ungrounded-number penalty: an invented value halves the
        # confidence, so it is refused only when the halved value is below min_confidence.
        if reason is None and confidence < min_confidence:
            reason = "low_confidence"
        return {
            "answer": text,
            "citations": valid,
            "dropped_citations": dropped,
            "ungrounded_numbers": ungrounded,
            "confidence": confidence,
            "refusal_reason": reason,
            "decision": "refuse" if reason else "answer",
            "refused": reason is not None,
            "_detail": {"valid": valid, "dropped": dropped, "ungrounded": ungrounded},
        }

    def refuse(state: QualityState) -> NodeUpdate:
        reason = state.get("refusal_reason") or "no_match"
        return {
            "answer": refusal_message(reason),
            "citations": [],
            "confidence": 0.0,
            "refused": True,
            "refusal_reason": reason,
            "_detail": {"reason": reason},
        }

    def route_gate(state: QualityState) -> Literal["answer", "refuse"]:
        return state.get("decision", "refuse")

    def route_verify(state: QualityState) -> Literal["refuse", "__end__"]:
        return "refuse" if state.get("refused") else "__end__"

    graph = StateGraph(QualityState)
    graph.add_node("retrieve", _traced("retrieve", retrieve))
    graph.add_node("gate", _traced("gate", gate))
    graph.add_node("answer", _traced("answer", answer))
    graph.add_node("verify", _traced("verify", verify))
    graph.add_node("refuse", _traced("refuse", refuse))
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "gate")
    graph.add_conditional_edges("gate", route_gate, {"answer": "answer", "refuse": "refuse"})
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", route_verify, {"refuse": "refuse", END: END})
    graph.add_edge("refuse", END)
    return graph.compile()


# LangChain stops a graph run with a bare RuntimeError when one of these v1 tracing
# variables is set while v2 tracing is off, which QualityAgent.ask always makes it;
# like LangChain, an empty, "0", "false" or "False" value counts as unset.
_V1_TRACING_VARS = ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER")
_V1_TRACING_OFF = frozenset({"", "0", "false", "False"})


class LegacyTracingError(RuntimeError):
    """A LangChain v1 tracing variable is set; LangChain would refuse to run the graph."""


class QualityAgent:
    """Convenience wrapper: compiled graph plus :meth:`ask` returning a typed answer."""

    def __init__(
        self, retriever: HybridRetriever, chat: ChatModel, *, min_confidence: float = 0.2
    ) -> None:
        self.retriever = retriever
        self.chat = chat
        self.graph = build_quality_agent(retriever, chat, min_confidence=min_confidence)

    def ask(self, question: str) -> QualityAnswer:
        """Run the graph on one question, with LangSmith tracing off (Law 25).

        Ollama transport errors (server down, timeout) propagate as ``OllamaError``; a
        LangChain v1 tracing variable in the environment raises :class:`LegacyTracingError`.
        """
        legacy = [v for v in _V1_TRACING_VARS if os.environ.get(v, "") not in _V1_TRACING_OFF]
        if legacy:
            raise LegacyTracingError(
                f"unset {', '.join(legacy)}: LangChain v1 tracing is not supported and "
                "hosted tracing stays off (Law 25)"
            )
        prompt_before = self.chat.stats.prompt_tokens
        completion_before = self.chat.stats.completion_tokens
        started = time.perf_counter()
        initial: QualityState = {"question": question, "trace": []}
        # Law 25: LangGraph sends each run (question, extracts, answer) to the hosted
        # LangSmith API when LANGSMITH_TRACING, LANGSMITH_TRACING_V2, LANGCHAIN_TRACING_V2
        # or LANGCHAIN_TRACING is "true" in the environment; tracing stays off here
        # whatever the environment says.
        with tracing_context(enabled=False):
            final = cast(QualityState, self.graph.invoke(initial))
        latency = (time.perf_counter() - started) * 1000
        hits = final.get("hits", [])
        citations = final.get("citations", [])
        doc_of = {h.chunk.chunk_id: h.chunk.doc_id for h in hits}
        return QualityAnswer(
            question=question,
            answer=final.get("answer", ""),
            citations=citations,
            cited_doc_ids=unique(doc_of[c] for c in citations if c in doc_of),
            dropped_citations=final.get("dropped_citations", []),
            ungrounded_numbers=final.get("ungrounded_numbers", []),
            confidence=final.get("confidence", 0.0),
            refused=final.get("refused", False),
            refusal_reason=final.get("refusal_reason"),
            relevance=final.get("relevance"),
            retrieved_chunk_ids=[h.chunk.chunk_id for h in hits],
            retrieved_doc_ids=unique(h.chunk.doc_id for h in hits),
            latency_ms=latency,
            prompt_tokens=self.chat.stats.prompt_tokens - prompt_before,
            completion_tokens=self.chat.stats.completion_tokens - completion_before,
            trace=final.get("trace", []),
        )


def write_trace_jsonl(
    events: Iterable[TraceEvent],
    path: Path,
    *,
    question: str = "",
    refusal_reason: str | None = None,
) -> None:
    """Append trace events to a JSONL file (one event per line).

    The question text is never written (Law 25: it may hold personal information);
    a short SHA-256 prefix and its length let runs be correlated without storing it.
    """
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            record = {
                "question_sha256": digest,
                "question_chars": len(question),
                "refusal_reason": refusal_reason,
                **event.model_dump(),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


__all__ = [
    "AnswerDraft",
    "LegacyTracingError",
    "QualityAgent",
    "QualityAnswer",
    "QualityState",
    "TraceEvent",
    "build_quality_agent",
    "write_trace_jsonl",
]
