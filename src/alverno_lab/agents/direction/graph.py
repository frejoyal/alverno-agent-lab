"""The management orchestrator: ``screen → route → sections → verify → assemble`` (ADR 0018).

The code screens and routes; each section is produced by its own module, under its own
envelope: D1 by the Quality agent, D2 by the HR agent (and its access log), D3 and D4 by
the closed overview projections of the simulated connectors. Sections are never fused: a
model, in full mode, writes one summary paragraph per overview section, from that
section's facts block only, and ``verify`` keeps a sentence only when each of its numbers
and ids is in the block. The request ends with one line in the ``direction`` access log.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import BaseModel

from alverno_lab.agents.direction.overview import maintenance_block, orders_block
from alverno_lab.agents.direction.prompts import SUMMARY_SYSTEM, summary_user
from alverno_lab.agents.direction.route import Period, RoutePlan, route, screen
from alverno_lab.connectors.types import MaintenanceOverview, OrdersOverview
from alverno_lab.llm import ChatModel
from alverno_lab.privacy.access_log import AccessLog
from alverno_lab.privacy.direction_access_log import (
    DirectionAccessLogRecord,
    DirectionPurpose,
    DirectionRefusalCode,
    SectionOutcome,
)
from alverno_lab.privacy.types import Channel

__all__ = [
    "SECTION_TITLES",
    "DirectionAgent",
    "DirectionAnswer",
    "OverviewSource",
    "Section",
    "Summary",
    "verify_summary",
]

SECTION_TITLES: dict[DirectionPurpose, str] = {
    "quality_policy": "Qualité",
    "hr_policy": "Ressources humaines",
    "orders_overview": "Commandes",
    "maintenance_overview": "Entretien",
}
REFUSAL_TEXT = (
    "Cette demande n'est pas traitée par l'outil de la direction : il ne relie aucun domaine "
    "au sujet d'une personne, ne classe aucune personne ni aucun petit groupe, ne donne ni "
    "prix, ni marge, ni renseignement de santé ou de discipline. Pour une question sur la "
    "protection des renseignements personnels : confidentialite@alverno.example."
)


class Summary(BaseModel):
    """The model's summary paragraph of one overview section."""

    summary: str


class _Answer(Protocol):
    """What the Quality and HR agents both return."""

    answer: str
    refused: bool
    citations: list[str]


class OverviewSource(Protocol):
    """The two closed overview reads of the simulated ERP and CMMS."""

    def orders_overview(self, account_id: str | None) -> OrdersOverview: ...

    def maintenance_overview(
        self, asset_id: str | None, start: dt.date, end: dt.date
    ) -> MaintenanceOverview: ...

    @property
    def as_of(self) -> dt.date: ...


@dataclass
class Section:
    """One section of the answer; ``block`` is the facts block of an overview section."""

    purpose: DirectionPurpose
    outcome: SectionOutcome
    text: str = ""
    reason: str | None = None
    citations: list[str] = field(default_factory=list)
    dropped_citations: int = 0
    block: str = ""
    summary: str = ""
    dropped_sentences: int = 0


class DirectionAnswer(BaseModel):
    """Public result of :meth:`DirectionAgent.ask`: served text, sections and codes only."""

    answer: str
    refused: bool
    refusal_reason: DirectionRefusalCode | None = None
    purposes: list[DirectionPurpose] = []
    scope_account: str | None = None
    scope_asset: str | None = None
    scope_start: str | None = None
    scope_end: str | None = None
    sections: list[dict[str, object]] = []
    latency_ms: float = 0.0


class _State(TypedDict, total=False):
    question: str
    refusal: DirectionRefusalCode | None
    plan: RoutePlan | None
    sections: list[Section]


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_ID = re.compile(
    r"\b(?:CMD-\d{4}-\d{4}|CLI-\d{3}|EQ-[A-Z]{3}-\d{2}|OT-\d{4}-\d{4}|[A-Z]{3}-[A-Z]{3,})\b"
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def verify_summary(summary: str, block: str) -> tuple[str, int]:
    """Keep the sentences whose numbers and ids all appear in ``block``; count the others."""
    allowed_numbers = set(_NUMBER.findall(block))
    allowed_ids = set(_ID.findall(block))
    kept: list[str] = []
    dropped = 0
    for sentence in (s.strip() for s in _SENTENCE.split(summary)):
        if not sentence:
            continue
        numbers_ok = all(n in allowed_numbers for n in _NUMBER.findall(sentence))
        ids_ok = all(i in allowed_ids for i in _ID.findall(sentence))
        if numbers_ok and ids_ok:
            kept.append(sentence)
        else:
            dropped += 1
    return " ".join(kept), dropped


@dataclass
class DirectionAgent:
    """One agent per process; :meth:`ask` runs one request through the graph."""

    quality: Callable[[str], _Answer]
    hr: Callable[[str], _Answer]
    erp: OverviewSource
    gmao: OverviewSource
    log: AccessLog[DirectionAccessLogRecord]
    channel: Channel = "cli"
    summarizer: ChatModel | None = None
    verify: bool = True
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        graph = StateGraph(_State)
        graph.add_node("screen", self._screen)
        graph.add_node("route", self._route)
        graph.add_node("sections", self._sections)
        graph.add_edge(START, "screen")
        graph.add_conditional_edges("screen", lambda s: END if s.get("refusal") else "route")
        graph.add_conditional_edges("route", lambda s: END if s.get("refusal") else "sections")
        graph.add_edge("sections", END)
        self._graph = graph.compile()

    # ------------------------------------------------------------------ nodes
    def _screen(self, state: _State) -> _State:
        return {"refusal": screen(state["question"])}

    def _route(self, state: _State) -> _State:
        plan = route(state["question"])
        return {"plan": plan, "refusal": None if plan is not None else "out_of_scope"}

    def _sections(self, state: _State) -> _State:
        plan = state["plan"]
        assert plan is not None
        return {"sections": [self._section(p, plan) for p in plan.purposes]}

    def _section(self, purpose: DirectionPurpose, plan: RoutePlan) -> Section:
        try:
            if purpose in ("quality_policy", "hr_policy"):
                return self._policy(purpose, plan)
            return self._overview(purpose, plan)
        except Exception:
            return Section(purpose, "error", text="Section indisponible.")

    def _policy(self, purpose: DirectionPurpose, plan: RoutePlan) -> Section:
        del plan
        agent = self.quality if purpose == "quality_policy" else self.hr
        result = agent(self._question)
        dropped = getattr(result, "dropped_citations", None)
        n_dropped = (
            len(dropped) if isinstance(dropped, list) else getattr(result, "n_dropped_citations", 0)
        )
        return Section(
            purpose,
            "refused" if result.refused else "served",
            text=result.answer,
            reason=getattr(result, "refusal_reason", None),
            citations=list(result.citations),
            dropped_citations=int(n_dropped),
        )

    def _overview(self, purpose: DirectionPurpose, plan: RoutePlan) -> Section:
        if purpose == "orders_overview":
            block = orders_block(self.erp.orders_overview(plan.scope.account_id))
        else:
            period = plan.scope.period or default_period(self.gmao.as_of)
            view = self.gmao.maintenance_overview(plan.scope.asset_id, period.start, period.end)
            block = maintenance_block(view)
        summary, dropped = "", 0
        if self.summarizer is not None:
            raw = self.summarizer.complete_json(
                SUMMARY_SYSTEM, summary_user(block), Summary
            ).summary
            summary, dropped = verify_summary(raw, block) if self.verify else (raw.strip(), 0)
        text = f"{summary}\n\n{block}" if summary else block
        return Section(
            purpose, "served", text=text, block=block, summary=summary, dropped_sentences=dropped
        )

    # ------------------------------------------------------------------ public
    def ask(self, question: str) -> DirectionAnswer:
        """Answer one question; the access log line is written before the answer returns."""
        start = self.clock()
        self._question = question
        decision: Literal["answered", "refused", "error"] = "error"
        state: _State = {}
        try:
            with tracing_context(enabled=False):
                state = cast("_State", self._graph.invoke({"question": question}))
            decision = "refused" if state.get("refusal") else "answered"
        finally:
            self._question = ""
            plan = state.get("plan")
            sections = state.get("sections", [])
            self.log.append(
                self.log.record(
                    channel=self.channel,
                    decision=decision,
                    reason=state.get("refusal"),
                    purposes=plan.purposes if plan is not None and decision == "answered" else (),
                    sections=tuple(s.outcome for s in sections) if decision == "answered" else (),
                    model=self.summarizer.model_name if self.summarizer is not None else None,
                    latency_ms=int((self.clock() - start) * 1000),
                )
            )
        refusal = state.get("refusal")
        latency = (self.clock() - start) * 1000
        if refusal is not None:
            return DirectionAnswer(
                answer=REFUSAL_TEXT, refused=True, refusal_reason=refusal, latency_ms=latency
            )
        assert plan is not None
        return DirectionAnswer(
            answer=assemble(sections),
            refused=False,
            purposes=list(plan.purposes),
            scope_account=plan.scope.account_id,
            scope_asset=plan.scope.asset_id,
            scope_start=plan.scope.period.start.isoformat() if plan.scope.period else None,
            scope_end=plan.scope.period.end.isoformat() if plan.scope.period else None,
            sections=[_section_dump(s) for s in sections],
            latency_ms=latency,
        )


def default_period(as_of: dt.date) -> Period:
    """The 12 months that end at the CMMS ``as_of`` date (ADR 0018, I2 precisions)."""
    return Period(as_of.replace(year=as_of.year - 1) + dt.timedelta(days=1), as_of)


def assemble(sections: list[Section]) -> str:
    """The sections, in the fixed order, each under its title; no text joins two of them."""
    parts = []
    for section in sections:
        title = SECTION_TITLES[section.purpose]
        body = section.text if section.text else "Aucune réponse."
        parts.append(f"## {title}\n\n{body}")
    return "\n\n".join(parts)


def _section_dump(section: Section) -> dict[str, object]:
    return {
        "purpose": section.purpose,
        "outcome": section.outcome,
        "reason": section.reason,
        "text": section.text,
        "citations": section.citations,
        "dropped_citations": section.dropped_citations,
        "block": section.block,
        "summary": section.summary,
        "dropped_sentences": section.dropped_sentences,
    }
