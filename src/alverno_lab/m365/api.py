"""Local HTTP API of the Quality agent for Microsoft 365 (docs/adr/0026).

Two routes: ``GET /v1/health`` (no token, no content) and ``POST /v1/quality/ask``
(Entra ID token with the ``Quality.Ask`` role). Only the Quality agent (level 2) is
exposed; no route writes. Each call to ``/v1/quality/ask`` adds one access-log record
without the question or the answer. The app listens where :func:`serve` puts it, and
:func:`serve` refuses any address but ``127.0.0.1``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from alverno_lab.agents.quality.graph import QualityAnswer
from alverno_lab.m365.auth import TokenRejected, TokenVerifier
from alverno_lab.privacy.access_log import AccessLog, AccessLogError
from alverno_lab.privacy.m365_access_log import M365AccessLogRecord

ROUTES: tuple[tuple[str, str], ...] = (("GET", "/v1/health"), ("POST", "/v1/quality/ask"))
LOOPBACK = "127.0.0.1"
MAX_QUESTION_CHARS = 500
Ask = Callable[[str], QualityAnswer]


def hash_user(oid: str, salt: bytes) -> str:
    """Salted SHA-256 of the token's ``oid``, cut to 32 hex characters."""
    return hashlib.sha256(salt + oid.encode("utf-8")).hexdigest()[:32]


def load_salt(path: Path) -> bytes:
    """The workstation's salt, created at first use (32 random bytes, outside the repo)."""
    import secrets

    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def answer_payload(answer: QualityAnswer) -> dict[str, Any]:
    """What the API returns: the same fields the parity metric compares."""
    return {
        "answer": answer.answer,
        "refused": answer.refused,
        "refusal_reason": answer.refusal_reason,
        "citations": list(answer.citations),
        "sources": list(answer.cited_doc_ids),
    }


def create_app(
    ask: Ask,
    verifier: TokenVerifier,
    log: AccessLog[M365AccessLogRecord],
    salt: bytes,
    *,
    channel: str = "api",
) -> Starlette:
    def record(**fields: Any) -> None:
        # A log that cannot be written refuses the call (ADR 0008): no unlogged answer.
        log.append(log.record(channel=channel, **fields))

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def ask_quality(request: Request) -> JSONResponse:
        started = time.perf_counter()
        try:
            principal = verifier.verify(request.headers.get("authorization"))
        except TokenRejected as exc:
            decision = "forbidden" if exc.oid is not None else "unauthorized"
            # A forbidden token is valid: its user is known; an unauthorized one is not.
            user = hash_user(exc.oid, salt) if exc.oid is not None else None
            try:
                record(decision=decision, reason=exc.code, user=user)
            except AccessLogError:
                return JSONResponse({"error": "audit_unavailable"}, status_code=503)
            return JSONResponse(
                {"error": exc.code},
                status_code=exc.status,
                headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
            )
        user = hash_user(principal.oid, salt)
        try:
            body = json.loads(await request.body())
            question = body["question"] if isinstance(body, dict) else None
        except (ValueError, KeyError):
            question = None
        problem = None
        if not isinstance(question, str) or not question.strip():
            problem = "no_question"
        elif len(question) > MAX_QUESTION_CHARS:
            problem = "too_long"
        if problem is not None or not isinstance(question, str):
            code = problem or "no_question"
            try:
                record(decision="invalid", reason=code, user=user)
            except AccessLogError:
                return JSONResponse({"error": "audit_unavailable"}, status_code=503)
            return JSONResponse({"error": code}, status_code=400)
        try:
            answer = await run_in_threadpool(ask, question.strip())
        except Exception:  # an agent failure is logged as an error, never echoed
            try:
                record(decision="error", reason="agent_error", user=user)
            except AccessLogError:
                return JSONResponse({"error": "audit_unavailable"}, status_code=503)
            return JSONResponse({"error": "agent_error"}, status_code=502)
        latency = int((time.perf_counter() - started) * 1000)
        try:
            record(
                decision="refused" if answer.refused else "answered",
                reason=(answer.refusal_reason or "refused") if answer.refused else None,
                user=user,
                n_citations=len(answer.citations),
                latency_ms=latency,
            )
        except AccessLogError:
            return JSONResponse({"error": "audit_unavailable"}, status_code=503)
        return JSONResponse(answer_payload(answer))

    return Starlette(
        routes=[
            Route("/v1/health", health, methods=["GET"]),
            Route("/v1/quality/ask", ask_quality, methods=["POST"]),
        ]
    )


def app_routes(app: Starlette) -> set[tuple[str, str]]:
    """(method, path) of every route of ``app``, HEAD left out."""
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, Route):
            for method in route.methods or ():
                if method != "HEAD":
                    found.add((method, route.path))
    return found


def serve(app: Starlette, host: str, port: int) -> None:  # pragma: no cover - network
    """Run ``app`` with uvicorn on the loopback address only."""
    if host != LOOPBACK:
        raise ValueError(f"the API listens on {LOOPBACK} only (ADR 0026)")
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
