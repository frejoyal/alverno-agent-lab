"""Embeddings, indexes, RRF fusion and the no-match barrier."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from alverno_lab.corpus import Chunk
from alverno_lab.embeddings import HashEmbedder
from alverno_lab.index import (
    BM25Index,
    IndexMismatchError,
    IndexNotFoundError,
    VectorIndex,
    corpus_hash,
)
from alverno_lab.retrieval import (
    CONSENSUS_BONUS,
    HybridRetriever,
    Reranker,
    min_max,
    names_version,
    query_coverage,
    rrf_fuse,
)


def test_rrf_fuse_matches_formula() -> None:
    fused = dict(rrf_fuse([["a", "b", "c"], ["b", "d"]], k=60))
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["d"] == pytest.approx(1 / 62)
    assert list(dict(rrf_fuse([["a", "b", "c"], ["b", "d"]]))) == ["b", "a", "d", "c"]


def test_rrf_fuse_ties_duplicates_and_validation() -> None:
    assert [i for i, _ in rrf_fuse([["x"], ["y"]])] == ["x", "y"]
    assert dict(rrf_fuse([["a", "a"]]))["a"] == pytest.approx(1 / 61)
    assert rrf_fuse([]) == []
    with pytest.raises(ValueError):
        rrf_fuse([["a"]], k=0)


def test_min_max() -> None:
    assert min_max([2.0, 4.0, 3.0]) == [0.0, 1.0, 0.5]
    assert min_max([5.0, 5.0]) == [1.0, 1.0]
    assert min_max([]) == []


def test_query_coverage() -> None:
    assert query_coverage("épaisseur peinture", "Peinture : épaisseur 60 µm") == 1.0
    assert query_coverage("épaisseur télétravail", "épaisseur") == 0.5
    assert query_coverage("de la", "texte") == 0.0


def test_hash_embedder_is_deterministic_and_normalised() -> None:
    emb = HashEmbedder(dim=64)
    a = emb.embed(["soudure caniveau", "soudure caniveau", ""])
    assert a.shape == (3, 64)
    assert np.allclose(a[0], a[1])
    assert np.linalg.norm(a[0]) == pytest.approx(1.0)
    assert np.linalg.norm(a[2]) == 0.0
    assert emb.embed([]).shape == (0, 64)
    assert emb.model_name == "hash-64"
    with pytest.raises(ValueError):
        HashEmbedder(dim=1)


def test_vector_index_roundtrip_and_manifest_checks(chunks: list[Chunk], tmp_path: Path) -> None:
    emb = HashEmbedder()
    index = VectorIndex.build(chunks, emb)
    path = tmp_path / "idx" / "index.npz"
    index.save(path)
    digest = corpus_hash(chunks)
    loaded = VectorIndex.load(path, expected_model=emb.model_name, expected_hash=digest)
    assert loaded.chunk_ids == index.chunk_ids
    assert np.allclose(loaded.embeddings, index.embeddings)

    with pytest.raises(IndexMismatchError, match="embedding model"):
        VectorIndex.load(path, expected_model="other-model", expected_hash=digest)
    with pytest.raises(IndexMismatchError, match="hash"):
        VectorIndex.load(path, expected_model=emb.model_name, expected_hash="0" * 64)
    with pytest.raises(IndexNotFoundError):
        VectorIndex.load(tmp_path / "nope.npz", expected_model="m", expected_hash="h")


@pytest.mark.parametrize("name", ["index", "index.bin", "index.npz"])
def test_vector_index_roundtrip_without_npz_extension(
    chunks: list[Chunk], tmp_path: Path, name: str
) -> None:
    # Regression: np.savez appended ".npz" and load() then found no index.
    emb = HashEmbedder()
    path = tmp_path / name
    VectorIndex.build(chunks, emb).save(path)
    loaded = VectorIndex.load(
        path, expected_model=emb.model_name, expected_hash=corpus_hash(chunks)
    )
    assert loaded.chunk_ids == [c.chunk_id for c in chunks]


def test_corrupt_manifest_is_refused(chunks: list[Chunk], tmp_path: Path) -> None:
    path = tmp_path / "index.npz"
    VectorIndex.build(chunks, HashEmbedder()).save(path)
    path.with_suffix(".manifest.json").write_text("{bad json", encoding="utf-8")
    with pytest.raises(IndexMismatchError):
        VectorIndex.load(path, expected_model="hash-256", expected_hash=corpus_hash(chunks))


def test_corpus_hash_changes_with_text(chunks: list[Chunk]) -> None:
    changed = [chunks[0].model_copy(update={"text": "autre"}), *chunks[1:]]
    assert corpus_hash(chunks) != corpus_hash(changed)
    assert corpus_hash(chunks) == corpus_hash(list(chunks))


def test_bm25_finds_exact_terms(chunks: list[Chunk]) -> None:
    bm25 = BM25Index(chunks)
    results = bm25.search("caniveau", top_k=5)
    assert chunks[results[0][0]].doc_id == "IT-021"
    assert bm25.search("de la", top_k=5) == []
    assert BM25Index([]).search("caniveau", 5) == []


def test_hybrid_search_ranks_expected_doc_first(retriever: HybridRetriever) -> None:
    hits = retriever.search("épaisseur du feuil de peinture en poudre")
    assert hits[0].chunk.doc_id == "QP-010"
    assert len(hits) <= retriever.final_k
    assert hits[0].rank_bm25 == 1
    assert hits[0].rerank_score is None
    assert retriever.search("   ") == []
    assert len(retriever.search("soudure", top_k=2)) == 2


def test_consensus_bonus_applied(retriever: HybridRetriever) -> None:
    hits = retriever.search("rugosité Ra inox aliments")
    top = hits[0]
    assert top.rank_vec == 1 and top.rank_bm25 == 1
    assert top.score == pytest.approx(0.45 + 0.55 + CONSENSUS_BONUS)


def test_no_match_gate_on_fake_coverage(retriever: HybridRetriever) -> None:
    assert retriever.gate_signal == "coverage"
    good = retriever.search("profondeur du caniveau des soudures")
    assert not retriever.is_no_match(good)
    bad = retriever.search("recette de tourtière du Lac-Saint-Jean")
    assert retriever.is_no_match(bad)
    assert retriever.is_no_match([])


class FixedReranker:
    """Reranker returning the same logit for every passage."""

    def __init__(self, value: float) -> None:
        self.value = value

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [self.value + 0.01 * i for i in range(len(texts))]


@pytest.mark.parametrize(("logit", "no_match"), [(-5.0, True), (2.0, False)])
def test_no_match_gate_on_reranker_score(chunks: list[Chunk], logit: float, no_match: bool) -> None:
    emb = HashEmbedder()
    reranker: Reranker = FixedReranker(logit)
    retriever = HybridRetriever(
        chunks,
        emb,
        VectorIndex.build(chunks, emb),
        BM25Index(chunks),
        reranker=reranker,
        no_match_threshold=0.0,
    )
    assert retriever.gate_signal == "rerank"
    hits = retriever.search("caniveau soudure")
    assert hits and hits[0].rerank_score is not None
    assert retriever.is_no_match(hits) is no_match


def test_cosine_gate_signal(chunks: list[Chunk]) -> None:
    emb = HashEmbedder()
    retriever = HybridRetriever(
        chunks, emb, VectorIndex.build(chunks, emb), BM25Index(chunks), no_match_threshold=0.05
    )
    assert retriever.gate_signal == "cosine"
    assert not retriever.is_no_match(retriever.search("caniveau soudure"))
    strict = HybridRetriever(
        chunks, emb, VectorIndex.build(chunks, emb), BM25Index(chunks), no_match_threshold=0.99
    )
    assert strict.is_no_match(strict.search("caniveau soudure"))


def test_retriever_rejects_misaligned_index(chunks: list[Chunk]) -> None:
    emb = HashEmbedder()
    with pytest.raises(ValueError):
        HybridRetriever(chunks[1:], emb, VectorIndex.build(chunks, emb), BM25Index(chunks))


def test_current_version_outranks_superseded(retriever: HybridRetriever) -> None:
    hits = retriever.search("fréquence d'étalonnage des micromètres et pieds à coulisse")
    doc_ids = [h.chunk.doc_id for h in hits]
    assert doc_ids[0] == "QP-004"
    assert "QP-004-V1" in doc_ids


def test_named_archived_version_is_retrievable(retriever: HybridRetriever) -> None:
    # Regression: the fixed 0.50 penalty kept archived versions out of the top k even
    # when the question named them.
    question = "Quelle était la fréquence d'étalonnage des micromètres dans la version 4.0?"
    named = [h.chunk.doc_id for h in retriever.search(question)]
    assert "QP-004-V1" in named[:3]
    chunk = next(c for c in retriever.chunks if c.doc_id == "QP-004-V1")
    assert names_version("selon la version 4.0", chunk)
    assert names_version("selon QP-004-V1", chunk)
    assert not names_version("selon la version 4.01", chunk)
    assert not names_version("quelle version est en vigueur?", chunk)


def test_bm25_ignores_the_title_repeated_in_every_chunk() -> None:
    """Regression (q006): title words gave every chunk of a document a BM25 score, so
    its short chunks pushed the one holding the answer out of the final top 5."""
    title = "Spécification client — Coffrets électriques Ruvelle : armoires et boîtiers"

    def chunk(doc: str, n: int, doc_title: str, section: str, text: str) -> Chunk:
        return Chunk(
            chunk_id=f"{doc}#{n:02d}", doc_id=doc, title=doc_title, section=section, text=text
        )

    chunks = [
        chunk("SPEC-T", 1, title, "Portée", "Boîtiers muraux et consoles."),
        chunk("SPEC-T", 2, title, "Emballage", "Palettes filmées."),
        chunk("SPEC-T", 3, title, "Historique", "Version initiale."),
        chunk(
            "SPEC-T",
            4,
            title,
            "Peinture en poudre",
            "- Couleur RAL 7035, fini texturé fin, poudre polyester pour l'intérieur et "
            "l'extérieur.\n- Épaisseur de 70 à 110 µm, mesurée en cinq points par pièce.\n"
            "- Adhérence 5B au quadrillage, essai sur une pièce par lot.\n- Cuisson selon "
            "la fiche technique du fournisseur, contrôle visuel sous éclairage normalisé.",
        ),
        chunk("QP-X", 1, "Procédure d'étalonnage", "Intervalles", "Pieds à coulisse : six mois."),
        chunk("IT-X", 1, "Instruction de peinture", "Couleur", "Couleur selon le bon de travail."),
        chunk("IT-Y", 1, "Instruction de soudage", "Cordons", "Cordons selon la norme CSA W59."),
    ]
    question = (
        "Quelle couleur de peinture Coffrets électriques Ruvelle exige-t-il pour ses armoires ?"
    )
    ranked = [chunks[i].chunk_id for i, _ in BM25Index(chunks).search(question, 7)]
    assert ranked[0] == "SPEC-T#04"
    # Chunks that match only through the repeated title no longer score at all.
    assert not {"SPEC-T#01", "SPEC-T#02", "SPEC-T#03"} & set(ranked)
