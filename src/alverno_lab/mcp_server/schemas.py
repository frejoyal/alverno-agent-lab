"""Inputs and outputs of the MCP tools (docs/adr/0017).

Inputs are strict pydantic models: an unknown field, a wrong type or an out-of-range value
is an ``invalid_input`` tool error, decided before any backend runs. Validation errors are
never shown: their messages would echo the input (a question may hold a PI by accident).
Free-text lengths are checked by :func:`check_text`, against ``mcp_max_input_chars``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

__all__ = [
    "ERROR_CODES",
    "AgentAnswer",
    "ErrorCode",
    "HrPolicyAskInput",
    "MachineDiagnosisInput",
    "OpsAnswer",
    "OrderStatusInput",
    "QualityAskInput",
    "QualitySearchInput",
    "SearchHit",
    "SearchResult",
    "ToolFailure",
    "check_text",
]

ErrorCode = Literal["unknown_tool", "invalid_input", "backend_unavailable", "internal_error"]
ERROR_CODES: tuple[ErrorCode, ...] = (
    "unknown_tool",
    "invalid_input",
    "backend_unavailable",
    "internal_error",
)
MAX_SEARCH_K = 8
EXCERPT_CHARS = 400

OrderId = Annotated[str, StringConstraints(pattern=r"^CMD-\d{4}-\d{4}$")]
AssetId = Annotated[str, StringConstraints(pattern=r"^EQ-[A-Z]{3}-\d{2}$")]
SymptomCode = Annotated[str, StringConstraints(pattern=r"^SYM-[A-Z]{2,12}$")]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class QualitySearchInput(_Input):
    query: str
    k: int = Field(default=5, ge=1, le=MAX_SEARCH_K)


class QualityAskInput(_Input):
    question: str


class HrPolicyAskInput(_Input):
    question: str


class OrderStatusInput(_Input):
    order_id: OrderId


class MachineDiagnosisInput(_Input):
    asset_id: AssetId
    symptom_code: SymptomCode


def check_text(text: str, max_chars: int) -> bool:
    """A free-text input is valid when it is not blank and fits ``max_chars``."""
    return bool(text.strip()) and len(text) <= max_chars


class SearchHit(BaseModel):
    """One retrieved passage: ids, title, version and the start of its text."""

    chunk_id: str
    doc_id: str
    title: str
    version: str
    excerpt: str


class SearchResult(BaseModel):
    hits: list[SearchHit]


class AgentAnswer(BaseModel):
    """What ``alverno ask`` and ``alverno hr ask`` print: the text, the refusal, the sources."""

    answer: str
    refused: bool
    refusal_reason: str | None
    citations: list[str]


class OpsAnswer(BaseModel):
    """The served text of a module 3 read flow and how it ended."""

    outcome: str
    text: str
    refusal_reason: str | None


class ToolFailure(BaseModel):
    """A closed code, never a message: the input stays out of every error."""

    error: ErrorCode
