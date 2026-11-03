"""Refusal messages of module 3 (spec I4 decision 6, EFVP « Politique de refus »).

Each sub-module covers all 24 ``OpsRefusalCode`` values with a fixed Québec French text
that names a role mailbox, never a person or a given name, holds no placeholder, stays
short and repeats no value. Assertion messages cite modules and codes, never a text.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

import pytest

from alverno_lab.privacy.lexicon import given_names, words
from alverno_lab.privacy.ops_policy import (
    IT_MAILBOX,
    MAINT_MAILBOX,
    PRIVACY_MAILBOX,
    SERVICE_MAILBOX,
    OpsModule,
    OpsPolicyError,
    OpsRefusalPolicy,
    auto_send_notice,
)
from alverno_lab.privacy.ops_types import OPS_REFUSAL_CODES, OpsRefusalCode

MODULES: tuple[OpsModule, ...] = get_args(OpsModule)
CODES: tuple[OpsRefusalCode, ...] = get_args(OpsRefusalCode)
ROLE_MAILBOXES = frozenset({SERVICE_MAILBOX, MAINT_MAILBOX, IT_MAILBOX, PRIVACY_MAILBOX})
SYSTEM_CODES = frozenset({"connector_unavailable", "audit_unavailable"})
PRIVACY_CODES = frozenset({"person_targeted", "profiling", "minimization_refused"})
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A sentence ends with « . », « ! » or « ? » followed by a space or the end of the text,
# so the dots inside a mailbox never count.
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
MAX_LENGTH = 300


def policy(module: OpsModule) -> OpsRefusalPolicy:
    return OpsRefusalPolicy(module)


@pytest.mark.parametrize("module", MODULES)
def test_every_code_has_a_message(module: OpsModule) -> None:
    keys = set(policy(module).messages)
    assert keys == OPS_REFUSAL_CODES, f"{module}: codes differ from OPS_REFUSAL_CODES"
    assert len(CODES) == 24


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("code", CODES)
def test_message_rules(module: OpsModule, code: OpsRefusalCode) -> None:
    text = policy(module).message(code)
    where = f"{module}:{code}"
    assert 0 < len(text) <= MAX_LENGTH, f"{where}: length"
    assert "{" not in text and "[" not in text and "}" not in text, f"{where}: placeholder"
    assert 1 <= len(SENTENCE_END_RE.findall(text)) <= 2, f"{where}: one or two sentences"
    assert text.rstrip().endswith((".", "!", "?")), f"{where}: final punctuation"
    mailboxes = set(EMAIL_RE.findall(text))
    assert mailboxes, f"{where}: no mailbox"
    assert mailboxes <= ROLE_MAILBOXES, f"{where}: an address that is not a role mailbox"
    if code in SYSTEM_CODES:
        assert IT_MAILBOX in mailboxes, f"{where}: IT mailbox"
    else:
        assert policy(module).mailbox in mailboxes, f"{where}: module mailbox"
    if PRIVACY_MAILBOX in mailboxes:
        assert code in PRIVACY_CODES, f"{where}: privacy mailbox on a non-privacy code"
    names = set(words(EMAIL_RE.sub(" ", text))) & given_names()
    assert not names, f"{where}: a given name"


@pytest.mark.parametrize("module", MODULES)
def test_messages_never_name_a_person_or_the_other_module(module: OpsModule) -> None:
    other = MAINT_MAILBOX if module == "service" else SERVICE_MAILBOX
    for code in CODES:
        text = policy(module).message(code)
        assert other not in text, f"{module}:{code}: mailbox of the other sub-module"
        assert "CNESST" not in text.upper(), f"{module}:{code}: no CNESST advice"


@pytest.mark.parametrize("module", MODULES)
def test_sensitive_out_of_scope_points_to_the_health_and_safety_process(
    module: OpsModule,
) -> None:
    text = policy(module).message("sensitive_out_of_scope")
    assert "le processus de santé et de sécurité du travail" in text


def test_the_two_modules_name_their_own_mailbox() -> None:
    assert policy("service").mailbox == "service-client@alverno.example"
    assert policy("maint").mailbox == "entretien@alverno.example"
    assert policy("service").message("empty") != policy("maint").message("empty")


@pytest.mark.parametrize("code", ["", "unknown", "empty_question", "own_record"])
def test_an_unknown_code_is_an_error_without_fallback(code: str) -> None:
    with pytest.raises(ValueError) as caught:
        policy("service").message(code)  # type: ignore[arg-type]
    assert str(caught.value) == "ops_policy:unknown_code"
    assert isinstance(caught.value, OpsPolicyError)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_an_unknown_module_is_an_error() -> None:
    bad: Literal["hr"] = "hr"
    with pytest.raises(OpsPolicyError, match=r"^ops_policy:unknown_module$") as caught:
        OpsRefusalPolicy(bad)  # type: ignore[arg-type]
    assert caught.value.__context__ is None


def test_messages_are_read_only() -> None:
    messages = policy("maint").messages
    with pytest.raises(TypeError):
        messages["empty"] = "autre"  # type: ignore[index]


def test_auto_send_notice() -> None:
    assert auto_send_notice() == (
        "Le brouillon doit être approuvé avant tout envoi ; l'envoi se fait depuis votre "
        "propre boîte."
    )
    assert auto_send_notice() not in {policy(m).message(c) for m in MODULES for c in CODES}
