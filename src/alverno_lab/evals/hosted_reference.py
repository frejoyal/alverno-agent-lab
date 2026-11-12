"""Optional hosted reference of module 4 (docs/adr/0023): the Quality agent with its chat
model answered by ``claude -p`` (Haiku), on the lab's subscription and never in CI.

Only the chat call changes: retrieval, the "no source" gate and the citation check stay
local. Three barriers stand before anything leaves the workstation:

1. the opt-in ``ALVERNO_ALLOW_HOSTED_REFERENCE=true`` (otherwise nothing is called);
2. the items: answerable ``quality_gold`` items only, none with an expected ``SPEC-*``
   source, none whose retrieved extracts include a ``SPEC-*`` document;
3. the lab's personal information detector on the whole prompt, right before sending.

An audit, independent of the barriers, re-checks what was actually sent and fills the
four counts of ADR 0023; each count has a negative control that removes its barrier and
must push the count above 0. The controls use a simulated sender: they never call the
hosted model.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from alverno_lab.agents.quality.prompts import SYSTEM_PROMPT, build_user_prompt
from alverno_lab.evals.dataset import GoldItem
from alverno_lab.evals.runner import EvalReport
from alverno_lab.llm import LLMCall, LLMOutputError, LLMStats
from alverno_lab.ollama import OllamaError
from alverno_lab.privacy import PrivacyContext
from alverno_lab.privacy.detect import detect_pi
from alverno_lab.retrieval import HybridRetriever

T = TypeVar("T", bound=BaseModel)

ALLOW_ENV = "ALVERNO_ALLOW_HOSTED_REFERENCE"
# Reference name -> model alias of the Claude Code command line (ADR 0023).
REFERENCES: dict[str, str] = {"claude-haiku": "haiku"}
# An extract of a client specification: its header line ``[SPEC-...#nn]`` (prompts.py).
# A procedure that only names a specification is not one.
SPEC_EXTRACT_RE = re.compile(r"^\[SPEC-[A-Z0-9]+(?:-[A-Z0-9]+)*#", re.MULTILINE)
# Paid API credentials are never passed to the command line (lab rule: no paid key).
STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
TIMEOUT_S = 300


class HostedReferenceDisabled(RuntimeError):
    """The opt-in is missing, or the command line is not configured as ADR 0023 requires."""


class HostedBlocked(LLMOutputError):
    """A barrier stopped the call; the graph turns it into a refusal, nothing is sent."""


class HostedTransportError(OllamaError):
    """The command line failed: counted in ``error_rate``, like an Ollama timeout."""


def allowed(env: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if env is None else env).get(ALLOW_ENV, "")
    return value.strip().lower() == "true"


def has_spec(text: str) -> bool:
    """True when ``text`` holds an extract of a ``SPEC-*`` document."""
    return SPEC_EXTRACT_RE.search(text) is not None


def eligible(items: Sequence[GoldItem], *, keep_traps: bool = False) -> list[GoldItem]:
    """Answerable items without a ``SPEC-*`` expected source (barrier 2, first half)."""
    return [
        item
        for item in items
        if (keep_traps or not item.should_refuse)
        and not any(doc.startswith("SPEC-") for doc in item.expected_doc_ids)
    ]


@dataclasses.dataclass
class Audit:
    """What actually left, checked again after the barriers (ADR 0023 counts)."""

    calls: int = 0
    pi_sent: int = 0
    trap_sent: int = 0
    spec_sent: int = 0
    calls_default: int = 0
    pi_blocked: int = 0
    spec_skipped: int = 0

    def counts(self) -> dict[str, int]:
        return {
            "hosted_pi_sent": self.pi_sent,
            "hosted_trap_sent": self.trap_sent,
            "hosted_spec_sent": self.spec_sent,
            "hosted_calls_default": self.calls_default,
        }


@dataclasses.dataclass(frozen=True)
class Barriers:
    """Each barrier can be removed by a negative control, never by a command option."""

    opt_in: bool = True
    spec: bool = True
    pi: bool = True


ALL_BARRIERS = Barriers()
Sender = Callable[[Sequence[str], str], str]


def claude_argv(alias: str, system: str, schema: Mapping[str, Any]) -> list[str]:
    """The command line of one call: no tool, no MCP, no CLAUDE.md, no session kept."""
    return [
        "claude",
        "-p",
        "--safe-mode",
        "--disable-slash-commands",
        "--model",
        alias,
        "--tools",
        "",
        "--system-prompt",
        system,
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, ensure_ascii=False),
    ]


def cli_sender(argv: Sequence[str], stdin: str) -> str:
    """Run the command in an empty working directory, without paid API credentials."""
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    with tempfile.TemporaryDirectory(prefix="alverno-ref-") as empty:
        try:
            done = subprocess.run(
                list(argv),
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=empty,
                env=env,
                timeout=TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostedTransportError(f"claude command failed: {type(exc).__name__}") from exc
    if done.returncode != 0:
        raise HostedTransportError(f"claude command exited with {done.returncode}")
    return done.stdout


@dataclasses.dataclass(frozen=True)
class CliResult:
    structured: dict[str, Any]
    model: str
    version: str
    input_tokens: int
    output_tokens: int


def parse_cli_output(stdout: str) -> CliResult:
    """Read ``--output-format json``; refuse a run with tools, MCP servers or an API key."""
    try:
        messages = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HostedTransportError("claude output is not JSON") from exc
    if not isinstance(messages, list) or not messages:
        raise HostedTransportError("claude output is not a message list")
    init = next((m for m in messages if m.get("subtype") == "init"), None)
    result = next((m for m in reversed(messages) if m.get("type") == "result"), None)
    if init is None or result is None:
        raise HostedTransportError("claude output has no init or result message")
    if init.get("apiKeySource", "none") != "none":
        raise HostedReferenceDisabled("the claude command used an API key (lab rule: none)")
    if [t for t in init.get("tools", []) if t != "StructuredOutput"] or init.get("mcp_servers"):
        raise HostedReferenceDisabled("the claude command offered tools or MCP servers")
    if result.get("is_error") or result.get("subtype") != "success":
        raise HostedTransportError(f"claude result: {result.get('subtype')}")
    structured = result.get("structured_output")
    if not isinstance(structured, dict):
        raise LLMOutputError("claude result has no structured output")
    usage = result.get("usage") or {}
    return CliResult(
        structured=structured,
        model=str(init.get("model", "")),
        version=str(init.get("claude_code_version", "")),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


class HostedChat:
    """A :class:`~alverno_lab.llm.ChatModel` answered by the hosted reference."""

    def __init__(
        self,
        reference: str,
        *,
        ctx: PrivacyContext,
        audit: Audit,
        trap_questions: Sequence[str],
        opt_in: bool,
        sender: Sender = cli_sender,
        barriers: Barriers = ALL_BARRIERS,
    ) -> None:
        self.alias = REFERENCES[reference]
        self.reference = reference
        self.ctx = ctx
        self.audit = audit
        self.trap_questions = [q for q in trap_questions if q]
        self.opt_in = opt_in
        self.sender = sender
        self.barriers = barriers
        self.stats = LLMStats()
        self.seen_models: set[str] = set()
        self.seen_versions: set[str] = set()

    @property
    def model_name(self) -> str:
        return f"claude-cli:{self.alias}"

    def _has_pi(self, system: str, user: str) -> bool:
        return bool(detect_pi(f"{system}\n{user}", self.ctx, mode="document"))

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        if self.barriers.opt_in and not self.opt_in:
            raise HostedReferenceDisabled(f"{ALLOW_ENV}=true is required")
        if self.barriers.spec and has_spec(user):
            self.audit.spec_skipped += 1
            raise HostedBlocked("a SPEC-* extract is never sent")
        if self.barriers.pi and self._has_pi(system, user):
            self.audit.pi_blocked += 1
            raise HostedBlocked("personal information in the prompt: not sent")
        # Audit of what leaves, independent of the barriers above.
        self.audit.calls += 1
        self.audit.calls_default += 0 if self.opt_in else 1
        self.audit.spec_sent += int(has_spec(user))
        self.audit.pi_sent += int(self._has_pi(system, user))
        self.audit.trap_sent += int(any(q in user for q in self.trap_questions))
        started = time.perf_counter()
        out = parse_cli_output(
            self.sender(claude_argv(self.alias, system, schema.model_json_schema()), user)
        )
        self.seen_models.add(out.model)
        self.seen_versions.add(out.version)
        self.stats.record(
            LLMCall(
                latency_ms=(time.perf_counter() - started) * 1000,
                prompt_tokens=out.input_tokens,
                completion_tokens=out.output_tokens,
            )
        )
        try:
            return schema.model_validate(out.structured)
        except ValidationError as exc:
            raise LLMOutputError("structured output does not match the schema") from exc


def preflight(
    items: Sequence[GoldItem], retriever: HybridRetriever, ctx: PrivacyContext, audit: Audit
) -> list[GoldItem]:
    """Items whose prompt may leave (barrier 2, second half, and barrier 3).

    The retrieval is deterministic: the extracts checked here are those the agent will
    send. The same checks run again in :class:`HostedChat` before each call.
    """
    kept = []
    for item in items:
        hits = retriever.search(item.question)
        if any(h.chunk.doc_id.startswith("SPEC-") for h in hits):
            audit.spec_skipped += 1
            continue
        prompt = build_user_prompt(item.question, hits)
        if detect_pi(f"{SYSTEM_PROMPT}\n{prompt}", ctx, mode="document"):
            audit.pi_blocked += 1
            continue
        kept.append(item)
    return kept


# --------------------------------------------------------------------------- controls
class _Draft(BaseModel):
    answer: str
    citations: list[str]
    confidence: float


def simulated_sender(argv: Sequence[str], stdin: str) -> str:
    """Answers like the command line, without any network call (negative controls)."""
    return json.dumps(
        [
            {
                "type": "system",
                "subtype": "init",
                "tools": ["StructuredOutput"],
                "mcp_servers": [],
                "apiKeySource": "none",
                "model": "simulated",
                "claude_code_version": "simulated",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": {"answer": "", "citations": [], "confidence": 0.0},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ]
    )


# A synthetic e-mail address and telephone number, detected as personal information.
CONTROL_PI = "Écrire à prenom.nom@courriel.example ou au 819-555-0142."
CONTROL_SPEC = "[SPEC-AGN-01#01] Spécification du client (extrait simulé)."


def negative_controls(
    ctx: PrivacyContext, items: Sequence[GoldItem]
) -> dict[str, dict[str, int | bool]]:
    """Remove each barrier in turn: the matching count must rise above 0 (ADR 0023)."""
    traps = [i.question for i in items if i.should_refuse]
    results: dict[str, dict[str, int | bool]] = {}

    def chat(barriers: Barriers, opt_in: bool = True) -> tuple[HostedChat, Audit]:
        audit = Audit()
        model = HostedChat(
            "claude-haiku",
            ctx=ctx,
            audit=audit,
            trap_questions=traps,
            opt_in=opt_in,
            sender=simulated_sender,
            barriers=barriers,
        )
        return model, audit

    # Trap barrier: item selection with the traps kept.
    model, audit = chat(ALL_BARRIERS)
    for item in eligible(items, keep_traps=True)[:50]:
        if item.should_refuse:
            model.complete_json(SYSTEM_PROMPT, f"Question : {item.question}", _Draft)
            break
    results["hosted_trap_sent"] = {"count": audit.trap_sent}
    # SPEC barrier removed.
    model, audit = chat(Barriers(spec=False))
    model.complete_json(SYSTEM_PROMPT, CONTROL_SPEC, _Draft)
    results["hosted_spec_sent"] = {"count": audit.spec_sent}
    # PI barrier removed.
    model, audit = chat(Barriers(pi=False))
    model.complete_json(SYSTEM_PROMPT, CONTROL_PI, _Draft)
    results["hosted_pi_sent"] = {"count": audit.pi_sent}
    # Opt-in barrier removed.
    model, audit = chat(Barriers(opt_in=False), opt_in=False)
    model.complete_json(SYSTEM_PROMPT, "Question : contrôle", _Draft)
    results["hosted_calls_default"] = {"count": audit.calls_default}
    for value in results.values():
        value["failed_as_planned"] = int(value["count"]) > 0
    return results


# --------------------------------------------------------------------------- run
@dataclasses.dataclass
class ReferenceRun:
    """Result of one hosted reference run: informative, outside the rule of ADR 0024."""

    reference: str
    model: str
    cli_version: str
    n_eligible: int
    n_sent_items: int
    counts: dict[str, int]
    pi_blocked: int
    spec_skipped: int
    calls: int
    metrics: dict[str, float]
    controls: dict[str, dict[str, int | bool]]

    @property
    def passed(self) -> bool:
        """Every count at 0 and every negative control failing as planned."""
        return not any(self.counts.values()) and all(
            bool(c["failed_as_planned"]) for c in self.controls.values()
        )

    def to_json(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "passed": self.passed}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ReferenceRun:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def run_reference(
    reference: str,
    items: Sequence[GoldItem],
    retriever: HybridRetriever,
    ctx: PrivacyContext,
    *,
    min_confidence: float,
    sender: Sender = cli_sender,
    env: Mapping[str, str] | None = None,
) -> tuple[ReferenceRun, EvalReport]:
    """Run the Quality agent with the hosted chat on the eligible items.

    Raises :class:`HostedReferenceDisabled` before any call when the opt-in is missing.
    """
    from alverno_lab.agents.quality.graph import QualityAgent
    from alverno_lab.evals.runner import run_eval

    if reference not in REFERENCES:
        raise HostedReferenceDisabled(f"unknown reference {reference!r}")
    opt_in = allowed(env)
    if not opt_in:
        raise HostedReferenceDisabled(f"{ALLOW_ENV}=true is required; nothing was called")
    audit = Audit()
    chosen = eligible(items)
    kept = preflight(chosen, retriever, ctx, audit)
    chat = HostedChat(
        reference,
        ctx=ctx,
        audit=audit,
        trap_questions=[i.question for i in items if i.should_refuse],
        opt_in=opt_in,
        sender=sender,
    )
    agent = QualityAgent(retriever, chat, min_confidence=min_confidence)
    report = run_eval(
        agent,
        kept,
        "full",
        backend="claude-cli",
        label=reference,
        config={"reference": reference, "alias": REFERENCES[reference]},
    )
    run = ReferenceRun(
        reference=reference,
        model=", ".join(sorted(chat.seen_models)) or "none",
        cli_version=", ".join(sorted(chat.seen_versions)) or "none",
        n_eligible=len(chosen),
        n_sent_items=len(kept),
        counts=audit.counts(),
        pi_blocked=audit.pi_blocked,
        spec_skipped=audit.spec_skipped,
        calls=audit.calls,
        metrics={
            k: v
            for k, v in report.metrics.items()
            if k
            in (
                "key_fact_recall",
                "citation_validity_rate",
                "over_refusal_rate",
                "false_answer_rate",
                "error_rate",
                "latency_p50_ms",
                "latency_p95_ms",
            )
        },
        controls=negative_controls(ctx, items),
    )
    return run, report
