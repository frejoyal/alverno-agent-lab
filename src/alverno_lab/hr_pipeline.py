"""Factories of the HR module (module 2): privacy context, corpus check, index, agent.

Every HR load (``hr index``, ``hr ask``, ``hr eval``, ``hr bench``, ``privacy eval``)
goes through :func:`check_hr_corpus`, which fails closed on any privacy rule of §5.7.
The HR module uses ``Settings.for_module("hr")``: its own corpus, gold set, index
(``.cache/hr/index.npz``) and no-match thresholds; the Quality ones stay untouched.
Every HR factory first applies :func:`check_hr_local` (R14): the HR module never runs
with ``allow_remote_ollama`` or an Ollama cloud model, whatever the opt-in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from alverno_lab.agents.hr.graph import HrAgent
from alverno_lab.config import RemoteOllamaError, Settings
from alverno_lab.corpus import Document, load_corpus
from alverno_lab.index import IndexMismatchError, IndexNotFoundError, VectorIndex
from alverno_lab.pipeline import Backend, build_index, build_retriever, make_chat
from alverno_lab.privacy import (
    AccessLog,
    OrgRoles,
    PrivacyConfigError,
    PrivacyContext,
    build_vocabulary,
    check_corpus_privacy,
    hr_refusal_policy,
)
from alverno_lab.privacy.access_log import MODEL_PATTERN
from alverno_lab.privacy.lexicon import allowlist_terms, given_names
from alverno_lab.retrieval import HybridRetriever

# Mailbox keys of data/hr/contacts.yaml (spec §3).
MailboxKey = Literal["rh", "paie", "confidentialite", "signalement", "soutien_ti", "qualite"]


class Contacts(BaseModel):
    """Role mailboxes and governance roles of ``data/hr/contacts.yaml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mailboxes: dict[MailboxKey, str]
    rprp_role: str
    harassment_designated_role: str


def load_contacts(path: Path) -> Contacts:
    """Read the contacts file; every mailbox must be at ``alverno.example``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PrivacyConfigError(f"contacts file not readable: {path}") from exc
    except yaml.YAMLError:
        raise PrivacyConfigError(f"{path}: invalid YAML") from None
    try:
        contacts = Contacts.model_validate(raw)
    except ValidationError as exc:
        locs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors())
        raise PrivacyConfigError(f"{path}: invalid contacts ({locs})") from None
    if set(contacts.mailboxes) != set(get_args(MailboxKey)):
        raise PrivacyConfigError(f"{path}: every role mailbox must be listed")
    if not all(m.endswith("@alverno.example") for m in contacts.mailboxes.values()):
        raise PrivacyConfigError(f"{path}: role mailboxes must be at alverno.example")
    return contacts


def load_hr_documents(settings: Settings) -> list[Document]:
    """Load the HR corpus at level 2 (no privacy check: see :func:`check_hr_corpus`)."""
    return load_corpus(settings.hr_corpus_dir, max_sensitivity=2)


def load_privacy_context(
    settings: Settings, *, documents: Sequence[Document] | None = None
) -> PrivacyContext:
    """Build the HR privacy context: roles, role mailboxes, allowlist, corpus vocabulary."""
    roles = OrgRoles.load(settings.hr_roles_path)
    contacts = load_contacts(settings.hr_contacts_path)
    for role in (contacts.rprp_role, contacts.harassment_designated_role):
        if not roles.is_role(role):
            raise PrivacyConfigError(f"{settings.hr_contacts_path}: unknown governance role")
    docs = load_hr_documents(settings) if documents is None else documents
    # Every lexicon is read now, so a missing data file fails at startup, not mid-request.
    given_names()
    return PrivacyContext(
        allowlist=allowlist_terms(),
        role_mailboxes=frozenset(m.casefold() for m in contacts.mailboxes.values()),
        roles=roles,
        vocabulary=build_vocabulary(f"{d.title}\n{d.body}" for d in docs),
        k=settings.small_group_k,
    )


def check_hr_corpus(
    settings: Settings, ctx: PrivacyContext | None = None
) -> tuple[list[Document], PrivacyContext]:
    """Load the HR corpus and apply the four privacy rules; raise ``CorpusError`` on any."""
    docs = load_hr_documents(settings)
    context = ctx or load_privacy_context(settings, documents=docs)
    check_corpus_privacy(docs, context, hr_rules=True)
    return docs, context


def check_hr_local(settings: Settings, *, model: str | None = None, fake: bool = False) -> None:
    """R14: refuse ``allow_remote_ollama`` and Ollama cloud models for the HR module.

    HR questions can hold personal information typed by mistake; they never leave the
    workstation, even with the opt-in that module 1 accepts (docs/loi25.md, EFVP).
    The chat model name must also fit the access log schema (``fake:`` prefix included
    in fake mode): otherwise every request would end in ``audit_unavailable``, which
    would point the user at the wrong cause.
    """
    if settings.allow_remote_ollama:
        raise RemoteOllamaError(
            "the HR module never runs with ALVERNO_ALLOW_REMOTE_OLLAMA=true: HR questions "
            "stay on this workstation (Law 25, EFVP R14)"
        )
    for name in (model or settings.chat_model, settings.embed_model):
        if name.strip().lower().endswith((":cloud", "-cloud")):
            raise RemoteOllamaError(
                f"model {name!r} is an Ollama cloud model: the HR module only runs local "
                "models (Law 25, EFVP R14)"
            )
    chat_model = model or settings.chat_model
    logged = f"fake:{chat_model}" if fake else chat_model
    if re.fullmatch(MODEL_PATTERN, logged) is None:
        raise PrivacyConfigError(
            "chat model name not loggable (access log schema: at most 64 letters, digits "
            "or . : / - _, the fake: prefix included in fake mode)"
        )


def hr_settings(settings: Settings) -> Settings:
    """The HR view of ``settings`` (corpus, gold set, index, no-match thresholds)."""
    return settings.for_module("hr")


def build_hr_index(settings: Settings, *, fake: bool = False) -> VectorIndex:
    """Check the HR corpus, then embed it; real mode saves ``hr_index_path``."""
    check_hr_local(settings)
    check_hr_corpus(settings)
    return build_index(hr_settings(settings), fake=fake)


def build_hr_retriever(
    settings: Settings, *, fake: bool = False
) -> tuple[HybridRetriever, Backend]:
    """Check the HR corpus, then build the hybrid retriever over it.

    Real mode loads ``hr_index_path`` and refuses it if the embedding model or the
    corpus hash differs (module-1 manifest check).
    """
    check_hr_local(settings)
    check_hr_corpus(settings)
    return _retriever(settings, fake=fake)


def _retriever(settings: Settings, *, fake: bool) -> tuple[HybridRetriever, Backend]:
    """``build_retriever`` on the HR view, with an HR hint on a missing or stale index."""
    try:
        return build_retriever(hr_settings(settings), fake=fake)
    except (IndexNotFoundError, IndexMismatchError) as exc:
        # The module-1 message says « run `alverno index` », which rebuilds the Quality
        # index; the HR index has its own command.
        raise type(exc)(
            f"HR index missing or stale at {settings.hr_index_path}; run `alverno hr index`"
        ) from None


def build_hr_agent(
    settings: Settings,
    *,
    fake: bool = False,
    model: str | None = None,
    retriever: HybridRetriever | None = None,
    access_log: AccessLog | None = None,
) -> HrAgent:
    """Build the HR agent (reusing ``retriever`` when given); the corpus is checked first.

    The access log defaults to ``access_log_dir`` with ``access_log_retention_days``;
    the refusal texts use the mailboxes of ``contacts.yaml``.
    """
    check_hr_local(settings, model=model, fake=fake)
    docs, ctx = check_hr_corpus(settings)
    if retriever is None:
        retriever, _ = _retriever(settings, fake=fake)
    contacts = load_contacts(settings.hr_contacts_path)
    policy = hr_refusal_policy(
        hr_mailbox=contacts.mailboxes["rh"],
        privacy_mailbox=contacts.mailboxes["confidentialite"],
    )
    log = access_log or AccessLog(
        settings.access_log_dir, retention_days=settings.access_log_retention_days
    )
    return HrAgent(
        retriever,
        make_chat(settings, fake=fake, model=model),
        ctx,
        access_log=log,
        min_confidence=settings.min_confidence,
        policy=policy,
        doc_levels={d.doc_id: d.sensitivity for d in docs},
    )
