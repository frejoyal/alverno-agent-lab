"""Shared helpers of the HR agent tests: probes, scripted chat models, agent factory.

Every value is synthetic (data/eval/synthetic_identities.yaml): phones in 555-0100 to
555-0199, common given names and surnames, the published example SIN, ALV matricules and
``.example`` addresses.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from alverno_lab.agents.hr.graph import HrAgent, HrAnswer
from alverno_lab.config import Settings
from alverno_lab.embeddings import Embedder, FloatMatrix
from alverno_lab.hr_pipeline import build_hr_retriever
from alverno_lab.index import BM25Index
from alverno_lab.llm import FakeChat, Responder, echo_first_extract
from alverno_lab.privacy import AccessLog, Canary, PIType, PrivacyContext, canary_in
from alverno_lab.retrieval import HybridRetriever

from .conftest import hr_repo_settings

PHONE = Canary(type=PIType.TELEPHONE, value="819-555-0142")
NAME = Canary(type=PIType.NOM, value="Sylvie Gagnon")
NAS = Canary(type=PIType.NAS, value="046 454 286")
MAIL = Canary(type=PIType.COURRIEL, value="cafe.matinal@courriel.example")
MATRICULE = Canary(type=PIType.MATRICULE, value="ALV-48213")


@dataclass(frozen=True)
class CanaryItem:
    """An answerable question that carries synthetic personal information."""

    question: str
    canaries: tuple[Canary, ...]


# Answerable policy questions in which the asker typed personal information (own data).
CANARY_ITEMS: tuple[CanaryItem, ...] = (
    CanaryItem(
        "Mon numéro de téléphone est le 819-555-0142, comment le changer dans le portail ?",
        (PHONE,),
    ),
    CanaryItem("Je m'appelle Sylvie Gagnon, comment demander mes vacances ?", (NAME,)),
    CanaryItem("Mon NAS 046 454 286 doit-il figurer sur le formulaire de dépôt direct ?", (NAS,)),
    CanaryItem(
        "Mon courriel cafe.matinal@courriel.example est-il le bon pour recevoir mon talon "
        "de paie ?",
        (MAIL,),
    ),
)


class ProbeEmbedder:
    """Embedder wrapper that records every text it embeds."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.texts: list[str] = []

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def embed(self, texts: list[str]) -> FloatMatrix:
        self.texts.extend(texts)
        return self.inner.embed(texts)


class ProbeBM25:
    """BM25 wrapper that records every query."""

    def __init__(self, inner: BM25Index) -> None:
        self.inner = inner
        self.queries: list[str] = []

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        self.queries.append(query)
        return self.inner.search(query, top_k)


@dataclass
class Probes:
    """What reached each backend during a run."""

    embedder: ProbeEmbedder
    bm25: ProbeBM25
    chat: FakeChat
    log_dir: Path
    extra: list[str] = field(default_factory=list)

    def backend_inputs(self) -> list[str]:
        """Every string the embedder, BM25 and the LLM received."""
        prompts = [part for call in self.chat.calls for part in call]
        return [*self.embedder.texts, *self.bm25.queries, *prompts]

    def backend_hit(self, canaries: Sequence[Canary]) -> bool:
        return any(canary_in(c, text) for c in canaries for text in self.backend_inputs())


def hr_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return hr_repo_settings(access_log_dir=tmp_path / "access", **overrides)


def probed_retriever(settings: Settings) -> tuple[HybridRetriever, ProbeEmbedder, ProbeBM25]:
    """A fake-mode HR retriever whose embedder and BM25 index record their inputs."""
    retriever, _ = build_hr_retriever(settings, fake=True)
    embedder = ProbeEmbedder(retriever.embedder)
    bm25 = ProbeBM25(retriever.bm25_index)
    retriever.embedder = embedder
    retriever.bm25_index = bm25  # type: ignore[assignment]  # duck-typed probe
    return retriever, embedder, bm25


def make_agent(
    tmp_path: Path,
    ctx: PrivacyContext,
    chat: FakeChat | None = None,
    *,
    access_log: AccessLog | None = None,
) -> tuple[HrAgent, Probes]:
    """An HR agent over the repository HR corpus, with probes on every backend."""
    settings = hr_settings(tmp_path)
    retriever, embedder, bm25 = probed_retriever(settings)
    chat = chat or FakeChat()
    log = access_log or AccessLog(settings.access_log_dir)
    agent = HrAgent(retriever, chat, ctx, access_log=log)
    return agent, Probes(embedder, bm25, chat, settings.access_log_dir)


def log_text(log_dir: Path) -> str:
    """Every access log line written under ``log_dir``."""
    if not log_dir.is_dir():
        return ""
    return "".join(p.read_text(encoding="utf-8") for p in sorted(log_dir.glob("*.jsonl")))


def leaks(answer: HrAnswer, canaries: Sequence[Canary]) -> bool:
    return any(canary_in(c, answer.answer) for c in canaries)


# --------------------------------------------------------------------------- chat models
_FIRST_ID_RE = re.compile(r"^\[([A-Za-z0-9][\w.-]*#\d{2,})\]", re.MULTILINE)
_QUESTION_RE = re.compile(r"^Question : (.*)$", re.MULTILINE)


def _first_chunk_id(user: str) -> str:
    match = _FIRST_ID_RE.search(user, max(user.rfind("\nExtraits de la documentation"), 0))
    assert match is not None, "no extract in the prompt"
    return match.group(1)


def echo_prompt(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Repeat the whole prompt back (system and user), citing the first extract."""
    del schema
    cid = _first_chunk_id(user)
    return {"answer": f"{system}\n{user} [{cid}]", "citations": [cid], "confidence": 0.9}


def echo_question(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Repeat the question line of the prompt, citing the first extract."""
    del system, schema
    cid = _first_chunk_id(user)
    match = _QUESTION_RE.search(user)
    question = match.group(1) if match else ""
    return {"answer": f"Vous demandez : {question} [{cid}]", "citations": [cid], "confidence": 0.9}


def leaky(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Invent a person and a phone number, with a valid citation (spec §7 LeakyChat)."""
    del system, schema
    cid = _first_chunk_id(user)
    return {
        "answer": f"Appelez Mme Gagnon au (819) 555-0142 [{cid}].",
        "citations": [cid],
        "confidence": 0.9,
    }


def scripted(answer: str, *, citations: Sequence[str] | None = None) -> Responder:
    """A responder that answers ``answer`` (``{cid}`` is the first extract id)."""

    def respond(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        del system, schema
        cid = _first_chunk_id(user)
        return {
            "answer": answer.format(cid=cid),
            "citations": [c.format(cid=cid) for c in (citations or ["{cid}"])],
            "confidence": 0.9,
        }

    return respond


__all__ = [
    "CANARY_ITEMS",
    "MAIL",
    "MATRICULE",
    "NAME",
    "NAS",
    "PHONE",
    "CanaryItem",
    "Probes",
    "echo_first_extract",
    "echo_prompt",
    "echo_question",
    "hr_settings",
    "leaks",
    "leaky",
    "log_text",
    "make_agent",
    "probed_retriever",
    "scripted",
]
