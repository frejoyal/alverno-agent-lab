"""Builds the management orchestrator and its probes (ADR 0018, ADR 0020).

``tools`` mode: the overview sections are the facts blocks alone (no model); the Quality
and HR sections come from their module (with ``--fake``, the module fakes). ``full`` mode:
a model writes one summary paragraph per overview section (with ``--fake``,
:func:`summary_echo`, which repeats the block's first count line).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from alverno_lab.agents.direction.graph import DirectionAgent
from alverno_lab.agents.ops.probes import ProbeChat
from alverno_lab.config import Settings
from alverno_lab.connectors.sim import SimErp, SimGmao
from alverno_lab.connectors.types import MaintenanceOverview, OrdersOverview
from alverno_lab.hr_pipeline import build_hr_agent, check_hr_local
from alverno_lab.llm import FakeChat
from alverno_lab.ops_pipeline import load_ops_context, ops_quarantine
from alverno_lab.pipeline import build_agent, make_chat
from alverno_lab.privacy.access_log import AccessLog
from alverno_lab.privacy.direction_access_log import DirectionAccessLogRecord
from alverno_lab.privacy.types import Channel

__all__ = [
    "FAKE_DIRECTION_MODEL",
    "DirectionMode",
    "DirectionRig",
    "build_direction_rig",
    "direction_log",
    "summary_echo",
]

DirectionMode = Literal["tools", "full"]
FAKE_DIRECTION_MODEL = "fake-direction-echo"
_BLOCK = re.compile(r"\[début du bloc\]\n(?P<block>.*?)\n\[fin du bloc\]", re.DOTALL)


def summary_echo(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """Fake summary: the block's first count line, as one sentence (reads the prompt only)."""
    del system, schema
    match = _BLOCK.search(user)
    lines = match.group("block").splitlines() if match else []
    counts = [line[2:] for line in lines if line.startswith("- ")]
    return {"summary": f"En bref : {counts[0]}." if counts else ""}


@dataclass
class _Counter:
    calls: int = 0


@dataclass
class _CountingSource:
    """An overview source whose reads are counted (``refused_reached_backend``)."""

    inner: Any  # SimErp or SimGmao: both have as_of and one overview read
    counter: _Counter

    @property
    def as_of(self) -> dt.date:
        value: dt.date = self.inner.as_of
        return value

    def orders_overview(self, account_id: str | None) -> OrdersOverview:
        self.counter.calls += 1
        result: OrdersOverview = self.inner.orders_overview(account_id)
        return result

    def maintenance_overview(
        self, asset_id: str | None, start: dt.date, end: dt.date
    ) -> MaintenanceOverview:
        self.counter.calls += 1
        result: MaintenanceOverview = self.inner.maintenance_overview(asset_id, start, end)
        return result


@dataclass
class DirectionRig:
    """The agent and what the evaluation reads: backend calls and the summary prompts."""

    agent: DirectionAgent
    counter: _Counter
    chat: ProbeChat | None = None
    prompts: list[tuple[str, str]] = field(default_factory=list)


def direction_log(settings: Settings) -> AccessLog[DirectionAccessLogRecord]:
    """The ``direction`` access log in ``access_log_dir``, with its retention."""
    return AccessLog(
        settings.access_log_dir,
        prefix="direction",
        record_type=DirectionAccessLogRecord,
        retention_days=settings.access_log_retention_days,
    )


def build_direction_rig(
    settings: Settings,
    *,
    mode: DirectionMode,
    fake: bool,
    channel: Channel,
    model: str | None = None,
    hr_log: AccessLog | None = None,
) -> DirectionRig:
    """The orchestrator over the module 1 to 3 engines, with its probes."""
    check_hr_local(settings, model=model, fake=fake)
    counter = _Counter()
    quality = build_agent(settings, fake=fake, model=model)
    hr = build_hr_agent(settings, fake=fake, model=model, access_log=hr_log)
    quarantine = ops_quarantine(load_ops_context(settings, "service"))
    erp = SimErp.from_yaml(settings.ops_erp_path, quarantine=quarantine)
    gmao = SimGmao.from_yaml(settings.ops_gmao_path, quarantine=quarantine)
    chat: ProbeChat | None = None
    if mode == "full":
        inner = (
            FakeChat(responder=summary_echo, model=FAKE_DIRECTION_MODEL)
            if fake
            else make_chat(settings, fake=False, model=model)
        )
        chat = ProbeChat(inner)

    def counted(call: Callable[[str], Any]) -> Callable[[str], Any]:
        def run(question: str) -> Any:
            counter.calls += 1
            return call(question)

        return run

    agent = DirectionAgent(
        quality=counted(quality.ask),
        hr=counted(lambda q: hr.ask(q, channel=channel)),
        erp=_CountingSource(erp, counter),
        gmao=_CountingSource(gmao, counter),
        log=direction_log(settings),
        channel=channel,
        summarizer=chat,
    )
    return DirectionRig(agent=agent, counter=counter, chat=chat)
