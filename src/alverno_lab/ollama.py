"""Thin synchronous Ollama HTTP client with clear, actionable errors."""

from __future__ import annotations

import json
from typing import Any

import httpx

from alverno_lab.config import LOCAL_HOSTS


class OllamaError(RuntimeError):
    """Base error for Ollama calls; the message is meant for the end user."""


class OllamaUnavailableError(OllamaError):
    """Ollama does not answer at the configured URL."""


class OllamaTimeoutError(OllamaError):
    """Ollama accepted the request but did not answer in time."""


class OllamaModelNotFoundError(OllamaError):
    """The requested model is not pulled on the Ollama server."""


class OllamaClient:
    """POSTs JSON to Ollama and maps transport failures to :class:`OllamaError`."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            transport=transport,
            # A proxy from the environment or the Windows registry would send questions
            # and extracts off the workstation (Law 25, local-first). A remote server is
            # an explicit opt-in and keeps the network settings of the environment.
            trust_env=(httpx.URL(self.base_url).host or "").lower() not in LOCAL_HOSTS,
        )

    # JSON payloads and responses are heterogeneous Ollama documents.
    def post_json(self, path: str, payload: dict[str, Any], *, model: str) -> dict[str, Any]:
        """POST ``payload`` and return the decoded JSON object."""
        try:
            response = self._client.post(path, json=payload)
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve` "
                "or set ALVERNO_OLLAMA_URL."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(
                f"Ollama did not answer within {self.timeout:.0f} s for model {model} "
                f"({path}). The model may still be loading or downloading; retry or raise "
                "ALVERNO_REQUEST_TIMEOUT."
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"HTTP error while calling Ollama {path}: {exc}") from exc
        if response.status_code == 404:
            raise OllamaModelNotFoundError(
                f"Model {model} is not available in Ollama. Run `ollama pull {model}`."
            )
        if response.status_code >= 400:
            detail = response.text[:300]
            raise OllamaError(f"Ollama {path} returned HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama {path} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise OllamaError(f"Ollama {path} returned a non-object JSON document")
        return data

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()
