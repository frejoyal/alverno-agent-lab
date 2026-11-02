"""Account scope check of 3a texts (I4 spec decision 5, ADR 0012).

The gold acceptance tests build the index from ``account_scope_values(SimErp(...))`` and
read the service gold set: cross-account requests and the two malicious edits flag,
answerable requests and clean edits do not. Synthetic strings check the matching rules.
Assertion messages name item ids, never a request, an edit or a value.
"""

from __future__ import annotations

import ast
import re
from functools import cache

import pytest

from alverno_lab.connectors import QuarantinedText, SimErp
from alverno_lab.connectors.sim import _label_short_forms, account_scope_values
from alverno_lab.evals.service import SvcGoldItem, load_svc_gold
from alverno_lab.privacy.scope import (
    ScopeError,
    ScopeIndex,
    ScopeVerdict,
    build_scope_index,
    check_scope,
)

from .conftest import REPO_ROOT
from .ops_privacy_helpers import OPS_DATA, SVC_GOLD

SRC = REPO_ROOT / "src" / "alverno_lab"
CLEAN = ScopeVerdict(ok=True, rule=None, accounts=0)


def _stub(text: str) -> QuarantinedText:
    return QuarantinedText(text=text, flags=())


@cache
def erp() -> SimErp:
    return SimErp.from_yaml(OPS_DATA / "erp.yaml", quarantine=_stub)


@cache
def index() -> ScopeIndex:
    return build_scope_index(account_scope_values(erp()))


@cache
def gold() -> tuple[SvcGoldItem, ...]:
    return tuple(load_svc_gold(SVC_GOLD, erp()))


# --------------------------------------------------------------------------- gold
def test_cross_account_requests_flag() -> None:
    items = [
        i
        for i in gold()
        if i.expected_reason == "cross_account" and i.expected_outcome == "refused"
    ]
    assert len(items) == 6
    for item in items:
        assert item.account_id is not None, item.id
        verdict = check_scope(item.request, item.account_id, index())
        assert not verdict.ok and verdict.rule == "other_account_value", item.id
        assert verdict.accounts == 1, item.id


def test_answerable_requests_do_not_flag() -> None:
    items = [
        i
        for i in gold()
        if i.expected_outcome in {"answered", "executed", "rejected"} or i.is_answerable
    ]
    assert len(items) >= 29
    for item in items:
        assert item.account_id is not None, item.id
        assert check_scope(item.request, item.account_id, index()) == CLEAN, item.id


def _edits(item: SvcGoldItem) -> list[str]:
    return [s.edited_text for s in item.approval_script if s.edited_text is not None]


def test_malicious_edits_flag_and_clean_edits_do_not() -> None:
    malicious = clean = 0
    for item in gold():
        for edit in _edits(item):
            assert item.account_id is not None, item.id
            verdict = check_scope(edit, item.account_id, index())
            if item.expected_outcome == "withheld" and item.expected_reason == "cross_account":
                malicious += 1
                assert not verdict.ok and verdict.accounts == 1, item.id
            else:
                clean += 1
                assert verdict == CLEAN, item.id
    assert malicious == 2
    assert clean >= 3


def test_other_traps_are_left_to_their_own_stage() -> None:
    for item in gold():
        if item.account_id is not None and item.family != "cross_account":
            assert check_scope(item.request, item.account_id, index()).ok, item.id


# --------------------------------------------------------------------------- scope values
def test_account_scope_values_cover_every_account_and_kind() -> None:
    values = account_scope_values(erp())
    assert set(values) == set(erp().account_ids)
    for account, found in values.items():
        assert account in found, account
        assert any(v.endswith(".example") and "@" not in v for v in found), account
        assert any("@" in v for v in found), account
        assert any(re.fullmatch(r"CT-[0-9]{3}", v) for v in found), account
        assert any(re.fullmatch(r"CMD-[0-9]{4}-[0-9]{4}", v) for v in found), account
        assert any("(fictive)" in v for v in found), account
        assert any(v and "(" not in v and " " in v and not v[0].isdigit() for v in found)
    tracking = {v for found in values.values() for v in found if v.startswith("TRK-")}
    assert tracking


def test_account_scope_values_hold_no_phone_or_home_address() -> None:
    values = account_scope_values(erp())
    for account, found in values.items():
        for value in found:
            assert sum(c.isdigit() for c in value) < 10, account
            assert not re.match(r"[0-9]+ ", value), account


def test_every_order_and_contact_belongs_to_exactly_one_account() -> None:
    values = account_scope_values(erp())
    owners: dict[str, int] = {}
    for found in values.values():
        for value in found:
            owners[value] = owners.get(value, 0) + 1
    order_ids = erp().order_ids
    assert all(owners[o] == 1 for o in order_ids)


def _names_in(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias | ast.FunctionDef):
            names.add(node.name)
    return names


def test_only_tests_read_the_account_scope_values() -> None:
    users = {
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "account_scope_values" in _names_in(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert users == {"connectors/sim.py"}
    import alverno_lab.connectors as connectors

    assert not hasattr(connectors, "account_scope_values")
    assert not hasattr(erp(), "account_scope_values")


# --------------------------------------------------------------------------- synthetic
SYNTHETIC = {
    "A-1": [
        "CLI-901",
        "Ateliers Lavoie (fictive)",
        "Ateliers Lavoie",
        "client91.example",
        "Hébert",
        "Marc-André Hébert",
        "marc-andre.hebert@client91.example",
        "CT-901",
        "CMD-2026-0901",
        "TRK-900001",
        "Tremblay",
    ],
    "A-2": [
        "CLI-902",
        "client92.example",
        "Lessard",
        "Sylvie Lessard",
        "sylvie.lessard@client92.example",
        "CMD-2026-0902",
        "Tremblay",
        "",
    ],
    "A-3": ["CLI-903", "client93.example", "Bernier", "CMD-2026-0903"],
}


@cache
def synthetic() -> ScopeIndex:
    return build_scope_index(SYNTHETIC)


CASES: list[tuple[str, str, int]] = [
    # Ids: exact, whole tokens.
    ("La commande CMD-2026-0902 est prête.", "A-1", 1),
    ("Le compte CLI-902 attend.", "A-1", 1),
    ("Suivi TRK-900001.", "A-2", 1),
    ("Commande CMD-2026-09021 inconnue.", "A-1", 0),
    ("Commande cmd-2026-0902.", "A-1", 0),
    ("Commande CMD-2026-0901 de ce client.", "A-1", 0),
    # Words: accent- and case-insensitive whole words.
    ("Bonjour M. HEBERT.", "A-2", 1),
    ("Bonjour M. Hebert.", "A-2", 1),
    ("Copie à Mme Lessard.", "A-1", 1),
    ("Les Lessardières ont livré.", "A-1", 0),
    ("Bonjour M. Hébert.", "A-1", 0),
    ("Aux Ateliers Lavoie, merci.", "A-2", 1),
    # A value shared with the in-scope account never flags.
    ("Bonjour M. Tremblay.", "A-1", 0),
    ("Bonjour M. Tremblay.", "A-3", 2),
    # E-mails: exact (case-insensitive), and a domain inside any address.
    ("Écrire à SYLVIE.LESSARD@CLIENT92.EXAMPLE.", "A-1", 1),
    ("Écrire à achats@client92.example.", "A-1", 1),
    ("Écrire à achats@ventes.client93.example.", "A-1", 1),
    ("Écrire à achats@client921.example.", "A-1", 0),
    ("Domaine client92.example confirmé.", "A-1", 1),
    ("Domaine www.client92.example confirmé.", "A-1", 1),
    ("Écrire à service-client@alverno.example.", "A-1", 0),
    # Several accounts are counted, never named.
    ("CLI-902 et CLI-903 attendent.", "A-1", 2),
    ("CLI-902, Lessard et sylvie.lessard@client92.example.", "A-1", 1),
]


@pytest.mark.parametrize(("text", "account", "others"), CASES)
def test_synthetic_matching(text: str, account: str, others: int) -> None:
    verdict = check_scope(text, account, synthetic())
    if others:
        assert verdict == ScopeVerdict(ok=False, rule="other_account_value", accounts=others)
    else:
        assert verdict == CLEAN


def test_unknown_account_is_an_error_without_context() -> None:
    with pytest.raises(ScopeError, match=r"^scope:unknown_account$") as info:
        check_scope("Bonjour", "CLI-999", synthetic())
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_the_index_keeps_ids_exact_and_words_normalised() -> None:
    idx = synthetic()
    assert "CMD-2026-0901" in idx.ids and "TRK-900001" in idx.ids
    assert "marc-andre hebert" in idx.words and "hebert" in idx.words
    assert "client91.example" in idx.domains
    assert "marc-andre.hebert@client91.example" in idx.emails
    assert idx.words["tremblay"] == frozenset({"A-1", "A-2"})
    assert idx.accounts == frozenset({"A-1", "A-2", "A-3"})
    assert "" not in idx.words


def test_the_index_repr_shows_account_ids_only() -> None:
    shown = repr(synthetic())
    assert "A-1" in shown
    assert "Hébert" not in shown and "hebert" not in shown.casefold()
    assert "client91" not in shown and "CMD-" not in shown


def test_an_account_id_is_a_value_of_its_account() -> None:
    idx = build_scope_index({"CLI-904": [], "CLI-905": []})
    assert check_scope("Pour CLI-905.", "CLI-904", idx).accounts == 1
    assert check_scope("Pour CLI-904.", "CLI-904", idx) == CLEAN


def test_an_empty_text_is_in_scope() -> None:
    assert check_scope("", "A-1", synthetic()) == CLEAN


def test_a_value_without_any_letter_left_is_skipped() -> None:
    # A lone combining accent normalises to nothing: it must not index an empty word.
    idx = build_scope_index({"CLI-906": [chr(0x301), "  "], "CLI-907": []})
    assert dict(idx.words) == {}
    assert check_scope("Bonjour.", "CLI-907", idx) == CLEAN


# --------------------------------------------------------------------------- review additions
def test_account_scope_values_hold_every_contact_and_tracking_ref() -> None:
    """Every contact's surname, full name, e-mail and ref, and every order's id and
    tracking ref, are values of its own account (spec decision 5)."""
    values = account_scope_values(erp())
    fixture = erp()._fixture
    for contact in fixture.contacts:
        found = values[contact.account_id]
        assert contact.surname in found, contact.account_id
        assert f"{contact.given_name} {contact.surname}" in found, contact.account_id
        assert contact.business_email in found, contact.account_id
        assert contact.contact_ref in found, contact.account_id
    for order in fixture.orders:
        found = values[order.account_id]
        assert order.order_id in found, order.account_id
        if order.tracking_ref is not None:
            assert order.tracking_ref in found, order.account_id


def test_account_scope_values_hold_each_label_with_and_without_its_qualifier() -> None:
    values = account_scope_values(erp())
    for account in erp()._fixture.accounts:
        found = values[account.account_id]
        assert account.label in found, account.account_id
        assert re.sub(r"\s*\([^()]*\)\s*$", "", account.label) in found, account.account_id


def _other_than(account: str) -> str:
    return next(a for a in sorted(erp().account_ids) if a != account)


def test_a_label_written_for_short_flags() -> None:
    """The distinctive part of a label (« chez <tail> »), even wrapped over two lines."""
    fixture = erp()._fixture
    checked = 0
    for account in fixture.accounts:
        core = re.sub(r"\s*\([^()]*\)\s*$", "", account.label)
        head, _, tail = core.partition(" ")
        tail = re.sub(r"^(?:(?:du|de|des|la|le|les)\s+|l'|d')+", "", tail, flags=re.I)
        if "-" not in tail and " " not in tail:
            continue
        other = _other_than(account.account_id)
        for text in (f"Chez {tail}, tout va bien.", f"Livré chez {head}\n{tail} hier."):
            verdict = check_scope(text, other, index())
            assert not verdict.ok and verdict.accounts == 1, account.account_id
        assert check_scope(f"Chez {tail}.", account.account_id, index()) == CLEAN
        checked += 1
    assert checked >= 3


@pytest.mark.parametrize("dash", [chr(0x2011), chr(0x2013), chr(0x2010), chr(0x2212)])
def test_an_id_with_another_dash_still_flags(dash: str) -> None:
    fixture = erp()._fixture
    order = fixture.orders[0]
    other = _other_than(order.account_id)
    text = f"La commande {order.order_id.replace('-', dash)} est prête."
    verdict = check_scope(text, other, index())
    assert not verdict.ok and verdict.accounts == 1, order.account_id
    assert check_scope(text, order.account_id, index()) == CLEAN


def test_a_lone_common_word_never_becomes_a_label_value() -> None:
    assert _label_short_forms("Groupe Nord (fictive)") == set()
    assert _label_short_forms("Atelier de l'Est Profond") == {"Est Profond"}
    assert _label_short_forms("Acier Desjardins-Roy") == {"Desjardins-Roy"}
