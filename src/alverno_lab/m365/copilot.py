"""Copilot Studio files of module 6 and their checks (docs/adr/0027).

:func:`openapi_document` builds the custom connector's OpenAPI 2.0 definition from the
API's routes; ``integrations/copilot-studio/connector/apiDefinition.swagger.json`` must
equal it. :func:`check_integration` measures what the gate reads: route coverage both
ways, the connector actions the topics call, and the generative settings of the agent.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import yaml

from alverno_lab.m365.api import LOOPBACK, MAX_QUESTION_CHARS, ROUTES

DEFAULT_PORT = 8765
SWAGGER = Path("connector") / "apiDefinition.swagger.json"
PROPERTIES = Path("connector") / "apiProperties.json"
AGENT = Path("agent") / "agent.yaml"
TOPICS = Path("agent") / "topics"
# Settings of agent.yaml that would send content to a hosted model (ADR 0027).
GENERATIVE_SETTINGS = (
    "generativeAnswers",
    "generativeOrchestration",
    "generativeActions",
    "useModelKnowledge",
    "webBrowsing",
)
OPERATIONS = {("GET", "/v1/health"): "Health", ("POST", "/v1/quality/ask"): "AskQuality"}


def openapi_document(port: int = DEFAULT_PORT) -> dict[str, Any]:
    """OpenAPI 2.0 (the format Power Platform custom connectors import)."""
    answer = {
        "type": "object",
        "required": ["answer", "refused", "citations", "sources"],
        "properties": {
            "answer": {"type": "string", "description": "Réponse avec citations, ou refus."},
            "refused": {"type": "boolean"},
            "refusal_reason": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}},
        },
    }
    error = {"type": "object", "properties": {"error": {"type": "string"}}}
    return {
        "swagger": "2.0",
        "info": {
            "title": "Alverno Qualité",
            "description": "Agent Qualité local de Fabrications Alverno (labo, entreprise "
            "fictive). Réponses à partir de la documentation qualité, avec citations.",
            "version": "1.0",
        },
        "host": f"{LOOPBACK}:{port}",
        "basePath": "/",
        "schemes": ["http"],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "securityDefinitions": {
            "oauth2_auth": {
                "type": "oauth2",
                "flow": "accessCode",
                "authorizationUrl": "https://login.microsoftonline.com/common/oauth2/authorize",
                "tokenUrl": "https://login.microsoftonline.com/common/oauth2/token",
                "scopes": {},
            }
        },
        "security": [{"oauth2_auth": []}],
        "paths": {
            "/v1/health": {
                "get": {
                    "operationId": OPERATIONS[("GET", "/v1/health")],
                    "summary": "État du service",
                    "security": [],
                    "responses": {"200": {"description": "Service disponible"}},
                }
            },
            "/v1/quality/ask": {
                "post": {
                    "operationId": OPERATIONS[("POST", "/v1/quality/ask")],
                    "summary": "Poser une question Qualité",
                    "parameters": [
                        {
                            "name": "body",
                            "in": "body",
                            "required": True,
                            "schema": {
                                "type": "object",
                                "required": ["question"],
                                "properties": {
                                    "question": {
                                        "type": "string",
                                        "maxLength": MAX_QUESTION_CHARS,
                                        "description": "Question en français, sans "
                                        "renseignement personnel.",
                                    }
                                },
                            },
                        }
                    ],
                    "responses": {
                        "200": {"description": "Réponse ou refus", "schema": answer},
                        "400": {"description": "Question absente ou trop longue", "schema": error},
                        "401": {"description": "Jeton invalide", "schema": error},
                        "403": {"description": "Rôle Quality.Ask absent", "schema": error},
                    },
                }
            },
        },
    }


def render_swagger(port: int = DEFAULT_PORT) -> str:
    return json.dumps(openapi_document(port), ensure_ascii=False, indent=2) + "\n"


def swagger_routes(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in (document.get("paths") or {}).items()
        for method in operations
    }


def swagger_operations(document: dict[str, Any]) -> set[str]:
    return {
        str(op.get("operationId"))
        for operations in (document.get("paths") or {}).values()
        for op in operations.values()
    }


def topic_actions(topic: Any) -> list[str]:
    """Connector operations a topic calls (``InvokeConnectorAction`` nodes, any depth)."""
    found: list[str] = []
    if isinstance(topic, dict):
        if topic.get("kind") == "InvokeConnectorAction":
            found.append(str(topic.get("operationId")))
        for value in topic.values():
            found += topic_actions(value)
    elif isinstance(topic, list):
        for value in topic:
            found += topic_actions(value)
    return found


@dataclasses.dataclass(frozen=True)
class IntegrationCheck:
    spec_api_coverage: float
    topic_action_valid: int
    generative_flags: int
    n_topics: int
    n_actions: int
    swagger_current: bool


def check_integration(root: Path, api_routes: set[tuple[str, str]]) -> IntegrationCheck:
    """The integration checks of the ``m365-fake`` profile (ADR 0028)."""
    document = json.loads((root / SWAGGER).read_text(encoding="utf-8"))
    described = swagger_routes(document)
    union = described | api_routes
    coverage = len(described & api_routes) / len(union) if union else 0.0
    operations = swagger_operations(document)
    actions: list[str] = []
    topics = sorted((root / TOPICS).glob("*.yaml"))
    for path in topics:
        actions += topic_actions(yaml.safe_load(path.read_text(encoding="utf-8")))
    valid = int(bool(actions) and all(a in operations for a in actions))
    agent = yaml.safe_load((root / AGENT).read_text(encoding="utf-8")) or {}
    settings = agent.get("settings") or {}
    flags = sum(1 for key in GENERATIVE_SETTINGS if settings.get(key) is not False)
    flags += 1 if agent.get("knowledgeSources") else 0
    current = (root / SWAGGER).read_text(encoding="utf-8") == render_swagger(
        int(str(document.get("host", "")).rsplit(":", 1)[-1] or DEFAULT_PORT)
    )
    return IntegrationCheck(
        spec_api_coverage=round(coverage, 4),
        topic_action_valid=valid,
        generative_flags=flags,
        n_topics=len(topics),
        n_actions=len(actions),
        swagger_current=current,
    )


def routes_of_api() -> set[tuple[str, str]]:
    return set(ROUTES)
