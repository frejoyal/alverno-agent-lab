"""Deterministic routing of module 3a (I6 spec decision 2, ADR 0012 « Routage déterministe »).

Synthetic requests check the closed rules: prefixed order ids only (after folding dashes),
no id → ``order_not_identified``, two distinct ids → ``multi_order``, one id and a word
of the drafting family → ``customer_reply``, one id otherwise → ``order_status``. The gold
acceptance test routes every svc_gold item that the screen lets through and compares
with the route written in the item. A structural test keeps every gold field, item id
and fixture value out of the 3a source. Assertion messages cite item ids and rules, never
a request.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

import pytest

from alverno_lab.agents.service.route import (
    ORDER_ID_RE,
    RouteResult,
    find_order_ids,
    has_drafting_word,
    route_service,
)
from alverno_lab.connectors.sim import SimErp, fixture_strings
from alverno_lab.evals.service import SvcGoldItem, load_svc_gold
from alverno_lab.privacy.ops_screen import screen_service

from .conftest import EVAL_DATA, REPO_ROOT
from .ops_privacy_helpers import ops_privacy_ctx
from .ops_toy_graph import stub_quarantine

SRC = REPO_ROOT / "src" / "alverno_lab"
SERVICE_SOURCES: tuple[Path, ...] = (
    *sorted((SRC / "agents" / "service").glob("*.py")),
    SRC / "ops_pipeline.py",
)
ORDER = "CMD-2026-0001"
OTHER = "CMD-2026-0002"
NB_HYPHEN = chr(0x2011)
EN_DASH = chr(0x2013)


@functools.cache
def sim_erp() -> SimErp:
    return SimErp.from_yaml(EVAL_DATA / "ops" / "erp.yaml", quarantine=stub_quarantine)


@functools.cache
def gold() -> tuple[SvcGoldItem, ...]:
    return tuple(load_svc_gold(EVAL_DATA / "svc_gold.jsonl", sim_erp()))


# --------------------------------------------------------------------------- ids
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Où en est la commande {ORDER} ?", [ORDER]),
        (f"({ORDER}).", [ORDER]),
        (f"{ORDER}, {ORDER} et encore {ORDER}", [ORDER]),
        (f"{ORDER} puis {OTHER}", [ORDER, OTHER]),
        (ORDER.replace("-", NB_HYPHEN), [ORDER]),
        (ORDER.replace("-", EN_DASH), [ORDER]),
        ("commande 2026-0412", []),
        ("cmd-2026-0001", []),
        ("CMD-2026-00011", []),
        ("XCMD-2026-0001", []),
        ("CMD-2026-0001-2", []),
        ("CMD-26-0001", []),
        ("CMD-٢٠٢٦-0001", []),  # Arabic-Indic digits are not [0-9]
    ],
)
def test_find_order_ids_reads_prefixed_whole_ids(text: str, expected: list[str]) -> None:
    assert find_order_ids(text) == expected


def test_the_order_id_pattern_is_the_adr_0012_one() -> None:
    assert ORDER_ID_RE.pattern == r"(?<![\w-])CMD-[0-9]{4}-[0-9]{4}(?![\w-])"


# --------------------------------------------------------------------------- drafting family
DRAFTING = [
    "Prépare une note",
    "préparer la note",
    "Préparez la note",
    "Rédige la suite",
    "rédiger la suite",
    "Rédigez la suite",
    "Écris au client",
    "écrire au client",
    "Écrivez au client",
    "Réponds au client",
    "répondre au client",
    "Répondez au client",
    "Envoie la suite",
    "envoyer la suite",
    "Envoyez la suite",
    "Transmets la suite",
    "transmettre la suite",
    "Transmettez la suite",
    "Compose la suite",
    "composer la suite",
    "Composez la suite",
    "rédige-lui la suite",
    "prépare-moi la suite",
    "envoie-la",
    "envoie-le-lui",
    "REDIGE LA SUITE",
    "redige la suite",
    "il faut une réponse",
    "un courriel au client",
    "un message au client",
    "UN BROUILLON",
    "un\nbrouillon",
]
NOT_DRAFTING = [
    "Où en est la commande ?",
    "La commande est-elle préparée ?",
    "Elle a été envoyée hier.",
    "Les réponses du client",
    "le brouillon",
    "la réponse du fournisseur",
    "messagerie",
    "prépareur",
    "envoi",
    "",
]


@pytest.mark.parametrize("text", DRAFTING)
def test_the_drafting_family_is_recognised(text: str) -> None:
    assert has_drafting_word(text)


@pytest.mark.parametrize("text", NOT_DRAFTING)
def test_other_words_are_not_the_drafting_family(text: str) -> None:
    assert not has_drafting_word(text)


# --------------------------------------------------------------------------- route
def test_one_id_without_a_drafting_word_is_an_order_status() -> None:
    assert route_service(f"Quel est l'état de la commande {ORDER} ?") == RouteResult(
        flow="order_status", order_id=ORDER, refusal=None
    )


def test_one_id_with_a_drafting_word_is_a_customer_reply() -> None:
    assert route_service(f"Rédige une réponse pour la commande {ORDER}.") == RouteResult(
        flow="customer_reply", order_id=ORDER, refusal=None
    )


def test_no_prefixed_id_is_order_not_identified() -> None:
    result = route_service("Où en est rendue la commande 2026-0412 ?")
    assert result == RouteResult(flow=None, order_id=None, refusal="order_not_identified")
    assert route_service("").refusal == "order_not_identified"


def test_two_distinct_ids_are_multi_order() -> None:
    result = route_service(f"Rédige une réponse pour {ORDER} et {OTHER}.")
    assert result == RouteResult(flow=None, order_id=None, refusal="multi_order")


def test_the_same_id_twice_is_one_order() -> None:
    assert route_service(f"{ORDER} : où en est la commande {ORDER} ?").order_id == ORDER


@pytest.mark.parametrize(
    ("flow", "order_id", "refusal"),
    [
        ("order_status", None, None),
        (None, ORDER, None),
        (None, None, None),
        ("order_status", ORDER, "multi_order"),
        (None, ORDER, "multi_order"),
    ],
)
def test_a_route_result_is_a_flow_or_a_refusal(
    flow: str | None, order_id: str | None, refusal: str | None
) -> None:
    with pytest.raises(ValueError, match="route:inconsistent"):
        RouteResult(flow=flow, order_id=order_id, refusal=refusal)  # type: ignore[arg-type]


def test_routing_reads_nothing_but_the_text() -> None:
    # Pure: the same text gives the same route, whatever else changed in between.
    text = f"Prépare un brouillon pour la commande {ORDER}."
    assert route_service(text) == route_service(text)
    assert route_service(text).flow == "customer_reply"


# --------------------------------------------------------------------------- gold acceptance
def test_every_routed_gold_item_routes_as_written() -> None:
    ctx = ops_privacy_ctx()
    checked = 0
    for item in gold():
        if item.route is None:
            continue
        screening = screen_service(item.request, ctx)
        assert screening.assessment.refusal is None, f"{item.id}:screen_refused"
        result = route_service(screening.redacted)
        got = result.flow or result.refusal
        assert got == item.route, f"{item.id}:route"
        if result.order_id is not None:
            assert result.order_id == item.order_id, f"{item.id}:order_id"
        checked += 1
    assert checked == sum(item.route is not None for item in gold())
    assert checked >= 40, "gold:routed_items"


def test_gold_items_without_a_route_are_screen_refusals() -> None:
    ctx = ops_privacy_ctx()
    for item in gold():
        if item.route is None:
            assert screen_service(item.request, ctx).assessment.refusal is not None, item.id


# --------------------------------------------------------------------------- no overfitting
GOLD_FIELDS = (
    "must_include",
    "must_not_include",
    "expected_outcome",
    "expected_reason",
    "expected_actions",
    "expected_proposal",
    "approval_script",
    "canaries",
    "contact_canaries",
    "injection_markers",
    "svc_gold",
    "load_svc_gold",
)
ITEM_ID_RE = re.compile(r"\bs[p]?[0-9]{2,3}\b")
FIXTURE_ID_RE = re.compile(r"\b(?:CMD|CLI|CT|TRK|TRP|RET|EQ|OT)-[0-9A-Z]")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", SERVICE_SOURCES, ids=lambda p: p.name)
def test_the_3a_source_reads_no_gold_field_and_no_item_id(path: Path) -> None:
    text = _source(path)
    names: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, ast.alias):
            names.add(node.name)
    assert not names & set(GOLD_FIELDS), f"{path.name}:gold_field"
    assert not ITEM_ID_RE.search(text), f"{path.name}:item_id"


@pytest.mark.parametrize("path", SERVICE_SOURCES, ids=lambda p: p.name)
def test_the_3a_source_hard_codes_no_fixture_value(path: Path) -> None:
    text = _source(path)
    # Every fixture string long enough to be specific (ids, labels, names, addresses,
    # notes); status codes and the two civilities are closed codes, not values.
    values = {
        value
        for value in fixture_strings(sim_erp())
        if len(value) >= 6 and not re.fullmatch(r"[a-z_]+", value)
    }
    assert values, "fixture:values"
    assert not [value for value in values if value in text], f"{path.name}:fixture_value"
    assert not FIXTURE_ID_RE.search(text.replace("CMD-NNNN-NNNN", "")), f"{path.name}:id"
