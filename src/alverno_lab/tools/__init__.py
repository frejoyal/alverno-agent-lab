"""Module 3 tools: typed registry, approval tokens and the executor (ADR 0012, ADR 0013).

Dependency direction: ``tools`` imports ``connectors`` and the module 3 types of
``privacy``; ``connectors`` and ``privacy`` never import ``tools``. No module of this
package makes or imports a network call, and no tool sends anything.
"""

from __future__ import annotations

from alverno_lab.tools.authority import (
    ApprovalAuthority,
    ApprovalError,
    ApprovalToken,
    ApprovalVerifier,
    VerifyResult,
)
from alverno_lab.tools.executor import (
    UNKNOWN_TOOL,
    ToolCallRecord,
    ToolData,
    ToolError,
    ToolExecutor,
    ToolResult,
    raw_args_sha256,
)
from alverno_lab.tools.registry import (
    FLOW_PLANS,
    READ_TOOLS,
    TOOL_KINDS,
    TOOL_NAMES,
    TOOLS,
    WRITE_TOOLS,
    CreateWorkRequestArgs,
    FailureCatalogArgs,
    GetAssetArgs,
    GetOrderArgs,
    ListWorkOrdersArgs,
    RecordReplyPreparedArgs,
    ToolKind,
    ToolName,
    ToolSpec,
    args_sha256,
    canonical_json,
)

__all__ = [
    "FLOW_PLANS",
    "READ_TOOLS",
    "TOOLS",
    "TOOL_KINDS",
    "TOOL_NAMES",
    "UNKNOWN_TOOL",
    "WRITE_TOOLS",
    "ApprovalAuthority",
    "ApprovalError",
    "ApprovalToken",
    "ApprovalVerifier",
    "CreateWorkRequestArgs",
    "FailureCatalogArgs",
    "GetAssetArgs",
    "GetOrderArgs",
    "ListWorkOrdersArgs",
    "RecordReplyPreparedArgs",
    "ToolCallRecord",
    "ToolData",
    "ToolError",
    "ToolExecutor",
    "ToolKind",
    "ToolName",
    "ToolResult",
    "ToolSpec",
    "VerifyResult",
    "args_sha256",
    "canonical_json",
    "raw_args_sha256",
]
