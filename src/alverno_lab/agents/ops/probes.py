"""Measurement probes of the module 3 evaluation (ADR 0015): counts and order, no value.

The evaluation wraps the real parts in these probes and reads what they recorded:

- :class:`Timeline` is one append-only sequence of :class:`TimelineEvent` shared by the
  probes of one item: log writes (``log``), executor calls (``call``), connector writes
  (``effect``) and model calls (``chat``). An event holds a kind, a name (an event, a
  tool or a model name), an action id, an argument digest, ``ok`` and a code: never a
  request, a draft, an argument value or a token's nonce or MAC. ``seq`` orders them,
  so « the ``approved`` record precedes the effect » is a comparison of two integers.
- :class:`TimelineLog` is an :class:`~alverno_lab.privacy.access_log.AccessLog` that
  adds a ``log`` event **after** each successful write; :class:`FailingLog` raises
  :class:`~alverno_lab.privacy.access_log.AccessLogError` once a given number of writes
  has succeeded, for the ``audit_unavailable`` tests.
- :class:`CountingConnector` stands for the ERP, the GMAO or both: it counts reads and
  writes and adds an ``effect`` event (tool, the token's action id, the digest of the
  arguments) **before** delegating each write.
- :class:`CountingExecutor` wraps :class:`~alverno_lab.tools.executor.ToolExecutor` and
  adds a ``call`` event after each call (tool, the token's action id or None, the
  argument digest, ``ok`` and the error code).
- :class:`CountingChat` counts model calls *before* delegating them (a copy of the HR
  attempt counter, not an import from ``agents/hr``); :class:`ProbeChat` keeps the
  ``(system, user)`` prompts in memory for the backend canary scan, and its ``repr``
  shows only their number.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.connectors.gmao import GmaoConnector
from alverno_lab.connectors.types import (
    CREATE_WORK_REQUEST_TOOL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    RECORD_REPLY_TOOL,
    AssetId,
    AssetView,
    ConnectorError,
    ContactView,
    FailureCodeView,
    OrderId,
    OrderView,
    ReplyPrepared,
    SymptomCode,
    WorkOrderView,
    WorkRequest,
    WriteReceipt,
    WriteToken,
    WriteToolName,
    args_sha256,
)
from alverno_lab.llm import ChatModel, LLMStats
from alverno_lab.privacy.access_log import (
    MAX_RETENTION_DAYS,
    MODEL_PATTERN,
    AccessLog,
    AccessLogError,
    utc_now,
)
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord
from alverno_lab.tools.authority import ApprovalToken
from alverno_lab.tools.executor import ToolCallRecord, ToolExecutor, ToolResult

__all__ = [
    "CountingChat",
    "CountingConnector",
    "CountingExecutor",
    "EventKind",
    "FailingLog",
    "ProbeChat",
    "Timeline",
    "TimelineEvent",
    "TimelineLog",
]

_M = TypeVar("_M", bound=BaseModel)

EventKind = Literal["log", "call", "effect", "chat"]

_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z_]{1,40}")
_NAME = re.compile(r"[a-z]+\.[a-z_]+|[a-z_]{1,40}")
_MODEL = re.compile(MODEL_PATTERN)
# A chat event whose model name does not fit the access-log pattern is named this.
CHAT_EVENT_NAME = "chat"


def _match(pattern: re.Pattern[str], value: object) -> str | None:
    return value if type(value) is str and pattern.fullmatch(value) is not None else None


@dataclasses.dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One probe event, as metadata only; malformed fields are refused at construction."""

    seq: int
    kind: EventKind
    name: str
    action_id: str | None = None
    args_sha256: str | None = None
    ok: bool | None = None
    detail_code: str | None = None

    def __post_init__(self) -> None:
        name_ok = (
            _match(_MODEL, self.name) is not None
            if self.kind == "chat"
            else _match(_NAME, self.name) is not None
        )
        if (
            type(self.seq) is not int
            or self.kind not in ("log", "call", "effect", "chat")
            or not name_ok
            or (self.action_id is not None and _match(_HEX32, self.action_id) is None)
            or (self.args_sha256 is not None and _match(_HEX64, self.args_sha256) is None)
            or (self.ok is not None and type(self.ok) is not bool)
            or (self.detail_code is not None and _match(_CODE, self.detail_code) is None)
        ):
            raise ValueError("timeline:invalid_event")


class Timeline:
    """An append-only, ordered list of :class:`TimelineEvent` (thread-safe)."""

    def __init__(self) -> None:
        self._events: list[TimelineEvent] = []
        self._lock = threading.Lock()

    def add(
        self,
        kind: EventKind,
        name: str,
        *,
        action_id: str | None = None,
        args_sha256: str | None = None,
        ok: bool | None = None,
        detail_code: str | None = None,
    ) -> TimelineEvent:
        """Append one event; ``seq`` is its position."""
        with self._lock:
            event = TimelineEvent(
                seq=len(self._events),
                kind=kind,
                name=name,
                action_id=action_id,
                args_sha256=args_sha256,
                ok=ok,
                detail_code=detail_code,
            )
            self._events.append(event)
            return event

    @property
    def events(self) -> tuple[TimelineEvent, ...]:
        """The events so far, in order (a copy)."""
        with self._lock:
            return tuple(self._events)

    def of_kind(self, kind: EventKind) -> tuple[TimelineEvent, ...]:
        """The events of one kind, in order."""
        return tuple(event for event in self.events if event.kind == kind)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# --------------------------------------------------------------------------- access log
class TimelineLog(AccessLog[OpsAccessLogRecord]):
    """The module 3 access log, plus a ``log`` event after each successful write.

    The event is named after the record's ``event``; it carries the action digest and the
    reason, never ``draft_id`` (not an action id). :attr:`records` keeps the written
    records in order, for the evaluation.
    """

    def __init__(
        self,
        directory: Path,
        *,
        prefix: Literal["service", "maint"],
        timeline: Timeline | None = None,
        retention_days: int = MAX_RETENTION_DAYS,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        super().__init__(
            directory,
            prefix=prefix,
            record_type=OpsAccessLogRecord,
            retention_days=retention_days,
            clock=clock,
        )
        self.timeline = timeline if timeline is not None else Timeline()
        self.records: list[OpsAccessLogRecord] = []

    def append(self, rec: OpsAccessLogRecord) -> None:
        """Write ``rec``, then record it; nothing is recorded when the write fails."""
        super().append(rec)
        self.records.append(rec)
        self.timeline.add(
            "log", rec.event, args_sha256=rec.action_args_sha256, detail_code=rec.reason
        )


class FailingLog(TimelineLog):
    """A :class:`TimelineLog` whose writes fail once ``fail_after`` writes have succeeded.

    ``fail_after=0`` fails the first write. A failed write raises
    :class:`~alverno_lab.privacy.access_log.AccessLogError`, writes nothing and records
    nothing.
    """

    def __init__(
        self,
        directory: Path,
        *,
        prefix: Literal["service", "maint"],
        fail_after: int = 0,
        timeline: Timeline | None = None,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        if type(fail_after) is not int or fail_after < 0:
            raise ValueError("failing_log:fail_after")
        super().__init__(directory, prefix=prefix, timeline=timeline, clock=clock)
        self.fail_after = fail_after
        self.failures = 0

    def append(self, rec: OpsAccessLogRecord) -> None:
        """Fail, or write and record as :class:`TimelineLog` does."""
        if len(self.records) >= self.fail_after:
            self.failures += 1
            raise AccessLogError("access log not writable: simulated failure")
        super().append(rec)


# --------------------------------------------------------------------------- connectors
def _action_id_of(token: object) -> str | None:
    """The token's action id when it is a well-formed one, else ``None``; never raises."""
    try:
        value = token.action_id  # type: ignore[attr-defined]  # any object may come in
    except Exception:
        return None
    return _match(_HEX32, value)


def _digest(build: Callable[[], BaseModel]) -> str | None:
    """:func:`args_sha256` of the payload ``build`` makes, or ``None`` when it fails."""
    try:
        return args_sha256(build())
    except Exception:
        return None


class CountingConnector:
    """Counting stand-in for the ERP, the GMAO or both (it satisfies both protocols).

    A side that was not given raises :class:`ConnectorError` (``counting:no_erp`` or
    ``counting:no_gmao``), which the executor reports as ``connector_error``. Every call
    is counted in :attr:`calls` by method name, and in :attr:`reads` or :attr:`writes`;
    a write adds its ``effect`` event before it is delegated.
    """

    def __init__(
        self,
        *,
        timeline: Timeline,
        erp: ErpConnector | None = None,
        gmao: GmaoConnector | None = None,
    ) -> None:
        self._erp = erp
        self._gmao = gmao
        self.timeline = timeline
        self.calls: dict[str, int] = {}
        self.reads = 0
        self.writes = 0

    def _count(self, method: str, *, write: bool = False) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1
        if write:
            self.writes += 1
        else:
            self.reads += 1

    def _need_erp(self) -> ErpConnector:
        if self._erp is None:
            raise ConnectorError("counting:no_erp")
        return self._erp

    def _need_gmao(self) -> GmaoConnector:
        if self._gmao is None:
            raise ConnectorError("counting:no_gmao")
        return self._gmao

    def _effect(self, tool: WriteToolName, token: object, digest: str | None) -> None:
        self.timeline.add("effect", tool, action_id=_action_id_of(token), args_sha256=digest)

    @property
    def total(self) -> int:
        """Every connector call, reads and writes."""
        return self.reads + self.writes

    # ERP
    def get_order(self, order_id: OrderId) -> OrderView | None:
        """Counted :meth:`ErpConnector.get_order`."""
        self._count("get_order")
        return self._need_erp().get_order(order_id)

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        """Counted :meth:`ErpConnector.get_order_contact`."""
        self._count("get_order_contact")
        return self._need_erp().get_order_contact(order_id)

    def carrier_label(self, code: str) -> str | None:
        """Counted :meth:`ErpConnector.carrier_label`."""
        self._count("carrier_label")
        return self._need_erp().carrier_label(code)

    def delay_label(self, code: str) -> str | None:
        """Counted :meth:`ErpConnector.delay_label`."""
        self._count("delay_label")
        return self._need_erp().delay_label(code)

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        """Counted :meth:`ErpConnector.record_reply_prepared`; its effect is recorded first."""
        self._count("record_reply_prepared", write=True)
        digest = _digest(lambda: ReplyPrepared(order_id=order_id, draft_sha256=draft_sha256))
        self._effect(RECORD_REPLY_TOOL, token, digest)
        return self._need_erp().record_reply_prepared(order_id, draft_sha256, token=token)

    # GMAO
    def get_asset(self, asset_id: AssetId) -> AssetView | None:
        """Counted :meth:`GmaoConnector.get_asset`."""
        self._count("get_asset")
        return self._need_gmao().get_asset(asset_id)

    def list_work_orders(
        self,
        asset_id: AssetId,
        symptom_code: SymptomCode | None,
        since_days: int = DEFAULT_HISTORY_DAYS,
        limit: int = DEFAULT_WORK_ORDER_LIMIT,
    ) -> tuple[WorkOrderView, ...]:
        """Counted :meth:`GmaoConnector.list_work_orders`."""
        self._count("list_work_orders")
        return self._need_gmao().list_work_orders(asset_id, symptom_code, since_days, limit)

    def failure_catalog(self) -> tuple[FailureCodeView, ...]:
        """Counted :meth:`GmaoConnector.failure_catalog`."""
        self._count("failure_catalog")
        return self._need_gmao().failure_catalog()

    def create_work_request(self, req: WorkRequest, *, token: WriteToken) -> WriteReceipt:
        """Counted :meth:`GmaoConnector.create_work_request`; its effect is recorded first."""
        self._count("create_work_request", write=True)
        digest = _digest(lambda: req)
        self._effect(CREATE_WORK_REQUEST_TOOL, token, digest)
        return self._need_gmao().create_work_request(req, token=token)


# --------------------------------------------------------------------------- executor
class CountingExecutor:
    """Wraps a :class:`ToolExecutor`; adds a ``call`` event after each call."""

    def __init__(self, inner: ToolExecutor, timeline: Timeline) -> None:
        if not isinstance(inner, ToolExecutor):
            raise TypeError("CountingExecutor:executor_required")
        self.inner = inner
        self.timeline = timeline
        self.calls = 0

    @property
    def records(self) -> list[ToolCallRecord]:
        """The inner executor's call records (one per call, in order)."""
        return self.inner.records

    def call(
        self, name: str, args: Mapping[str, object], *, token: ApprovalToken | None = None
    ) -> ToolResult:
        """:meth:`ToolExecutor.call`, then its ``call`` event."""
        self.calls += 1
        result = self.inner.call(name, args, token=token)
        record = self.inner.records[-1]
        self.timeline.add(
            "call",
            record.tool,
            action_id=_action_id_of(token) if token is not None else None,
            args_sha256=record.args_sha256 or None,
            ok=result.ok,
            detail_code=result.error,
        )
        return result


# --------------------------------------------------------------------------- chat
class CountingChat:
    """A chat model that counts calls *before* delegating them.

    A copy of the HR attempt counter (``agents/hr/graph.py``), without its answer repair:
    a call that fails still counts, since its prompt reached the model. With a timeline,
    each call also adds a ``chat`` event named after the model.
    """

    def __init__(self, inner: ChatModel, timeline: Timeline | None = None) -> None:
        self.inner = inner
        self.stats: LLMStats = inner.stats
        self.attempts = 0
        self.timeline = timeline

    @property
    def model_name(self) -> str:
        """The inner model's name."""
        return self.inner.model_name

    def complete_json(self, system: str, user: str, schema: type[_M]) -> _M:
        """Count, then delegate."""
        self.attempts += 1
        if self.timeline is not None:
            name = _match(_MODEL, self.inner.model_name) or CHAT_EVENT_NAME
            self.timeline.add("chat", name)
        return self.inner.complete_json(system, user, schema)


class ProbeChat:
    """A chat model that keeps every ``(system, user)`` prompt in memory, then delegates.

    The prompts are for the backend canary scan only; ``repr`` shows their number, never
    their text.
    """

    def __init__(self, inner: ChatModel) -> None:
        self.inner = inner
        self.stats: LLMStats = inner.stats
        self.prompts: list[tuple[str, str]] = []

    @property
    def model_name(self) -> str:
        """The inner model's name."""
        return self.inner.model_name

    def complete_json(self, system: str, user: str, schema: type[_M]) -> _M:
        """Record the prompt, then delegate."""
        self.prompts.append((system, user))
        return self.inner.complete_json(system, user, schema)

    def __repr__(self) -> str:
        return f"ProbeChat(prompts=<{len(self.prompts)} hidden>)"

    __str__ = __repr__
