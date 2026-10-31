"""Factories wiring Settings into retrievers, chat models and agents (real or --fake)."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from alverno_lab.agents.quality.graph import QualityAgent
from alverno_lab.config import Settings
from alverno_lab.corpus import Chunk, chunk_corpus, load_corpus
from alverno_lab.embeddings import Embedder, HashEmbedder, OllamaEmbedder
from alverno_lab.index import BM25Index, VectorIndex, corpus_hash
from alverno_lab.llm import ChatModel, FakeChat, OllamaChat
from alverno_lab.retrieval import (
    CrossEncoderReranker,
    GateSignal,
    HybridRetriever,
    NoopReranker,
    Reranker,
)


@dataclass(frozen=True)
class Backend:
    """Human-readable description of the components in use, for reports."""

    name: str
    embed_model: str
    reranker: str
    gate_signal: GateSignal
    no_match_threshold: float


def load_chunks(settings: Settings) -> list[Chunk]:
    """Load and chunk the corpus configured in ``settings``."""
    documents = load_corpus(settings.corpus_dir, max_sensitivity=settings.max_sensitivity)
    return chunk_corpus(documents, settings.max_chunk_chars)


def make_embedder(settings: Settings, *, fake: bool) -> Embedder:
    """Hash embedder in fake mode, Ollama otherwise."""
    if fake:
        return HashEmbedder(settings.hash_dim)
    return OllamaEmbedder(
        settings.check_model_is_local(settings.embed_model),
        base_url=settings.ollama_url,
        timeout=settings.request_timeout,
        batch_size=settings.embed_batch_size,
        keep_alive=settings.keep_alive,
        num_ctx=settings.embed_num_ctx,
    )


def make_reranker(settings: Settings, *, fake: bool) -> Reranker:
    """Noop in fake mode or when disabled; cross-encoder when installed (``auto``)."""
    if fake or settings.reranker == "none":
        return NoopReranker()
    if settings.reranker == "auto" and importlib.util.find_spec("sentence_transformers") is None:
        return NoopReranker()
    return CrossEncoderReranker(settings.rerank_model)


def build_index(settings: Settings, *, fake: bool = False) -> VectorIndex:
    """Embed the corpus and, in real mode, persist the index with its manifest."""
    chunks = load_chunks(settings)
    index = VectorIndex.build(chunks, make_embedder(settings, fake=fake))
    if not fake:
        index.save(settings.index_path)
    return index


def build_retriever(settings: Settings, *, fake: bool = False) -> tuple[HybridRetriever, Backend]:
    """Build the hybrid retriever.

    Fake mode embeds in memory with :class:`HashEmbedder`; real mode loads the saved
    index and refuses it if the embedding model or corpus hash differs.
    """
    chunks = load_chunks(settings)
    embedder = make_embedder(settings, fake=fake)
    if fake:
        vector_index = VectorIndex.build(chunks, embedder)
    else:
        vector_index = VectorIndex.load(
            settings.index_path,
            expected_model=embedder.model_name,
            expected_hash=corpus_hash(chunks),
        )
    reranker = make_reranker(settings, fake=fake)
    signal: GateSignal
    if fake:
        signal, threshold = "coverage", settings.fake_no_match_threshold
    elif isinstance(reranker, NoopReranker):
        signal, threshold = "cosine", settings.vector_no_match_threshold
    else:
        signal, threshold = "rerank", settings.no_match_threshold
    retriever = HybridRetriever(
        chunks,
        embedder,
        vector_index,
        BM25Index(chunks),
        reranker=reranker,
        rrf_k=settings.rrf_k,
        candidate_k=settings.top_k,
        final_k=settings.final_k,
        no_match_threshold=threshold,
        gate_signal=signal,
        query_prefix="" if fake else settings.embed_query_prefix,
    )
    reranker_name = settings.rerank_model if retriever.uses_reranker else "none"
    backend = Backend(
        name="fake" if fake else "ollama",
        embed_model=embedder.model_name,
        reranker=reranker_name,
        gate_signal=signal,
        no_match_threshold=threshold,
    )
    return retriever, backend


def make_chat(settings: Settings, *, fake: bool, model: str | None = None) -> ChatModel:
    """FakeChat in fake mode, OllamaChat otherwise."""
    name = model or settings.chat_model
    if fake:
        return FakeChat(model=f"fake:{name}")
    return OllamaChat(
        settings.check_model_is_local(name),
        base_url=settings.ollama_url,
        timeout=settings.request_timeout,
        keep_alive=settings.keep_alive,
        num_ctx=settings.num_ctx,
    )


def build_agent(
    settings: Settings,
    *,
    fake: bool = False,
    model: str | None = None,
    retriever: HybridRetriever | None = None,
) -> QualityAgent:
    """Build the Quality agent (reusing ``retriever`` when given)."""
    if retriever is None:
        retriever, _ = build_retriever(settings, fake=fake)
    chat = make_chat(settings, fake=fake, model=model)
    return QualityAgent(retriever, chat, min_confidence=settings.min_confidence)
