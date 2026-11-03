"""Chat models returning schema-validated JSON: Ollama for real runs, a fake for tests."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, Field, ValidationError

from alverno_lab.ollama import OllamaClient, OllamaError

T = TypeVar("T", bound=BaseModel)


class LLMOutputError(OllamaError):
    """The model output could not be validated against the requested schema."""


class LLMCall(BaseModel):
    """Measurements of one chat call."""

    latency_ms: float
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMStats(BaseModel):
    """Cumulative latency and token counters (Ollama ``prompt_eval_count``/``eval_count``)."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_ms: float = 0.0
    history: list[LLMCall] = Field(default_factory=list)

    def record(self, call: LLMCall) -> None:
        """Add one call to the counters."""
        self.calls += 1
        self.prompt_tokens += call.prompt_tokens
        self.completion_tokens += call.completion_tokens
        self.total_latency_ms += call.latency_ms
        self.history.append(call)


@runtime_checkable
class ChatModel(Protocol):
    """A chat model that answers with JSON validated by a pydantic schema."""

    stats: LLMStats

    @property
    def model_name(self) -> str:
        """Model identifier, used in reports."""
        ...

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        """Return the model answer parsed into ``schema``."""
        ...


class OllamaChat:
    """``POST /api/chat`` with ``format`` = JSON schema, temperature 0 and ``think: false``.

    Each call is preceded by a fixed one-token request (``reset_prompt_cache``). Ollama
    reuses the cached prompt prefix of the previous request, and the numerics of the
    answer then depend on that request: at temperature 0 the same request could cite
    another chunk after a different predecessor (ADR 0016, non-régression). After the
    reset, every call shares the same short prefix with the cache, so an identical
    request gets an identical answer whatever ran before.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
        keep_alive: str = "10m",
        num_ctx: int = 8192,
        max_retries: int = 1,
        reset_prompt_cache: bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.max_retries = max_retries
        self.reset_prompt_cache = reset_prompt_cache
        self.stats = LLMStats()
        self._client = OllamaClient(base_url, timeout=timeout, transport=transport)

    @property
    def model_name(self) -> str:
        """Model identifier, used in reports."""
        return self.model

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        """Call Ollama and validate the JSON answer; retry once on invalid output."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        for _attempt in range(self.max_retries + 1):
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
                "think": False,
                "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_ctx": self.num_ctx},
            }
            # The latency includes the reset: it is part of the cost of each answer.
            started = time.perf_counter()
            if self.reset_prompt_cache:
                self._reset_prompt_cache()
            data = self._client.post_json("/api/chat", payload, model=self.model)
            self.stats.record(
                LLMCall(
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=int(data.get("prompt_eval_count") or 0),
                    completion_tokens=int(data.get("eval_count") or 0),
                )
            )
            message = data.get("message")
            content = message.get("content", "") if isinstance(message, dict) else ""
            try:
                return schema.model_validate_json(_strip_fences(str(content)))
            except ValidationError as exc:
                # Field and message of each error, never the invalid value itself.
                last_error = "; ".join(
                    f"{'.'.join(str(p) for p in err['loc']) or 'JSON'}: {err['msg']}"
                    for err in exc.errors()[:3]
                )
                # At temperature 0 an identical request gives an identical answer: show
                # the model its output and the validation error before retrying.
                messages = [
                    *messages,
                    {"role": "assistant", "content": str(content)},
                    {
                        "role": "user",
                        "content": (
                            "Ta réponse n'est pas un JSON valide pour le schéma demandé : "
                            f"{last_error}. Réponds uniquement avec le JSON."
                        ),
                    },
                ]
        raise LLMOutputError(
            f"model {self.model} returned output that does not match {schema.__name__}: "
            f"{last_error}"
        )

    def _reset_prompt_cache(self) -> None:
        """Replace the cached prompt with a fixed one (one token generated, ignored).

        Same ``num_ctx`` and ``keep_alive`` as the real call, so the model is not reloaded.
        """
        self._client.post_json(
            "/api/chat",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "."}],
                "stream": False,
                "think": False,
                "keep_alive": self.keep_alive,
                "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": 1},
            },
            model=self.model,
        )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _strip_fences(content: str) -> str:
    """Remove Markdown code fences some models add around JSON."""
    return _FENCE_RE.sub("", content.strip())


# A fake responder receives (system, user, schema) and returns a JSON-like mapping;
# Any is needed because each schema has its own field types.
Responder = Callable[[str, str, type[BaseModel]], dict[str, Any]]

# Extract blocks follow the line that introduces them in the user prompt, each one
# starting its line with its id ("[QP-006#09] Title — Section"); searching from that
# line skips an id quoted in the question, even on a line of its own.
# The line is shared by the module prompts (« ... qualité », « ... RH d'Alverno »).
_EXTRACTS_LINE = "\nExtraits de la documentation"
_CHUNK_ID_RE = re.compile(r"^\[([A-Za-z0-9][\w.-]*#\d{2,})\]", re.MULTILINE)
# A block ends where the next one starts (blank line, then its id) or at the
# "[fin des extraits]" line; a body line may itself start with "[" ("[Note] ...").
_BLOCK_END_RE = re.compile(r"\n\n\[[A-Za-z0-9][\w.-]*#\d{2,}\]|\n\[fin des extraits\]")


def echo_first_extract(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Default fake answer: quote the first extract of the prompt and cite it."""
    del system, schema
    match = _CHUNK_ID_RE.search(user, max(user.rfind(_EXTRACTS_LINE), 0))
    if match is None:
        return {"answer": "", "citations": [], "confidence": 0.0}
    chunk_id = match.group(1)
    body = _BLOCK_END_RE.split(user[match.end() :], maxsplit=1)[0]
    lines = [line.strip() for line in body.splitlines()[1:] if line.strip()]
    excerpt = " ".join(lines)[:400]
    return {"answer": f"{excerpt} [{chunk_id}]", "citations": [chunk_id], "confidence": 0.8}


class FakeChat:
    """Scriptable offline chat model.

    ``script`` items are returned in order (dicts or a callable responder);
    once exhausted, ``responder`` answers (default: :func:`echo_first_extract`).
    """

    def __init__(
        self,
        script: Sequence[dict[str, Any] | Responder] | None = None,
        *,
        responder: Responder | None = None,
        model: str = "fake-chat",
        error: Exception | None = None,
    ) -> None:
        self._script = list(script or [])
        self._responder = responder or echo_first_extract
        self._model = model
        self._error = error
        self.stats = LLMStats()
        self.calls: list[tuple[str, str]] = []

    @property
    def model_name(self) -> str:
        """Model identifier, used in reports."""
        return self._model

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        """Return the next scripted answer validated against ``schema``."""
        self.calls.append((system, user))
        self.stats.record(
            LLMCall(latency_ms=0.0, prompt_tokens=len(user) // 4, completion_tokens=20)
        )
        if self._error is not None:
            raise self._error
        item = self._script.pop(0) if self._script else self._responder
        data = item(system, user, schema) if callable(item) else item
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise LLMOutputError(f"fake output does not match {schema.__name__}") from exc
