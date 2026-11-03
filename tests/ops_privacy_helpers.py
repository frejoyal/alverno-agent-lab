"""Shared test helper of module 3: the ops ``PrivacyContext``.

The production builder ``load_ops_context`` belongs to the I6 pipeline
(``ops_pipeline.py``); until then the I4 tests build the same context here, from the same
sources (spec I4 decision 2): the HR context (roles, HR role mailboxes, allowlist, HR
corpus vocabulary, k) plus ``build_vocabulary`` over every string of
``data/eval/ops/vocabulary.yaml``, and the ops mailboxes of that file (the role
mailboxes ``service-client@`` and ``entretien@``, and the generic customer mailboxes, as
the module plan says) added to ``role_mailboxes``. It never reads a holdout set.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from dataclasses import replace

import yaml

from alverno_lab.hr_pipeline import load_privacy_context
from alverno_lab.privacy import PrivacyContext, build_vocabulary

from .conftest import EVAL_DATA, hr_repo_settings

OPS_DATA = EVAL_DATA / "ops"
OPS_VOCABULARY = OPS_DATA / "vocabulary.yaml"
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
_MAILBOX_KEYS = ("role_mailboxes", "customer_mailboxes")


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


@functools.cache
def ops_privacy_ctx() -> PrivacyContext:
    """The ops privacy context, built once per test process."""
    hr = load_privacy_context(hr_repo_settings())
    raw: object = yaml.safe_load(OPS_VOCABULARY.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "vocabulary.yaml:not_a_mapping"
    mailboxes = [m for key in _MAILBOX_KEYS for m in _strings(raw.get(key, []))]
    assert "service-client@alverno.example" in mailboxes, "vocabulary.yaml:mailbox.missing"
    assert "entretien@alverno.example" in mailboxes, "vocabulary.yaml:mailbox.missing"
    return replace(
        hr,
        vocabulary=hr.vocabulary | build_vocabulary(_strings(raw)),
        role_mailboxes=hr.role_mailboxes | frozenset(m.casefold() for m in mailboxes),
    )
