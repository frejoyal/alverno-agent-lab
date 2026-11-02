"""Approval-script driver of the module 3 evaluation (I5 spec decision 7, ADR 0015).

The safety counts of ADR 0015 are read from receipts, never from what a model says it
did. :class:`ApprovalScriptDriver` runs one item through an
:class:`~alverno_lab.agents.ops.runtime.OpsEnvelope` and collects those receipts into
one :class:`ItemRun`:

- the turns the envelope returned (their kinds, in order);
- the access-log records the item wrote (:class:`OpsAccessLogRecord`, in order);
- the probe timeline (log writes, executor calls, connector effects, model calls);
- the executor's :class:`ToolCallRecord` list and the simulators' executed writes
  (:class:`SideEffectRecord`);
- the threads left in the checkpointer, and the connector and model call counts.

An item runs as an operator would: ``start``; then, while a draft is pending and the
script has steps, ``decide(step)`` naming the draft shown (``draft_id``); a script
exhausted while a draft is still pending ends with ``abandon()``. The driver never mints
a token: only the envelope does, after its decision record. A 3b structured edit
(``edited_args``) is merged over the presented arguments, so a gold step names only the
fields it changes; a step whose ``decision`` is not ``approve``, ``edit`` or ``reject``
is passed as written, for the envelope to turn into ``invalid_decision``.

Each item reads its receipts as slices taken after the marks set before it starts, so a
rig shared by several items still gives per-item receipts. An :class:`ItemRun` holds no
request; its served texts stay inside the turns and the trace, both out of ``repr``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from pydantic import JsonValue

from alverno_lab.agents.ops.probes import (
    CountingChat,
    CountingConnector,
    CountingExecutor,
    Timeline,
    TimelineEvent,
    TimelineLog,
)
from alverno_lab.agents.ops.runtime import OpsEnvelope, OpsRuntimeError, OpsTurn, OpsTurnKind
from alverno_lab.connectors.types import SideEffectRecord
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord
from alverno_lab.privacy.ops_types import OpsFlag
from alverno_lab.tools.executor import ToolCallRecord

__all__ = [
    "DECISIONS",
    "UNKNOWN_ERROR",
    "ApprovalScriptDriver",
    "EffectSource",
    "ItemRun",
    "OpsRig",
    "ScriptStep",
]

ScriptStep = Mapping[str, JsonValue]
DECISIONS: frozenset[str] = frozenset({"approve", "edit", "reject"})
# The envelope's errors are ``<step>:<rule>``; anything else is recorded as this code.
UNKNOWN_ERROR = "unknown:error"
_ERROR_CODE = re.compile(r"[a-z_]{1,20}:[a-z_]{1,40}")


class EffectSource(Protocol):
    """A simulated connector that keeps its executed writes (``SimErp``, ``SimGmao``)."""

    @property
    def executed(self) -> Sequence[SideEffectRecord]: ...


@dataclasses.dataclass(frozen=True)
class OpsRig:
    """One envelope and the probes around its parts, for the driver to read.

    ``log`` must be the envelope's log, and ``timeline`` the timeline its probes share.
    ``sims`` are the simulated connectors whose ``executed`` lists hold the real side
    effects; ``chat`` is the counted model, when the mode has one.
    """

    envelope: OpsEnvelope
    log: TimelineLog
    timeline: Timeline
    executor: CountingExecutor
    connector: CountingConnector
    sims: tuple[EffectSource, ...] = ()
    chat: CountingChat | None = None

    def __post_init__(self) -> None:
        if self.envelope.log is not self.log or self.log.timeline is not self.timeline:
            raise ValueError("ops_rig:log")
        if self.executor.timeline is not self.timeline:
            raise ValueError("ops_rig:executor")
        if self.connector.timeline is not self.timeline:
            raise ValueError("ops_rig:connector")


@dataclasses.dataclass(frozen=True)
class ItemRun:
    """The receipts of one item; ``repr`` shows ids, kinds, codes and counts only.

    ``screen_refused`` is true when the first turn is a refusal that ran no node: an
    empty trace, which only the request screen gives (every node appends a trace
    entry). ``error`` is the envelope's ``<step>:<rule>`` code when a step raised.
    ``auto_send_trap`` comes from the gold item, never from the system under test: it is
    the denominator of ``auto_send_held_rate`` (ADR 0015 « Oracle indépendant »).
    ``trace`` is the most complete scrubbed trace of the item (the last non-empty one;
    the state's trace only grows within a thread).
    """

    item_id: str
    screen_refused: bool = False
    kinds: tuple[OpsTurnKind, ...] = ()
    flags: tuple[OpsFlag, ...] = ()
    records: tuple[OpsAccessLogRecord, ...] = ()
    events: tuple[TimelineEvent, ...] = ()
    calls: tuple[ToolCallRecord, ...] = ()
    side_effects: tuple[SideEffectRecord, ...] = ()
    residual_threads: int = 0
    connector_calls: int = 0
    chat_calls: int = 0
    error: str | None = None
    auto_send_trap: bool = False
    turns: tuple[OpsTurn, ...] = dataclasses.field(default=(), repr=False)
    trace: tuple[dict[str, JsonValue], ...] = dataclasses.field(default=(), repr=False)


@dataclasses.dataclass(frozen=True)
class _Marks:
    """Where each receipt list stood before the item started."""

    records: int
    events: int
    calls: int
    sims: tuple[int, ...]
    connector: int
    chat: int

    @classmethod
    def of(cls, rig: OpsRig) -> _Marks:
        return cls(
            records=len(rig.log.records),
            events=len(rig.timeline),
            calls=len(rig.executor.records),
            sims=tuple(len(sim.executed) for sim in rig.sims),
            connector=rig.connector.total,
            chat=rig.chat.attempts if rig.chat is not None else 0,
        )


def _error_code(exc: OpsRuntimeError) -> str:
    text = str(exc)
    return text if _ERROR_CODE.fullmatch(text) is not None else UNKNOWN_ERROR


def _last_trace(turns: Sequence[OpsTurn]) -> tuple[dict[str, JsonValue], ...]:
    for turn in reversed(turns):
        if turn.trace:
            return turn.trace
    return ()


def _screen_refused(turns: Sequence[OpsTurn]) -> bool:
    if not turns:
        return False
    first = turns[0]
    return first.kind == "refused" and not first.trace and first.draft_id is None


class ApprovalScriptDriver:
    """Runs items through envelopes built by ``envelope_factory`` (one call per item)."""

    def __init__(self, envelope_factory: Callable[[], OpsRig]) -> None:
        self._factory = envelope_factory

    def run(
        self,
        item_id: str,
        request: str,
        script: Sequence[ScriptStep],
        *,
        auto_send_trap: bool = False,
    ) -> ItemRun:
        """Start ``request``, play ``script`` while a draft is pending, then collect.

        Each decision names the draft it was shown (``draft_id``), as the CLI does.
        ``auto_send_trap`` is the gold item's family marker, kept on the :class:`ItemRun`.
        """
        rig = self._factory()
        if not isinstance(rig, OpsRig):
            raise TypeError("ApprovalScriptDriver:rig_required")
        envelope = rig.envelope
        marks = _Marks.of(rig)
        turns: list[OpsTurn] = []
        error: str | None = None
        try:
            turns.append(envelope.start(request))
            for step in script:
                if not envelope.pending:
                    break
                shown = turns[-1].draft_id
                turns.append(envelope.decide(self._value(envelope, step), draft_id=shown))
        except OpsRuntimeError as exc:
            # The envelope's message is a code; the exception itself is not kept.
            error = _error_code(exc)
        if envelope.pending:
            # The script ran out while a draft waits: the operator walked away.
            turns.append(envelope.abandon())
        run = self._collect(item_id, rig, marks, turns, error)
        return dataclasses.replace(run, auto_send_trap=auto_send_trap)

    @staticmethod
    def _value(envelope: OpsEnvelope, step: ScriptStep) -> object:
        """The value passed to ``decide``: the step, with a structured edit merged."""
        value: dict[str, JsonValue] = dict(step)
        edited = value.get("edited_args")
        action = envelope.presented_action
        if value.get("decision") == "edit" and isinstance(edited, dict) and action is not None:
            # A partial override of the presented arguments (3b); never a token.
            value["edited_args"] = {**action.args, **edited}
        return value

    @staticmethod
    def _collect(
        item_id: str,
        rig: OpsRig,
        marks: _Marks,
        turns: Sequence[OpsTurn],
        error: str | None,
    ) -> ItemRun:
        effects: list[SideEffectRecord] = []
        for sim, mark in zip(rig.sims, marks.sims, strict=True):
            effects.extend(sim.executed[mark:])
        chat = rig.chat.attempts - marks.chat if rig.chat is not None else 0
        return ItemRun(
            item_id=item_id,
            screen_refused=_screen_refused(turns),
            kinds=tuple(turn.kind for turn in turns),
            flags=turns[0].flags if turns else (),
            records=tuple(rig.log.records[marks.records :]),
            events=rig.timeline.events[marks.events :],
            calls=tuple(rig.executor.records[marks.calls :]),
            side_effects=tuple(effects),
            residual_threads=rig.envelope.thread_count(),
            connector_calls=rig.connector.total - marks.connector,
            chat_calls=chat,
            error=error,
            turns=tuple(turns),
            trace=_last_trace(turns),
        )
