"""Vector (numpy cosine) and BM25 indexes, with an integrity manifest for the vector index."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ValidationError
from rank_bm25 import BM25Okapi

from alverno_lab.corpus import Chunk
from alverno_lab.embeddings import Embedder, FloatMatrix
from alverno_lab.text import tokenize

__all__ = [
    "BM25Index",
    "IndexManifest",
    "IndexMismatchError",
    "IndexNotFoundError",
    "IndexStoreError",
    "VectorIndex",
    "corpus_hash",
    "tokenize",
]

MANIFEST_SCHEMA = 1


class IndexStoreError(RuntimeError):
    """Base error for index persistence."""


class IndexNotFoundError(IndexStoreError):
    """No saved index at the expected path."""


class IndexMismatchError(IndexStoreError):
    """The saved index was built with another model or another corpus."""


def corpus_hash(chunks: Sequence[Chunk]) -> str:
    """SHA-256 over chunk ids and indexed texts: changes with the corpus or the chunking."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(chunk.indexed_text.encode("utf-8"))
        digest.update(b"\x01")
    return digest.hexdigest()


class IndexManifest(BaseModel):
    """Sidecar JSON describing how a vector index was built."""

    schema_version: int = MANIFEST_SCHEMA
    embed_model: str
    corpus_hash: str
    dim: int
    n_chunks: int
    created_at: str


def manifest_path(index_path: Path) -> Path:
    """Manifest location next to the ``.npz`` file."""
    return index_path.with_suffix(".manifest.json")


class VectorIndex:
    """Dense cosine index over unit-norm embeddings held in memory."""

    def __init__(
        self, embeddings: FloatMatrix, chunk_ids: Sequence[str], manifest: IndexManifest
    ) -> None:
        if embeddings.shape[0] != len(chunk_ids):
            raise ValueError("embeddings and chunk_ids length differ")
        self.embeddings = embeddings
        self.chunk_ids = list(chunk_ids)
        self.manifest = manifest

    @classmethod
    def build(cls, chunks: Sequence[Chunk], embedder: Embedder) -> VectorIndex:
        """Embed every chunk's indexed text."""
        matrix = embedder.embed([c.indexed_text for c in chunks])
        manifest = IndexManifest(
            embed_model=embedder.model_name,
            corpus_hash=corpus_hash(chunks),
            dim=int(matrix.shape[1]) if matrix.ndim == 2 else 0,
            n_chunks=len(chunks),
            created_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        )
        return cls(matrix, [c.chunk_id for c in chunks], manifest)

    def search(self, query_vector: FloatMatrix, top_k: int) -> list[tuple[int, float]]:
        """Return ``(row, cosine)`` pairs, best first."""
        if len(self.chunk_ids) == 0:
            return []
        scores = self.embeddings @ query_vector.reshape(-1)
        k = min(top_k, len(scores))
        order = np.argsort(-scores, kind="stable")[:k]
        return [(int(i), float(scores[i])) for i in order]

    def save(self, path: Path) -> None:
        """Write ``path`` (.npz) and its JSON manifest."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write through a handle: np.savez(path) appends ".npz" to a path without that
        # extension, and load(path) would then not find the file.
        with path.open("wb") as handle:
            np.savez(handle, embeddings=self.embeddings, chunk_ids=np.asarray(self.chunk_ids))
        manifest_path(path).write_text(self.manifest.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, *, expected_model: str, expected_hash: str) -> VectorIndex:
        """Load an index; refuse it if the embedding model or corpus hash differs."""
        mpath = manifest_path(path)
        if not path.exists() or not mpath.exists():
            raise IndexNotFoundError(f"no index at {path}; run `alverno index` first")
        try:
            manifest = IndexManifest.model_validate_json(mpath.read_text(encoding="utf-8"))
        except (ValidationError, json.JSONDecodeError) as exc:
            raise IndexMismatchError(f"unreadable index manifest {mpath}: {exc}") from exc
        if manifest.schema_version != MANIFEST_SCHEMA:
            raise IndexMismatchError(
                f"index schema {manifest.schema_version} != {MANIFEST_SCHEMA}; "
                "run `alverno index` again"
            )
        if manifest.embed_model != expected_model:
            raise IndexMismatchError(
                f"index was built with embedding model {manifest.embed_model!r}, "
                f"expected {expected_model!r}; run `alverno index` again"
            )
        if manifest.corpus_hash != expected_hash:
            raise IndexMismatchError(
                "index does not match the current corpus (hash differs); run `alverno index` again"
            )
        with np.load(path, allow_pickle=False) as data:
            embeddings = data["embeddings"].astype(np.float32)
            chunk_ids = [str(x) for x in data["chunk_ids"]]
        if len(chunk_ids) != manifest.n_chunks:
            raise IndexMismatchError(f"index {path} is inconsistent with its manifest")
        return cls(embeddings, chunk_ids, manifest)


# Length normalisation (Okapi default 0.75). Chunks run from one line to a full
# section, so the default over-rewards short chunks; 0.3 keeps hit@1 and MRR and lifts
# context recall from 0.962 to 0.990 on the gold set (docs/adr/0004).
BM25_B = 0.3


class BM25Index:
    """Okapi BM25 over French tokens (lowercase, accents kept, short stopwords removed)."""

    def __init__(self, chunks: Sequence[Chunk], *, b: float = BM25_B) -> None:
        self.tokens = [tokenize(c.lexical_text) for c in chunks]
        # BM25Okapi divides by the corpus size; an empty corpus is handled in search().
        self._bm25 = BM25Okapi(self.tokens, b=b) if self.tokens else None

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Return ``(row, score)`` pairs with a positive score, best first."""
        query_tokens = tokenize(query)
        if self._bm25 is None or not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)
        order = np.argsort(-scores, kind="stable")
        return [(int(i), float(scores[i])) for i in order[:top_k] if scores[i] > 0]
