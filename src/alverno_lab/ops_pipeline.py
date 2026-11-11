"""Factories of module 3 (ADR 0012 « Câblage »): privacy context, local check, 3a rig.

- :func:`load_ops_context` builds the ``PrivacyContext`` of the ops screens, the
  quarantine and the guards: the HR context (roles, HR role mailboxes, allowlist, HR
  corpus vocabulary, k) plus the business vocabulary of ``data/eval/ops/vocabulary.yaml``
  (every string of the file) and its mailboxes (the role mailboxes ``service-client@``
  and ``entretien@`` and the generic customer mailboxes) added to ``role_mailboxes``.
- :func:`check_ops_local` is R14 of the HR module, copied: no remote Ollama host and no
  Ollama cloud model, whatever the opt-in, and a model name the access log accepts.
- :func:`ops_quarantine` wraps :func:`~alverno_lab.privacy.quarantine.scrub_tool_text`
  into the connectors' ``Quarantine`` callable.
- :func:`build_service_rig` wires one 3a request runner: the simulated ERP (fresh, with
  the quarantine adapter), the counting connector, the executor and its counting
  wrapper, the approval authority (held by the envelope; the executor holds only its
  verifier), the scope index of every account (:func:`service_scope_index`), the service
  screen and refusal policy, the operator header (read from the contact by code, for
  display only) and the 3a graph. ``ops_max_edit_rounds`` goes to both the envelope and
  the ``approve`` node. The ``tools`` mode drafts with the ``TemplateDrafter`` and calls
  no model (its ``CountingChat`` must stay at 0 calls). The ``full`` mode drafts with the
  ``ChatDrafter`` on ``CountingChat(ProbeChat(chat))``: ``chat`` is the fake of
  :func:`~alverno_lab.agents.ops.fakes.make_ops_fake_chat` with ``fake=True``, else the
  local Ollama model of :func:`~alverno_lab.pipeline.make_chat` (after
  :func:`check_ops_local`). The envelope reads the model name and the attempt count from
  that same wrapper.

- :func:`build_maint_rig` wires one 3b request runner the same way: the simulated GMAO
  (fresh, with the quarantine adapter), the counting connector, the executor, the
  authority, the maintenance screen and refusal policy, the operator header
  (:func:`maint_header`: the asset's name and line and the cited work orders, read from
  state; no person) and the 3b graph with ``ops_history_days`` and
  ``ops_max_work_orders``. The ``tools`` mode diagnoses with the ``BM25Diagnoser`` and
  calls no model (its ``CountingChat`` must stay at 0 calls). The ``full`` mode
  diagnoses with the ``ChatDiagnoser`` on ``CountingChat(ProbeChat(chat))``, ``chat``
  chosen as in the 3a rig (the same ``fake-ops`` fake, whose responder answers a
  ``Diagnosis`` with ``ops_diagnose_echo``).

Nothing here reads a holdout set.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Literal

import yaml

from alverno_lab.agents.maintenance import (
    BM25Diagnoser,
    ChatDiagnoser,
    Diagnoser,
    build_maint_graph,
)
from alverno_lab.agents.ops.fakes import make_ops_fake_chat
from alverno_lab.agents.ops.probes import (
    CountingChat,
    CountingConnector,
    CountingExecutor,
    ProbeChat,
    Timeline,
    TimelineLog,
)
from alverno_lab.agents.ops.runtime import DraftHeader, OpsEnvelope, OpsRuntimeError
from alverno_lab.agents.ops.state import OpsState
from alverno_lab.agents.service import ChatDrafter, Drafter, TemplateDrafter, build_service_graph
from alverno_lab.config import RemoteOllamaError, Settings
from alverno_lab.connectors.erp import ErpConnector
from alverno_lab.connectors.sim import SimErp, SimGmao, account_scope_index
from alverno_lab.connectors.types import AssetView, Quarantine, QuarantinedText
from alverno_lab.evals.ops_harness import OpsRig
from alverno_lab.hr_pipeline import load_privacy_context
from alverno_lab.pipeline import make_chat
from alverno_lab.privacy import PrivacyConfigError, PrivacyContext, build_vocabulary
from alverno_lab.privacy.access_log import MODEL_PATTERN
from alverno_lab.privacy.ops_policy import MAINT_MAILBOX, SERVICE_MAILBOX, OpsRefusalPolicy
from alverno_lab.privacy.ops_screen import screen_maintenance, screen_service
from alverno_lab.privacy.quarantine import scrub_tool_text
from alverno_lab.privacy.scope import ScopeIndex, build_scope_index
from alverno_lab.privacy.types import Channel
from alverno_lab.tools.authority import ApprovalAuthority
from alverno_lab.tools.executor import ToolExecutor

__all__ = [
    "OpsMode",
    "OpsModule",
    "build_maint_rig",
    "build_service_rig",
    "check_ops_local",
    "load_ops_context",
    "maint_header",
    "ops_quarantine",
    "service_header",
    "service_scope_index",
]

OpsModule = Literal["service", "maint"]
OpsMode = Literal["tools", "full"]

_MAILBOX_KEYS = ("role_mailboxes", "customer_mailboxes")
_ROLE_MAILBOXES = (SERVICE_MAILBOX, MAINT_MAILBOX)


def _strings(node: object) -> Iterator[str]:
    """Every string leaf of a parsed YAML document (mapping keys excluded)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _read_vocabulary(settings: Settings) -> dict[str, object]:
    path = settings.ops_vocabulary_path
    raw: object = None
    rule: str | None = None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        rule = "unreadable"
    except yaml.YAMLError:
        rule = "invalid_yaml"
    if rule is None and not isinstance(raw, dict):
        rule = "not_a_mapping"
    if rule is not None or not isinstance(raw, dict):
        # Raised outside the except block, without a chain: no snippet of the file.
        raise PrivacyConfigError(f"{path}: vocabulary.{rule or 'not_a_mapping'}") from None
    return raw


def load_ops_context(settings: Settings, module: OpsModule) -> PrivacyContext:
    """The ops ``PrivacyContext`` (the same for ``service`` and ``maint``).

    Raise :class:`~alverno_lab.privacy.PrivacyConfigError` when the vocabulary file is
    missing, invalid or lacks one of the two ops role mailboxes.
    """
    if module not in ("service", "maint"):
        raise ValueError("ops_pipeline:module")
    hr = load_privacy_context(settings)
    raw = _read_vocabulary(settings)
    mailboxes = [m for key in _MAILBOX_KEYS for m in _strings(raw.get(key, []))]
    if any(box not in mailboxes for box in _ROLE_MAILBOXES):
        raise PrivacyConfigError(f"{settings.ops_vocabulary_path}: vocabulary.mailbox_missing")
    return replace(
        hr,
        vocabulary=hr.vocabulary | build_vocabulary(_strings(raw)),
        role_mailboxes=hr.role_mailboxes | frozenset(m.casefold() for m in mailboxes),
    )


def check_ops_local(settings: Settings, *, model: str | None = None, fake: bool = False) -> None:
    """R14 for module 3: refuse ``allow_remote_ollama`` and Ollama cloud models.

    A request may hold an order's facts and a draft names a customer contact; neither
    leaves the workstation, even with the opt-in that module 1 accepts (EFVP of module 3).
    The chat model name must also fit the access log schema (``fake:`` prefix included in
    fake mode), or every request would end in ``audit_unavailable``.
    """
    if settings.allow_remote_ollama:
        raise RemoteOllamaError(
            "module 3 never runs with ALVERNO_ALLOW_REMOTE_OLLAMA=true: requests and "
            "drafts stay on this workstation (Law 25, EFVP R14)"
        )
    for name in (model or settings.chat_model, settings.embed_model):
        if name.strip().lower().endswith((":cloud", "-cloud")):
            raise RemoteOllamaError(
                f"model {name!r} is an Ollama cloud model: module 3 only runs local "
                "models (Law 25, EFVP R14)"
            )
    chat_model = model or settings.chat_model
    logged = f"fake:{chat_model}" if fake else chat_model
    if re.fullmatch(MODEL_PATTERN, logged) is None:
        raise PrivacyConfigError(
            "chat model name not loggable (access log schema: at most 64 letters, digits "
            "or . : / - _, the fake: prefix included in fake mode)"
        )


def ops_quarantine(ctx: PrivacyContext) -> Quarantine:
    """The connectors' quarantine: :func:`scrub_tool_text` with the ops context."""

    def quarantine(text: str) -> QuarantinedText:
        result = scrub_tool_text(text, ctx)
        return QuarantinedText(text=result.text, flags=tuple(result.flags))

    return quarantine


def service_scope_index(erp: SimErp) -> ScopeIndex:
    """The scope index of every account of ``erp`` (``connectors.sim.account_scope_index``).

    The rig's guards and the evaluation's ``cross_account_leaks`` read the same index.
    """
    return account_scope_index(erp, build_scope_index)


def service_header(erp: ErpConnector) -> Callable[[OpsState], DraftHeader]:
    """The operator header of a pending 3a draft: account label, recipient function,
    cited ids. The contact is read by code for display only; it never enters state."""

    def header_for(state: OpsState) -> DraftHeader:
        ids = state.get("ids") or []
        contact = erp.get_order_contact(ids[0]) if len(ids) == 1 else None
        if contact is None:
            raise OpsRuntimeError("header:no_contact")
        cited = tuple(str(c) for c in state.get("draft_citations") or [])
        return DraftHeader(
            account_label=contact.account_label,
            recipient_function=contact.function,
            cited_ids=cited,
        )

    return header_for


def build_service_rig(
    settings: Settings,
    *,
    mode: OpsMode,
    fake: bool,
    log: TimelineLog,
    channel: Channel,
    timeline: Timeline | None = None,
    ctx: PrivacyContext | None = None,
) -> OpsRig:
    """One 3a request runner and its probes (see the module docstring).

    ``log`` is the service access log (its timeline is the rig's); ``timeline``, when
    given, must be that same timeline. ``ctx`` reuses an ops context already loaded (an
    evaluation builds one rig per item). Building a full-mode rig calls no model: a model
    that cannot be reached shows at the first draft (``<step>:model_unavailable``).
    """
    if mode not in ("tools", "full"):
        raise OpsRuntimeError("pipeline:mode")
    check_ops_local(settings, fake=fake)
    context = ctx if ctx is not None else load_ops_context(settings, "service")
    shared = log.timeline if timeline is None else timeline
    sim = SimErp.from_yaml(settings.ops_erp_path, quarantine=ops_quarantine(context))
    connector = CountingConnector(timeline=shared, erp=sim)
    authority = ApprovalAuthority()
    executor = CountingExecutor(ToolExecutor(connector, connector, authority.verifier), shared)
    drafter: Drafter
    if mode == "full":
        model = make_ops_fake_chat() if fake else make_chat(settings, fake=False)
        chat = CountingChat(ProbeChat(model), shared)
        drafter = ChatDrafter(chat)
    else:
        # Tools mode calls no model: the counted chat is there to prove it (0 calls).
        chat = CountingChat(make_chat(settings, fake=fake), shared)
        drafter = TemplateDrafter()
    scope_index = service_scope_index(sim)
    rounds = settings.ops_max_edit_rounds
    graph = build_service_graph(
        executor=executor,
        erp=connector,
        ctx=context,
        scope_index=scope_index,
        drafter=drafter,
        max_edit_rounds=rounds,
    )
    envelope = OpsEnvelope(
        graph,
        module="service",
        screen=lambda request: screen_service(request, context),
        log=log,
        authority=authority,
        policy=OpsRefusalPolicy("service"),
        channel=channel,
        header_for=service_header(connector),
        model_name=lambda: chat.model_name,
        chat_attempts=lambda: chat.attempts,
        max_edit_rounds=rounds,
    )
    return OpsRig(
        envelope=envelope,
        log=log,
        timeline=shared,
        executor=executor,
        connector=connector,
        sims=(sim,),
        chat=chat,
    )


def maint_header(state: OpsState) -> DraftHeader:
    """The operator header of a pending 3b work request: the asset's name (in
    ``account_label``) and production line (in ``recipient_function``), and the cited
    work orders. Read from the asset view in state; no person is named."""
    ids = state.get("ids") or []
    for record in state.get("records") or []:
        if isinstance(record, dict) and len(ids) == 1 and record.get("asset_id") == ids[0]:
            if "name" not in record:
                continue
            asset = AssetView.model_validate(record)
            action = state.get("proposed_action") or {}
            args = action.get("args") if isinstance(action, dict) else None
            raw = args.get("cited_wo_ids") if isinstance(args, dict) else None
            cited = tuple(str(c) for c in raw) if isinstance(raw, list) else ()
            return DraftHeader(
                account_label=asset.name, recipient_function=asset.line, cited_ids=cited
            )
    raise OpsRuntimeError("header:no_asset")


def build_maint_rig(
    settings: Settings,
    *,
    mode: OpsMode,
    fake: bool,
    log: TimelineLog,
    channel: Channel,
    timeline: Timeline | None = None,
    ctx: PrivacyContext | None = None,
) -> OpsRig:
    """One 3b request runner and its probes (see the module docstring).

    ``log`` is the maintenance access log (prefix ``maint``); the other parameters are
    those of :func:`build_service_rig`. Building a full-mode rig calls no model: a model
    that cannot be reached shows at the first diagnosis (``<step>:model_unavailable``).
    """
    if mode not in ("tools", "full"):
        raise OpsRuntimeError("pipeline:mode")
    check_ops_local(settings, fake=fake)
    context = ctx if ctx is not None else load_ops_context(settings, "maint")
    shared = log.timeline if timeline is None else timeline
    sim = SimGmao.from_yaml(settings.ops_gmao_path, quarantine=ops_quarantine(context))
    connector = CountingConnector(timeline=shared, gmao=sim)
    authority = ApprovalAuthority()
    executor = CountingExecutor(ToolExecutor(connector, connector, authority.verifier), shared)
    diagnoser: Diagnoser
    if mode == "full":
        model = make_ops_fake_chat() if fake else make_chat(settings, fake=False)
        chat = CountingChat(ProbeChat(model), shared)
        diagnoser = ChatDiagnoser(chat)
    else:
        # Tools mode calls no model: the counted chat is there to prove it (0 calls).
        chat = CountingChat(make_chat(settings, fake=fake), shared)
        diagnoser = BM25Diagnoser()
    rounds = settings.ops_max_edit_rounds
    graph = build_maint_graph(
        executor=executor,
        gmao=connector,
        ctx=context,
        diagnoser=diagnoser,
        history_days=settings.ops_history_days,
        max_work_orders=settings.ops_max_work_orders,
        max_edit_rounds=rounds,
    )
    envelope = OpsEnvelope(
        graph,
        module="maint",
        screen=lambda request: screen_maintenance(request, context),
        log=log,
        authority=authority,
        policy=OpsRefusalPolicy("maint"),
        channel=channel,
        header_for=maint_header,
        model_name=lambda: chat.model_name,
        chat_attempts=lambda: chat.attempts,
        max_edit_rounds=rounds,
    )
    return OpsRig(
        envelope=envelope,
        log=log,
        timeline=shared,
        executor=executor,
        connector=connector,
        sims=(sim,),
        chat=chat,
    )
