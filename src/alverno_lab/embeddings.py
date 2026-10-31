"""Text embedders: Ollama for real runs, a deterministic hashing embedder for CI."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Protocol, runtime_checkable

import httpx
import numpy as np
import numpy.typing as npt

from alverno_lab.ollama import OllamaClient, OllamaError
from alverno_lab.text import tokenize

FloatMatrix = npt.NDArray[np.float32]


@runtime_checkable
class Embedder(Protocol):
    """Embeds texts into L2-normalised row vectors."""

    @property
    def model_name(self) -> str:
        """Identifier stored in the index manifest."""
        ...

    def embed(self, texts: list[str]) -> FloatMatrix:
        """Return an ``(len(texts), dim)`` float32 matrix with unit-norm rows."""
        ...


def l2_normalize(matrix: FloatMatrix) -> FloatMatrix:
    """Normalise rows to unit length; all-zero rows stay zero."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class HashEmbedder:
    """Deterministic bag-of-words embedder (hashing trick); no model, no network."""

    def __init__(self, dim: int = 256) -> None:
        if dim < 2:
            raise ValueError("dim must be >= 2")
        self.dim = dim

    @property
    def model_name(self) -> str:
        """Identifier stored in the index manifest."""
        return f"hash-{self.dim}"

    def _vector(self, text: str) -> npt.NDArray[np.float32]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token, count in Counter(tokenize(text)).items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            sign = 1.0 if (value >> 63) & 1 == 0 else -1.0
            vec[value % self.dim] += sign * (1.0 + math.log(count))
        return vec

    def embed(self, texts: list[str]) -> FloatMatrix:
        """Embed texts with the hashing trick and L2-normalise."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return l2_normalize(np.stack([self._vector(t) for t in texts]))


class OllamaEmbedder:
    """Embeddings from a local Ollama server via ``POST /api/embed`` (batched)."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
        batch_size: int = 32,
        keep_alive: str = "10m",
        num_ctx: int = 2048,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.batch_size = batch_size
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self._client = OllamaClient(base_url, timeout=timeout, transport=transport)

    @property
    def model_name(self) -> str:
        """Identifier stored in the index manifest."""
        return self.model

    def embed(self, texts: list[str]) -> FloatMatrix:
        """Embed texts in batches and L2-normalise."""
        rows: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            payload = {
                "model": self.model,
                "input": batch,
                "keep_alive": self.keep_alive,
                "options": {"num_ctx": self.num_ctx},
            }
            data = self._client.post_json("/api/embed", payload, model=self.model)
            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise OllamaError(f"unexpected /api/embed response for model {self.model}")
            rows.extend([float(x) for x in row] for row in embeddings)
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return l2_normalize(np.asarray(rows, dtype=np.float32))
