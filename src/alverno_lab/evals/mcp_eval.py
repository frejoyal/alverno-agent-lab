"""Evaluation of the MCP server (profile ``mcp-fake``, ADR 0020 « 5a — Jeux et mesures »).

An in-memory SDK client calls the tools, in ``--fake`` mode only:

- **Parity.** Every question of ``quality_gold`` goes through ``quality_search`` and
  ``quality_ask``, every question of ``hr_gold`` through ``hr_policy_ask``; each result is
  compared with the direct call of the same backend (retriever, Quality agent, HR agent).
- **Protocol traps.** Each item of ``mcp_gold`` is called with the module 3 tools closed
  or open, as the item says; the error code is compared with the expected one, and the
  backend calls during the trap are counted.
- **Structure.** ``tools/list`` in both configurations: no write tool, no module 3 tool
  without the option. A probe counts ``socket.bind`` and ``socket.listen`` calls over the
  whole run.

The report holds item ids, codes and counts, never a question or an answer.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client
from pydantic import BaseModel, ConfigDict, Field

from alverno_lab.agents.hr.graph import HrAgent
from alverno_lab.config import Settings
from alverno_lab.evals.canaries import load_canaries
from alverno_lab.evals.hr import thresholds_sha256
from alverno_lab.evals.ops_eval_common import artifact_hits, max_rate, rate, run_files
from alverno_lab.evals.thresholds import block_sha256
from alverno_lab.hr_pipeline import build_hr_agent
from alverno_lab.mcp_server.adapters import Backends, build_backends
from alverno_lab.mcp_server.server import OPS_TOOLS, build_server
from alverno_lab.privacy.access_log import AccessLog
from alverno_lab.privacy.canary import canary_in
from alverno_lab.tools.registry import WRITE_TOOLS

__all__ = [
    "FIRST_DIR_PROFILE",
    "LAST_DIR_PROFILE",
    "McpEvalReport",
    "McpTrapResult",
    "dir_thresholds_sha256",
    "run_mcp_eval",
    "write_mcp_report",
]

PROFILE = "mcp-fake"
FIRST_DIR_PROFILE = "mcp-fake:"
LAST_DIR_PROFILE = "dir-full-ollama:"
FRAGMENT = 12
# Names of tools that would write or approve: the module 3 write tools, their flows and
# the approval step (ADR 0013). None may ever be listed.
WRITE_NAMES = frozenset(
    {*WRITE_TOOLS, "customer_reply", "work_request", "create_work_request", "approve"}
)


def dir_thresholds_sha256(path: Path) -> str:
    """``dir_thresholds_sha256``: the ``mcp-fake`` to ``dir-full-ollama`` range (ADR 0020)."""
    return block_sha256(path, FIRST_DIR_PROFILE, LAST_DIR_PROFILE)


class McpTrapResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    family: str
    expected_error: str
    got_error: str | None
    backend_calls: int


class McpEvalReport(BaseModel):
    """What ``alverno mcp eval`` writes: counts and codes only."""

    profile: str = PROFILE
    timestamp: str
    metrics: dict[str, float]
    n_parity_items: int
    parity_mismatch_ids: list[str] = Field(default_factory=list)
    traps: list[McpTrapResult] = Field(default_factory=list)
    tools_default: list[str] = Field(default_factory=list)
    tools_ops: list[str] = Field(default_factory=list)
    dataset_sha256: dict[str, str] = Field(default_factory=dict)
    thresholds_sha256: str = ""
    dir_thresholds_sha256: str = ""


# --------------------------------------------------------------------------- probes
@contextlib.contextmanager
def listener_probe() -> Iterator[list[str]]:
    """Count ``socket.bind`` and ``socket.listen`` calls while the block runs.

    Calls made inside ``socket.socketpair`` are not counted: on Windows, Python emulates
    a socket pair (the asyncio event loop's self-pipe) with a loopback listener on an
    ephemeral port, closed as soon as the pair is connected. It serves nothing.
    """
    calls: list[str] = []
    depth = [0]
    original_bind, original_listen = socket.socket.bind, socket.socket.listen
    original_pair = socket.socketpair

    def bind(self: socket.socket, *args: Any) -> None:
        if not depth[0]:
            calls.append("bind")
        original_bind(self, *args)

    def listen(self: socket.socket, *args: Any) -> None:
        if not depth[0]:
            calls.append("listen")
        original_listen(self, *args)

    def socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        depth[0] += 1
        try:
            return original_pair(*args, **kwargs)
        finally:
            depth[0] -= 1

    socket.socket.bind = bind  # type: ignore[method-assign,assignment]
    socket.socket.listen = listen  # type: ignore[method-assign,assignment]
    socket.socketpair = socketpair
    try:
        yield calls
    finally:
        socket.socket.bind = original_bind  # type: ignore[method-assign]
        socket.socket.listen = original_listen  # type: ignore[method-assign]
        socket.socketpair = original_pair


class CountingBackends(Backends):
    """The backends, with a count of the calls that reached them."""

    calls: int = 0

    def _count[R](self, call: Callable[[], R]) -> R:
        self.calls += 1
        return call()

    def quality_search(self, query: str, k: int) -> Any:
        return self._count(lambda: super(CountingBackends, self).quality_search(query, k))

    def quality_ask(self, question: str) -> Any:
        return self._count(lambda: super(CountingBackends, self).quality_ask(question))

    def hr_policy_ask(self, question: str) -> Any:
        return self._count(lambda: super(CountingBackends, self).hr_policy_ask(question))

    def order_status(self, args: Any) -> Any:
        return self._count(lambda: super(CountingBackends, self).order_status(args))

    def machine_diagnosis(self, args: Any) -> Any:
        return self._count(lambda: super(CountingBackends, self).machine_diagnosis(args))


def _counting(backends: Backends) -> CountingBackends:
    return CountingBackends(
        retriever=backends.retriever,
        quality=backends.quality,
        hr=backends.hr,
        service=backends.service,
        maint=backends.maint,
    )


# --------------------------------------------------------------------------- helpers
def _jsonl(path: Path) -> list[dict[str, Any]]:
    # Any: decoded JSON lines.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fragments(text: str) -> set[str]:
    text = " ".join(text.split())
    return {text[i : i + FRAGMENT] for i in range(max(0, len(text) - FRAGMENT + 1))}


def _log_channels(directory: Path, prefix: str) -> list[str]:
    channels: list[str] = []
    for path in sorted(directory.glob(f"{prefix}-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                channels.append(str(json.loads(line).get("channel")))
    return channels


def _structured(result: Any) -> dict[str, Any]:
    content = getattr(result, "structured_content", None)
    return content if isinstance(content, dict) else {}


# --------------------------------------------------------------------------- run
def run_mcp_eval(
    settings: Settings, *, out_dir: Path, stem: str = "eval-mcp-fake"
) -> McpEvalReport:
    """Run the 5a evaluation in ``--fake`` mode and write its reports under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    access = out_dir / "access"
    run_settings = settings.model_copy(update={"access_log_dir": access})
    quality_items = _jsonl(settings.eval_path)
    hr_items = _jsonl(settings.hr_eval_path)
    traps = _jsonl(settings.mcp_eval_path)
    hr_canaries = [c.canary for c in load_canaries(settings.hr_eval_path)]

    def hr_factory(s: Settings) -> HrAgent:
        log = AccessLog(access, retention_days=s.access_log_retention_days)
        return build_hr_agent(s, fake=True, access_log=log)

    with listener_probe() as listeners:
        base = _counting(build_backends(run_settings, fake=True, hr_factory=hr_factory))
        ops_settings = run_settings.model_copy(update={"mcp_ops": True})
        ops = _counting(build_backends(ops_settings, fake=True, hr_factory=hr_factory))
        outcome = anyio.run(
            _drive, base, ops, run_settings, quality_items, hr_items, traps, hr_canaries, access
        )

    errors_text = outcome["errors_text"]
    tools_default: list[str] = outcome["tools_default"]
    tools_ops: list[str] = outcome["tools_ops"]
    trap_results: list[McpTrapResult] = outcome["traps"]
    rejected = sum(t.got_error is not None for t in trap_results)
    right_code = sum(t.got_error == t.expected_error for t in trap_results)
    n_parity = 2 * len(quality_items) + len(hr_items)
    report = McpEvalReport(
        timestamp=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%MZ"),
        metrics={},
        n_parity_items=n_parity,
        parity_mismatch_ids=outcome["mismatches"],
        traps=trap_results,
        tools_default=tools_default,
        tools_ops=tools_ops,
        dataset_sha256={
            p.name: _sha(p)
            for p in (settings.eval_path, settings.hr_eval_path, settings.mcp_eval_path)
        },
        thresholds_sha256=thresholds_sha256(settings.thresholds_path),
        dir_thresholds_sha256=dir_thresholds_sha256(settings.thresholds_path),
    )
    write_mcp_report(report, out_dir, stem)
    questions = [i["question"] for i in (*quality_items, *hr_items)] + [
        str(v) for t in traps for v in t["arguments"].values() if isinstance(v, str)
    ]
    fragments = set().union(*(_fragments(q) for q in questions))
    texts = [p.read_text(encoding="utf-8", errors="replace") for p in run_files(out_dir)]
    texts.append(errors_text)
    log_hits = sum(1 for text in texts for frag in fragments if frag in text)
    ops_names = {t.name for t in OPS_TOOLS}
    write_listed = {n for n in (*tools_default, *tools_ops) if n in WRITE_NAMES}
    metrics: dict[str, float] = {
        "exposed_write_tools": len(write_listed) + outcome["not_read_only"],
        "ops_tools_exposed_by_default": len(ops_names & set(tools_default)),
        "network_listeners": len(listeners),
        "parity_mismatches": len(outcome["mismatches"]),
        "protocol_trap_reached_backend": sum(t.backend_calls for t in trap_results),
        "log_content_hits": log_hits,
        "answer_leak_count": outcome["leaks"],
        "artifact_canary_hits": artifact_hits(out_dir, hr_canaries),
        "channel_mismatch": outcome["channel_mismatch"],
        "error_rate": max_rate(outcome["unexpected_errors"], n_parity),
        "protocol_trap_rejection_rate": rate(rejected, len(trap_results)),
        "protocol_reason_accuracy": rate(right_code, len(trap_results)),
        "n_parity_items": n_parity,
        "n_protocol_traps": len(trap_results),
        "n_canary_items": sum(bool(i.get("canaries")) for i in hr_items),
    }
    report = report.model_copy(update={"metrics": metrics})
    write_mcp_report(report, out_dir, stem)
    return report


async def _drive(
    base: CountingBackends,
    ops: CountingBackends,
    settings: Settings,
    quality_items: list[dict[str, Any]],
    hr_items: list[dict[str, Any]],
    traps: list[dict[str, Any]],
    hr_canaries: list[Any],
    access: Path,
) -> dict[str, Any]:
    mismatches: list[str] = []
    unexpected = 0
    leaks = 0
    channel_mismatch = 0
    errors: list[str] = []
    trap_results: list[McpTrapResult] = []
    ops_settings = settings.model_copy(update={"mcp_ops": True})
    async with (
        Client(build_server(base, settings)) as client,
        Client(build_server(ops, ops_settings)) as ops_client,
    ):
        listed = (await client.list_tools()).tools
        listed_ops = (await ops_client.list_tools()).tools
        not_read_only = sum(
            not (t.annotations is not None and t.annotations.read_only_hint)
            for t in (*listed, *listed_ops)
        )
        for item in quality_items:
            question = item["question"]
            hits = base.retriever.search(question, top_k=5)
            got = await client.call_tool("quality_search", {"query": question, "k": 5})
            served = [h["chunk_id"] for h in _structured(got).get("hits", [])]
            unexpected += bool(got.is_error)
            if served != [h.chunk.chunk_id for h in hits]:
                mismatches.append(f"{item['id']}:quality_search")
            direct = base.quality.ask(question)
            got = await client.call_tool("quality_ask", {"question": question})
            unexpected += bool(got.is_error)
            s = _structured(got)
            same = (s.get("answer"), s.get("refused"), s.get("refusal_reason"), s.get("citations"))
            if same != (direct.answer, direct.refused, direct.refusal_reason, direct.citations):
                mismatches.append(f"{item['id']}:quality_ask")
        for item in hr_items:
            question = item["question"]
            direct_hr = base.hr.ask(question, channel="eval")
            before = len(_log_channels(access, "hr"))
            got = await client.call_tool("hr_policy_ask", {"question": question})
            after = _log_channels(access, "hr")[before:]
            channel_mismatch += sum(c != "mcp" for c in after) + (not after)
            unexpected += bool(got.is_error)
            s = _structured(got)
            same = (s.get("answer"), s.get("refused"), s.get("refusal_reason"), s.get("citations"))
            expected = (
                direct_hr.answer,
                direct_hr.refused,
                direct_hr.refusal_reason,
                direct_hr.citations,
            )
            if same != expected:
                mismatches.append(f"{item['id']}:hr_policy_ask")
            text = json.dumps(s, ensure_ascii=False)
            leaks += any(canary_in(c, text) for c in hr_canaries)
        for trap in traps:
            target, backends = (ops_client, ops) if trap["ops_enabled"] else (client, base)
            calls = backends.calls
            got = await target.call_tool(trap["tool"], trap["arguments"])
            errors.append(json.dumps(_structured(got), ensure_ascii=False))
            code = _structured(got).get("error") if got.is_error else None
            trap_results.append(
                McpTrapResult(
                    id=trap["id"],
                    family=trap["family"],
                    expected_error=trap["expected_error"],
                    got_error=code if isinstance(code, str) else None,
                    backend_calls=backends.calls - calls,
                )
            )
    return {
        "mismatches": mismatches,
        "unexpected_errors": unexpected,
        "leaks": leaks,
        "channel_mismatch": channel_mismatch,
        "errors_text": "\n".join(errors),
        "traps": trap_results,
        "tools_default": [t.name for t in listed],
        "tools_ops": [t.name for t in listed_ops],
        "not_read_only": not_read_only,
    }


def write_mcp_report(report: McpEvalReport, out_dir: Path, stem: str) -> None:
    """Write ``<stem>.json`` and a short ``<stem>.md`` (codes and counts only)."""
    (out_dir / f"{stem}.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [f"# {report.profile}", "", f"- timestamp: {report.timestamp}"]
    lines += [f"- {k}: {v}" for k, v in report.metrics.items()]
    # Codes only: prose labels could match a fragment of a question (log_content_hits).
    lines += ["", "- tools_default: " + ", ".join(report.tools_default)]
    lines += ["- tools_mcp_ops: " + ", ".join(report.tools_ops), ""]
    (out_dir / f"{stem}.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
