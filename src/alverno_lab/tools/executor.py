"""Tool executor of module 3: validated arguments, approved writes, one record per call.

:meth:`ToolExecutor.call` is the only way the graphs reach a connector through a tool
(ADR 0012). It never raises: bad arguments, an unknown tool, a refused write and a
connector failure all come back as ``ToolResult(ok=False, error=<code>)``.

- Arguments are validated by the tool's args model. An ill-formed id is ``invalid_args``,
  never an exception carrying the value.
- The executor holds an :class:`~alverno_lab.tools.authority.ApprovalVerifier`, never the
  authority: it can redeem a token, not mint one.
- A write runs only when :meth:`ApprovalVerifier.redeem` returns ``"ok"`` for this tool
  and the SHA-256 of these exact canonical arguments (ADR 0013). ``redeem`` spends the
  nonce in the same atomic step, before the connector runs: « consumed only on
  success » means on a successful verification, so one token triggers at most one
  execution attempt, even when two calls race or the connector then fails. A refused
  token is never spent, and a second use gives ``token_reused``.
- What a connector returns must be of the tool's view type; anything else is
  ``connector_unavailable`` and is dropped, so a non-conforming connector cannot pass a
  raw record through.
- Every call appends one :class:`ToolCallRecord` to :attr:`ToolExecutor.records`: tool
  name, kind, argument digest and outcome flags, never a value. The digest of invalid
  arguments is the SHA-256 of the canonical JSON of the raw mapping, or ``""`` when the
  mapping cannot be serialised; an unknown tool name is recorded as ``"unknown"``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.connectors.gmao import GmaoConnector
from alverno_lab.connectors.types import (
    AssetView,
    ConnectorError,
    FailureCodeView,
    OrderView,
    WorkOrderView,
    WriteReceipt,
)
from alverno_lab.tools.authority import ApprovalToken, ApprovalVerifier, VerifyResult
from alverno_lab.tools.registry import (
    TOOLS,
    CreateWorkRequestArgs,
    GetAssetArgs,
    GetOrderArgs,
    ListWorkOrdersArgs,
    RecordReplyPreparedArgs,
    ToolKind,
    ToolName,
    args_sha256,
)

__all__ = [
    "UNKNOWN_TOOL",
    "ToolCallRecord",
    "ToolData",
    "ToolError",
    "ToolExecutor",
    "ToolResult",
    "raw_args_sha256",
]

ToolError = Literal[
    "invalid_args",
    "unknown_tool",
    "no_token",
    "bad_token",
    "wrong_tool",
    "wrong_args",
    "token_reused",
    # The connector refused the call (ConnectorError: an unknown order or asset, a work
    # order of another asset, ...); the message is not kept.
    "connector_error",
    # Any other exception of the connector (a real one could fail on I/O), or a result
    # that is not of the tool's view type.
    "connector_unavailable",
]

UNKNOWN_TOOL: Literal["unknown"] = "unknown"
RecordedTool = ToolName | Literal["unknown"]

ToolData = (
    OrderView
    | AssetView
    | tuple[WorkOrderView, ...]
    | tuple[FailureCodeView, ...]
    | WriteReceipt
    | None
)

_TOKEN_REFUSALS: dict[VerifyResult, ToolError] = {
    "bad_mac": "bad_token",
    "wrong_tool": "wrong_tool",
    "wrong_args": "wrong_args",
    "reused": "token_reused",
}


class ToolResult(BaseModel):
    """What a call returns: a view, a tuple of views, a receipt or nothing, or an error.

    ``data`` is ``None`` both for a failed call and for a read of an unknown id; ``ok``
    tells them apart.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    ok: bool
    tool: RecordedTool
    data: ToolData = None
    error: ToolError | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ToolResult:
        if self.ok == (self.error is not None) or (not self.ok and self.data is not None):
            raise ValueError("ok and error disagree")
        return self


class ToolCallRecord(BaseModel):
    """One call, as metadata only: no argument, no result, no message."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tool: RecordedTool
    kind: ToolKind | None
    args_sha256: str = Field(pattern=r"^([0-9a-f]{64})?$")
    approved: bool
    executed: bool
    refusal: ToolError | None


def raw_args_sha256(args: object) -> str:
    """Digest of arguments that did not validate: canonical JSON of the raw mapping.

    ``""`` when the value is not a mapping or cannot be serialised (non-JSON value,
    mixed key types, cycle, a mapping that fails when read).
    """
    if not isinstance(args, Mapping):
        return ""
    try:
        text = json.dumps(dict(args), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # Never raise: a raw mapping can fail in any way, with a value in the message.
    except Exception:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def _of_type(data: object, view: type[BaseModel]) -> bool:
    return isinstance(data, tuple) and all(isinstance(row, view) for row in data)


def _conforms(tool: ToolName, data: object) -> bool:
    """Whether ``data`` is what ``tool`` returns (exact view types, no raw record)."""
    if tool == "erp.get_order":
        return data is None or isinstance(data, OrderView)
    if tool == "gmao.get_asset":
        return data is None or isinstance(data, AssetView)
    if tool == "gmao.list_work_orders":
        return _of_type(data, WorkOrderView)
    if tool == "gmao.failure_catalog":
        return _of_type(data, FailureCodeView)
    return isinstance(data, WriteReceipt)


class ToolExecutor:
    """Runs registered tools against the connectors; writes need an approval token."""

    def __init__(self, erp: ErpConnector, gmao: GmaoConnector, verifier: ApprovalVerifier) -> None:
        # Fail closed: the authority itself (which can mint) is not accepted.
        if not isinstance(verifier, ApprovalVerifier):
            raise TypeError("ToolExecutor:verifier_required")
        self._erp = erp
        self._gmao = gmao
        self._verifier = verifier
        self.records: list[ToolCallRecord] = []

    def _finish(
        self,
        tool: RecordedTool,
        digest: str,
        *,
        data: ToolData = None,
        error: ToolError | None = None,
        approved: bool = False,
        executed: bool = False,
    ) -> ToolResult:
        kind = TOOLS[tool].kind if tool != UNKNOWN_TOOL else None
        self.records.append(
            ToolCallRecord(
                tool=tool,
                kind=kind,
                args_sha256=digest,
                approved=approved,
                executed=executed,
                refusal=error,
            )
        )
        return ToolResult(ok=error is None, tool=tool, data=data, error=error)

    def call(
        self,
        name: str,
        args: Mapping[str, object],
        *,
        token: ApprovalToken | None = None,
    ) -> ToolResult:
        """Validate, authorise and run one tool call; never raises."""
        tool = _known(name)
        if tool is None:
            return self._finish(UNKNOWN_TOOL, raw_args_sha256(args), error="unknown_tool")
        spec = TOOLS[tool]
        model: BaseModel | None = None
        digest = ""
        try:
            if isinstance(args, Mapping):
                model = spec.args_model.model_validate(dict(args))
                digest = args_sha256(model)
        # Never raise: a raw mapping can fail in any way, and an integer too long for
        # JSON fails only at the digest.
        except Exception:
            model = None
        if model is None:
            return self._finish(tool, raw_args_sha256(args), error="invalid_args")
        if spec.kind == "read":
            return self._run_read(tool, model, digest)
        return self._write(tool, model, digest, token)

    def _run_read(self, tool: ToolName, model: BaseModel, digest: str) -> ToolResult:
        error: ToolError | None = None
        data: object = None
        try:
            data = self._read(model)
        except ConnectorError:
            error = "connector_error"
        # Never raise: an exception message may hold a value.
        except Exception:
            error = "connector_unavailable"
        if error is None and not _conforms(tool, data):
            error = "connector_unavailable"
        if error is not None:
            return self._finish(tool, digest, error=error)
        return self._finish(tool, digest, data=_as_data(data), executed=True)

    def _read(self, model: BaseModel) -> object:
        if isinstance(model, GetOrderArgs):
            return self._erp.get_order(model.order_id)
        if isinstance(model, GetAssetArgs):
            return self._gmao.get_asset(model.asset_id)
        if isinstance(model, ListWorkOrdersArgs):
            return self._gmao.list_work_orders(
                model.asset_id, model.symptom_code, model.since_days, model.limit
            )
        return self._gmao.failure_catalog()

    def _write(
        self, tool: ToolName, model: BaseModel, digest: str, token: ApprovalToken | None
    ) -> ToolResult:
        if token is None:
            return self._finish(tool, digest, error="no_token")
        # Checked and spent in one atomic step: one token, at most one execution attempt.
        verdict = self._verifier.redeem(token, tool, digest)
        if verdict != "ok":
            return self._finish(tool, digest, error=_TOKEN_REFUSALS[verdict])
        error: ToolError | None = None
        receipt: object = None
        try:
            if isinstance(model, RecordReplyPreparedArgs):
                receipt = self._erp.record_reply_prepared(
                    model.order_id, model.draft_sha256, token=token
                )
            elif isinstance(model, CreateWorkRequestArgs):
                receipt = self._gmao.create_work_request(model, token=token)
            else:  # pragma: no cover - the registry has exactly these two writes
                raise TypeError
        except ConnectorError:
            error = "connector_error"
        # Never raise: an exception message may hold a value.
        except Exception:
            error = "connector_unavailable"
        if error is None and not _conforms(tool, receipt):
            error = "connector_unavailable"
        if error is not None:
            return self._finish(tool, digest, error=error, approved=True)
        return self._finish(tool, digest, data=_as_data(receipt), approved=True, executed=True)


def _as_data(data: object) -> ToolData:
    # Only called after _conforms: the value is one of the ToolData types.
    return cast(ToolData, data)


def _known(name: object) -> ToolName | None:
    if not isinstance(name, str):
        return None
    for tool in TOOLS:
        # ``str.__eq__`` compares the content: a ``str`` subclass cannot lie or raise here.
        if str.__eq__(name, tool) is True:
            return tool
    return None
