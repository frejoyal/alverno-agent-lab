"""Deterministic routing of 3b requests (I9 spec decision 1; ADR 0012 « Routage déterministe »).

The router reads the redacted request and the GMAO's closed symptom labels: asset ids
``EQ-XXX-NN`` (none, or two distinct ones, give ``asset_not_identified``), the
work-request word families, and the symptom word families (fixture labels plus closed
synonyms). The requests here are synthetic paraphrases and near misses written for the
test, never gold requests; the gold acceptance check (every maint_gold item routes as
written, with its symptom) prints item ids only.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping

import pytest

from alverno_lab.agents.maintenance import (
    ASSET_ID_RE,
    SYMPTOM_SYNONYMS,
    MaintRouteResult,
    find_asset_ids,
    has_work_request_words,
    route_maintenance,
    symptom_of,
)
from alverno_lab.connectors import SimGmao
from alverno_lab.connectors.types import SYMPTOM_CODES, SymptomCode
from alverno_lab.evals.maint import MaintGoldItem, load_maint_gold
from alverno_lab.ops_pipeline import ops_quarantine
from alverno_lab.privacy.ops_screen import screen_maintenance

from .conftest import EVAL_DATA
from .ops_privacy_helpers import ops_privacy_ctx

OPS = EVAL_DATA / "ops"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
NB_HYPHEN = chr(0x2011)


@functools.cache
def gmao() -> SimGmao:
    return SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))


def labels() -> Mapping[SymptomCode, str]:
    return gmao().symptom_labels()


@functools.cache
def gold() -> tuple[MaintGoldItem, ...]:
    return tuple(load_maint_gold(MAINT_GOLD, gmao()))


# --------------------------------------------------------------------------- labels
def test_symptom_labels_cover_the_closed_codes_and_are_read_only() -> None:
    found = gmao().symptom_labels()
    assert set(found) == SYMPTOM_CODES
    assert all(isinstance(label, str) and label for label in found.values())
    with pytest.raises(TypeError):
        found["SYM-FUITE"] = "x"  # type: ignore[index]


def test_every_symptom_code_has_a_synonym_family() -> None:
    assert set(SYMPTOM_SYNONYMS) == SYMPTOM_CODES


# --------------------------------------------------------------------------- asset ids
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("La plieuse EQ-PLI-01 fuit.", ["EQ-PLI-01"]),
        ("EQ-PLI-01 puis EQ-PLI-01 encore", ["EQ-PLI-01"]),
        ("EQ-PLI-01 et EQ-SOU-02", ["EQ-PLI-01", "EQ-SOU-02"]),
        ("EQ" + NB_HYPHEN + "PLI" + NB_HYPHEN + "01 fuit", ["EQ-PLI-01"]),
        ("XEQ-PLI-01 ou EQ-PLI-011 ou eq-pli-01 ou EQ-PL-01", []),
        ("la plieuse de 135 tonnes", []),
    ],
    ids=["one", "repeated", "two", "nb_hyphen", "malformed", "none"],
)
def test_asset_ids_are_whole_prefixed_tokens(text: str, expected: list[str]) -> None:
    assert find_asset_ids(text) == expected
    assert ASSET_ID_RE.pattern.startswith("(?<![\\w-])")


@pytest.mark.parametrize(
    "text",
    ["Pourquoi la plieuse fuit-elle ?", "Comparez EQ-PLI-01 et EQ-PLI-02 : les deux fuient."],
    ids=["no_id", "two_ids"],
)
def test_no_asset_or_two_assets_are_not_identified(text: str) -> None:
    result = route_maintenance(text, labels())
    assert result == MaintRouteResult(
        flow=None, asset_id=None, symptom=None, refusal="asset_not_identified"
    )


def test_a_route_result_is_either_routed_or_refused() -> None:
    with pytest.raises(ValueError, match="route:inconsistent"):
        MaintRouteResult(flow="diagnosis", asset_id=None, symptom=None, refusal=None)
    with pytest.raises(ValueError, match="route:inconsistent"):
        MaintRouteResult(
            flow=None, asset_id=None, symptom="SYM-FUITE", refusal="asset_not_identified"
        )


# --------------------------------------------------------------------------- flow
@pytest.mark.parametrize(
    "text",
    [
        "Ouvre une demande de travail pour EQ-USI-01, ça vibre.",
        "Peux-tu préparer un bon de travail pour EQ-PLI-02 ?",
        "Crée-moi un ordre de travail sur EQ-LAS-01 : arrêt.",
        "Lancez une demande d'intervention pour EQ-PEI-01.",
        "Prépare un bon pour EQ-CMP-01, il fuit.",
        "Remplis une demande corrective pour EQ-SOU-01.",
    ],
    ids=["ouvre", "preparer", "cree_moi", "lancez", "un_bon_pour", "corrective"],
)
def test_a_creation_verb_and_a_work_request_noun_route_to_work_request(text: str) -> None:
    assert has_work_request_words(text)
    assert route_maintenance(text, labels()).flow == "work_request"


@pytest.mark.parametrize(
    "text",
    [
        "Que disent les bons de travail de EQ-USI-01 sur ce bruit ?",
        "Prépare un bon diagnostic de EQ-USI-01, il vibre.",
        "Une demande de travail existe-t-elle déjà pour EQ-PLI-01 ?",
        "Diagnostique EQ-PLI-01, ça fuit.",
    ],
    ids=["history_only", "bon_adjective", "noun_without_verb", "plain"],
)
def test_near_misses_stay_a_diagnosis(text: str) -> None:
    assert not has_work_request_words(text)
    assert route_maintenance(text, labels()).flow == "diagnosis"


# --------------------------------------------------------------------------- symptom
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Le vérin suinte depuis lundi.", "SYM-FUITE"),
        ("Il y a une fuite d'huile.", "SYM-FUITE"),
        ("La broche fait un bruit de claquement et vibre.", "SYM-BRUIT"),
        ("La machine s'arrête toute seule.", "SYM-ARRET"),
        ("Elle ne démarre plus ce matin.", "SYM-ARRET"),
        ("Le moteur chauffe beaucoup.", "SYM-CHAUFF"),
        ("Les pièces sont hors cote.", "SYM-PRECIS"),
        ("Un décalage des dimensions apparaît.", "SYM-PRECIS"),
        ("Un code d'erreur s'affiche au pupitre.", "SYM-ALARME"),
        ("Le voyant rouge clignote.", "SYM-ALARME"),
        ("On perd de la pression au circuit.", "SYM-PRESS"),
        ("Les soudures sont poreuses.", "SYM-QUALI"),
        ("La coupe laisse des bavures.", "SYM-QUALI"),
        ("Imprécision dimensionnelle sur les pliages.", "SYM-PRECIS"),
        ("Surchauffe du groupe.", "SYM-CHAUFF"),
    ],
)
def test_symptom_word_families(text: str, expected: str) -> None:
    assert symptom_of(text, labels()) == expected


def test_no_symptom_word_gives_none_and_the_history_is_unfiltered() -> None:
    text = "Diagnostique EQ-PLI-01 s'il te plaît."
    assert symptom_of(text, labels()) is None
    result = route_maintenance(text, labels())
    assert result.flow == "diagnosis" and result.symptom is None


def test_the_family_found_most_often_wins_then_the_first_one() -> None:
    # Two families of the leak (fuite, coule) against one of the pressure.
    assert symptom_of("Ça coule, une fuite fait chuter la pression.", labels()) == "SYM-FUITE"
    # One family each: the first mention wins.
    assert symptom_of("La pression baisse et ça vibre.", labels()) == "SYM-PRESS"
    assert symptom_of("Ça vibre et la pression baisse.", labels()) == "SYM-BRUIT"


def test_label_words_come_from_the_connector_labels() -> None:
    custom: dict[SymptomCode, str] = {"SYM-ALARME": "clignotement inhabituel"}
    assert symptom_of("Un clignotement inhabituel au tableau.", custom) == "SYM-ALARME"
    assert symptom_of("Un clignotement inhabituel au tableau.", labels()) is None


# --------------------------------------------------------------------------- gold
def test_every_gold_item_that_reaches_the_router_routes_as_written() -> None:
    ctx = ops_privacy_ctx()
    mismatches = []
    for item in gold():
        if item.route is None:
            continue
        redacted = screen_maintenance(item.request, ctx).redacted
        result = route_maintenance(redacted, labels())
        got = result.flow or result.refusal
        symptom_ok = item.symptom_code is None or result.symptom == item.symptom_code
        if got != item.route or result.asset_id != item.asset_id or not symptom_ok:
            mismatches.append(item.id)
    assert mismatches == []


# --------------------------------------------------------------------------- review near misses
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Du côté gauche, la plieuse EQ-PLI-01 fuit : quelles causes ?", "SYM-FUITE"),
        ("La plieuse EQ-PLI-01 fuit du côté gauche : quelles causes ?", "SYM-FUITE"),
        ("À côté du vérin, EQ-PLI-02 fait un bruit de claquement.", "SYM-BRUIT"),
        ("En pleine coupe, la table EQ-LAS-01 s'arrête net.", "SYM-ARRET"),
        ("Le service qualité signale que EQ-USI-01 chauffe.", "SYM-CHAUFF"),
        ("Le robot de soudure EQ-SOU-01 fuit au raccord.", "SYM-FUITE"),
        ("Le cordon d'alimentation de EQ-CMP-01 est usé, la machine s'arrête.", "SYM-ARRET"),
    ],
    ids=[
        "cote_side_first",
        "cote_side_after",
        "a_cote",
        "pleine_coupe",
        "service_qualite",
        "robot_de_soudure",
        "cordon_alim",
    ],
)
def test_words_that_name_something_else_name_no_symptom(text: str, expected: str) -> None:
    assert symptom_of(text, labels()) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Une cote de la pièce est fausse.",
        "Les cotes ne sont pas respectées : cote non respectée au perçage.",
        "La qualité de coupe se dégrade.",
        "Les coupes sont bavées.",
        "Mauvaise soudure sur le cadre.",
        "Le cordon de soudure est irrégulier.",
    ],
)
def test_dimension_and_quality_phrases_still_name_their_symptom(text: str) -> None:
    assert symptom_of(text, labels()) in ("SYM-PRECIS", "SYM-QUALI")


@pytest.mark.parametrize(
    "text",
    [
        "Fais le tour des bons de travail de EQ-PLI-01 : pourquoi elle fuit ?",
        "Lance l'analyse des bons de travail de EQ-USI-01, la broche chauffe.",
        "Prépare un diagnostic à partir des bons de travail de EQ-SOU-01, elle s'arrête.",
        "Fais un résumé des demandes de travail de EQ-PLI-01.",
        "Ouvre l'historique des ordres de travail de EQ-LAS-01.",
    ],
    ids=["fais_le_tour", "lance_analyse", "prepare_diagnostic", "fais_resume", "ouvre_historique"],
)
def test_work_orders_as_a_complement_stay_a_diagnosis(text: str) -> None:
    assert not has_work_request_words(text)
    assert route_maintenance(text, labels()).flow == "diagnosis"


@pytest.mark.parametrize(
    "text",
    [
        "Fais-moi une demande de travail pour EQ-USI-01.",
        "Faire un bon de travail pour EQ-PLI-01, il fuit.",
        "Ouvre pour la plieuse EQ-PLI-01 une demande d'intervention.",
        "Prépare moi un nouveau bon de travail pour EQ-CMP-01.",
        "Peux-tu remplir l'ordre de travail de EQ-LAS-01 ?",
    ],
    ids=["fais_moi", "faire_un_bon", "complement", "pronoun_adjective", "l_apostrophe"],
)
def test_a_work_request_object_after_the_verb_routes_to_work_request(text: str) -> None:
    assert has_work_request_words(text)
    assert route_maintenance(text, labels()).flow == "work_request"
