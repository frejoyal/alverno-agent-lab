"""The functions behind the MCP tools: the same ones as the command line (docs/adr/0017).

:class:`Backends` holds the Quality retriever and agent, the HR agent and, when
``mcp_ops`` is on, one module 3 rig per sub-module (tools mode: no model). Each
method translates a validated input into a call of the existing function and its result
into an output model; it adds no rule of its own. The module 3 tools build a fixed request
from typed ids, so no free text reaches the envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from alverno_lab.agents.hr.graph import HrAgent
from alverno_lab.agents.ops.probes import TimelineLog
from alverno_lab.agents.ops.runtime import OpsEnvelope, OpsTurn
from alverno_lab.agents.quality.graph import QualityAgent
from alverno_lab.config import Settings
from alverno_lab.connectors.sim import SimGmao
from alverno_lab.evals.ops_harness import OpsRig
from alverno_lab.hr_pipeline import build_hr_agent
from alverno_lab.mcp_server.schemas import (
    EXCERPT_CHARS,
    AgentAnswer,
    MachineDiagnosisInput,
    OpsAnswer,
    OrderStatusInput,
    SearchHit,
    SearchResult,
)
from alverno_lab.ops_pipeline import build_maint_rig, build_service_rig
from alverno_lab.pipeline import build_agent, build_retriever
from alverno_lab.retrieval import HybridRetriever

__all__ = ["Backends", "UnknownSymptomError", "build_backends", "order_request", "symptom_request"]


class UnknownSymptomError(ValueError):
    """A symptom code outside the closed vocabulary of the simulated CMMS."""


def order_request(order_id: str) -> str:
    """The fixed 3a request of ``order_status`` (flow ``order_status``, read only)."""
    return f"Où en est la commande {order_id} ?"


def symptom_request(asset_id: str, label: str) -> str:
    """The fixed 3b request of ``machine_diagnosis`` (flow ``diagnosis``, read only)."""
    return f"Diagnostic de l'équipement {asset_id} : {label}. Quelle est la cause probable ?"


def _ops_answer(envelope: OpsEnvelope, turn: OpsTurn) -> OpsAnswer:
    if envelope.pending:
        # A read request never holds a draft; if one ever did, nothing is approved here.
        turn = envelope.abandon()
    return OpsAnswer(outcome=turn.kind, text=turn.text or "", refusal_reason=turn.reason)


@dataclass
class Backends:
    """Validated inputs in, output models out; one instance per server process."""

    retriever: HybridRetriever
    quality: QualityAgent
    hr: HrAgent
    service: OpsRig | None = None
    maint: OpsRig | None = None

    @property
    def ops_enabled(self) -> bool:
        return self.service is not None and self.maint is not None

    def quality_search(self, query: str, k: int) -> SearchResult:
        hits = self.retriever.search(query, top_k=k)
        return SearchResult(
            hits=[
                SearchHit(
                    chunk_id=h.chunk.chunk_id,
                    doc_id=h.chunk.doc_id,
                    title=h.chunk.title,
                    version=h.chunk.version,
                    excerpt=h.chunk.text[:EXCERPT_CHARS],
                )
                for h in hits
            ]
        )

    def quality_ask(self, question: str) -> AgentAnswer:
        result = self.quality.ask(question)
        return AgentAnswer(
            answer=result.answer,
            refused=result.refused,
            refusal_reason=result.refusal_reason,
            citations=list(result.citations),
        )

    def hr_policy_ask(self, question: str) -> AgentAnswer:
        result = self.hr.ask(question, channel="mcp")
        return AgentAnswer(
            answer=result.answer,
            refused=result.refused,
            refusal_reason=result.refusal_reason,
            citations=list(result.citations),
        )

    def order_status(self, args: OrderStatusInput) -> OpsAnswer:
        assert self.service is not None
        envelope = self.service.envelope
        return _ops_answer(envelope, envelope.start(order_request(args.order_id)))

    def machine_diagnosis(self, args: MachineDiagnosisInput) -> OpsAnswer:
        assert self.maint is not None
        sim = next(s for s in self.maint.sims if isinstance(s, SimGmao))
        labels = dict(sim.symptom_labels())
        if args.symptom_code not in labels:
            raise UnknownSymptomError("symptom_code")
        envelope = self.maint.envelope
        request = symptom_request(args.asset_id, labels[args.symptom_code])
        return _ops_answer(envelope, envelope.start(request))


def build_backends(
    settings: Settings,
    *,
    fake: bool,
    model: str | None = None,
    hr_factory: Callable[[Settings], HrAgent] | None = None,
) -> Backends:
    """Build the backends of one server process; the module 3 rigs only when enabled."""
    retriever, _ = build_retriever(settings, fake=fake)
    quality = build_agent(settings, fake=fake, model=model, retriever=retriever)
    hr = (
        hr_factory(settings)
        if hr_factory is not None
        else build_hr_agent(settings, fake=fake, model=model)
    )
    backends = Backends(retriever=retriever, quality=quality, hr=hr)
    if settings.mcp_ops:
        backends.service = build_service_rig(
            settings,
            mode="tools",
            fake=fake,
            log=TimelineLog(
                settings.access_log_dir,
                prefix="service",
                retention_days=settings.access_log_retention_days,
            ),
            channel="mcp",
        )
        backends.maint = build_maint_rig(
            settings,
            mode="tools",
            fake=fake,
            log=TimelineLog(
                settings.access_log_dir,
                prefix="maint",
                retention_days=settings.access_log_retention_days,
            ),
            channel="mcp",
        )
    return backends
