"""The local MCP server of the lab: stdio only, read only, level 2 by default (ADR 0017).

The server is the SDK's low-level ``Server``: the tool list and every call go through the
two handlers below, so the server decides each error itself. An unknown name (a write
tool, a module 3 tool while ``ALVERNO_MCP_OPS`` is off) is ``unknown_tool``; an input that
fails its model or the length rule is ``invalid_input``; both are decided before any
backend runs. A backend failure is ``backend_unavailable`` (Ollama, the index) or
``internal_error``. No error carries a message: the question stays out of every error.
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ValidationError

from alverno_lab.config import Settings
from alverno_lab.index import IndexStoreError
from alverno_lab.mcp_server.adapters import Backends, UnknownSymptomError
from alverno_lab.mcp_server.schemas import (
    ErrorCode,
    HrPolicyAskInput,
    MachineDiagnosisInput,
    OrderStatusInput,
    QualityAskInput,
    QualitySearchInput,
    ToolFailure,
    check_text,
)
from alverno_lab.ollama import OllamaError

__all__ = [
    "BASE_TOOLS",
    "INSTRUCTIONS",
    "OPS_TOOLS",
    "OPS_WARNING",
    "build_server",
    "run_stdio",
    "tool_names",
]

INSTRUCTIONS = (
    "Outils de Fabrications Alverno inc. (entreprise fictive), en lecture seule. "
    "Réservés à un client MCP dont le modèle de langage tourne sur le poste : ne les "
    "reliez pas à un modèle hébergé. La question n'est pas conservée ; le journal des "
    "accès du module RH note seulement des métadonnées (canal mcp), 90 jours au plus, "
    "pour le responsable de la protection des renseignements personnels "
    "(confidentialite@alverno.example)."
)
OPS_WARNING = (
    "Warning: ALVERNO_MCP_OPS=true exposes order_status and machine_diagnosis, whose "
    "answers may hold personal information (level 4). Use them only with an MCP client "
    "whose model runs on this workstation (EFVP of module 5, art. 17)."
)


@dataclass(frozen=True)
class ToolSpec:
    """A tool: its input model, whether it reads free text, and its call."""

    name: str
    description: str
    model: type[BaseModel]
    text_field: str | None
    call: Callable[[Backends, BaseModel], BaseModel]


def _search(b: Backends, args: BaseModel) -> BaseModel:
    assert isinstance(args, QualitySearchInput)
    return b.quality_search(args.query, args.k)


def _quality(b: Backends, args: BaseModel) -> BaseModel:
    assert isinstance(args, QualityAskInput)
    return b.quality_ask(args.question)


def _hr(b: Backends, args: BaseModel) -> BaseModel:
    assert isinstance(args, HrPolicyAskInput)
    return b.hr_policy_ask(args.question)


def _order(b: Backends, args: BaseModel) -> BaseModel:
    assert isinstance(args, OrderStatusInput)
    return b.order_status(args)


def _diagnosis(b: Backends, args: BaseModel) -> BaseModel:
    assert isinstance(args, MachineDiagnosisInput)
    return b.machine_diagnosis(args)


BASE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "quality_search",
        "Cherche des passages du corpus Qualité (niveau 2) ; sans modèle de langage.",
        QualitySearchInput,
        "query",
        _search,
    ),
    ToolSpec(
        "quality_ask",
        "Pose une question à l'agent Qualité ; réponse citée ou refus motivé.",
        QualityAskInput,
        "question",
        _quality,
    ),
    ToolSpec(
        "hr_policy_ask",
        "Pose une question sur une politique RH ; aucune question sur une personne.",
        HrPolicyAskInput,
        "question",
        _hr,
    ),
)
OPS_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "order_status",
        "État d'une commande, par son identifiant CMD-AAAA-NNNN (lecture seule).",
        OrderStatusInput,
        None,
        _order,
    ),
    ToolSpec(
        "machine_diagnosis",
        "Diagnostic d'un arrêt, par l'identifiant EQ-XXX-NN et un code de symptôme.",
        MachineDiagnosisInput,
        None,
        _diagnosis,
    ),
)


def tool_names(*, ops: bool) -> tuple[str, ...]:
    """The names ``tools/list`` gives, in order."""
    return tuple(t.name for t in (*BASE_TOOLS, *(OPS_TOOLS if ops else ())))


def _schema(spec: ToolSpec, max_chars: int) -> dict[str, Any]:
    # Any: a JSON Schema is an arbitrary JSON object.
    schema = spec.model.model_json_schema()
    if spec.text_field is not None:
        schema["properties"][spec.text_field]["maxLength"] = max_chars
    schema["additionalProperties"] = False
    return schema


def _failure(code: ErrorCode) -> types.CallToolResult:
    payload = ToolFailure(error=code).model_dump()
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=True,
    )


def _success(result: BaseModel) -> types.CallToolResult:
    payload = result.model_dump()
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
    )


def call_tool(
    backends: Backends, settings: Settings, name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    """One tool call, decided in order: name, input, then the backend."""
    specs = {t.name: t for t in (*BASE_TOOLS, *(OPS_TOOLS if backends.ops_enabled else ()))}
    spec = specs.get(name)
    if spec is None:
        return _failure("unknown_tool")
    args: BaseModel | None = None
    # A validation error would echo the input: it is dropped, only the code is served.
    with contextlib.suppress(ValidationError):
        args = spec.model.model_validate(arguments or {})
    if args is None:
        return _failure("invalid_input")
    if spec.text_field is not None:
        text = getattr(args, spec.text_field)
        if not check_text(text, settings.mcp_max_input_chars):
            return _failure("invalid_input")
    code: ErrorCode | None = None
    result: BaseModel | None = None
    try:
        result = spec.call(backends, args)
    except UnknownSymptomError:
        code = "invalid_input"
    except (OllamaError, IndexStoreError):
        code = "backend_unavailable"
    except Exception:
        code = "internal_error"
    if code is not None or result is None:
        return _failure(code or "internal_error")
    return _success(result)


def build_server(backends: Backends, settings: Settings) -> Server[Any]:
    """The low-level server; ``tools/list`` follows ``backends.ops_enabled``."""
    # Any: the SDK's lifespan result type; this server has no lifespan state.
    specs = (*BASE_TOOLS, *(OPS_TOOLS if backends.ops_enabled else ()))
    tools = [
        types.Tool(
            name=spec.name,
            description=spec.description,
            input_schema=_schema(spec, settings.mcp_max_input_chars),
            annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
        )
        for spec in specs
    ]

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        del ctx, params
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        del ctx
        # The agents are synchronous; a worker thread keeps the event loop free.
        return await anyio.to_thread.run_sync(
            call_tool, backends, settings, params.name, params.arguments
        )

    return Server(
        "alverno-agent-lab",
        version="0.5.0",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def run_stdio(backends: Backends, settings: Settings) -> None:
    """Serve on stdin and stdout until the client closes them; no port is opened."""
    if backends.ops_enabled:
        print(OPS_WARNING, file=sys.stderr)
    server = build_server(backends, settings)

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(main)
