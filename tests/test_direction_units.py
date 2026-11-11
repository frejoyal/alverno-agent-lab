"""Units of the management orchestrator (ADR 0018): screen, routing, periods, overviews,
facts blocks, the ``direction`` access log and the graph with stub engines."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alverno_lab.agents.direction.graph import DirectionAgent, Section, assemble, verify_summary
from alverno_lab.agents.direction.overview import maintenance_block, orders_block, read_counts
from alverno_lab.agents.direction.route import Period, Scope, fold, parse_period, route, screen
from alverno_lab.connectors.sim import SimErp, SimGmao
from alverno_lab.evals.direction_oracle import load_oracle
from alverno_lab.llm import FakeChat
from alverno_lab.privacy.access_log import AccessLog, AccessLogError
from alverno_lab.privacy.direction_access_log import DirectionAccessLogRecord

from .conftest import EVAL_DATA

OPS = EVAL_DATA / "ops"


def _quarantine(text: str) -> Any:
    from alverno_lab.connectors.types import QuarantinedText

    return QuarantinedText(text=text, flags=())


@pytest.fixture(scope="module")
def erp() -> SimErp:
    return SimErp.from_yaml(OPS / "erp.yaml", quarantine=_quarantine)


@pytest.fixture(scope="module")
def gmao() -> SimGmao:
    return SimGmao.from_yaml(OPS / "gmao.yaml", quarantine=_quarantine)


@pytest.fixture(scope="module")
def oracle() -> Any:
    return load_oracle(OPS / "erp.yaml", OPS / "gmao.yaml")


# --------------------------------------------------------------------------- screen
@pytest.mark.parametrize(
    ("question", "code"),
    [
        ("Y a-t-il eu un accident sur la plieuse EQ-PLI-02 ?", "sensitive_out_of_scope"),
        ("Quel technicien a reçu une mesure disciplinaire ?", "sensitive_out_of_scope"),
        ("Quelle est la marge sur les commandes de CLI-001 ?", "restricted_info"),
        ("Quel est le délai moyen de réparation des machines ?", "profiling"),
        ("Quel est le temps d'arrêt de la plieuse en 2026 ?", "profiling"),
        ("Classe les opérateurs selon leur productivité.", "profiling"),
        ("Quel estimateur gère les commandes en retard ?", "person_join"),
        ("Qui a causé la panne de la plieuse EQ-PLI-01 ?", "person_join"),
        ("Les absences du soudeur expliquent-elles les arrêts ?", "person_join"),
    ],
)
def test_the_screen_refuses_with_the_first_rule_that_fires(question: str, code: str) -> None:
    assert screen(question) == code


@pytest.mark.parametrize(
    "question",
    [
        "Quelles machines ont eu le plus de pannes en 2026 ?",
        "Combien de bons correctifs pour la soudeuse EQ-SOU-01 en 2026 ?",
        "Un employé en arrêt de travail garde-t-il son assurance collective ?",
        "Quelle est la politique de télétravail ?",
    ],
)
def test_ordinary_management_questions_pass_the_screen(question: str) -> None:
    assert screen(question) is None


def test_fold_removes_accents_case_and_curly_apostrophes() -> None:
    assert fold("  Délai" + chr(0x2019) + "ARRÊT  ") == "delai'arret"


# --------------------------------------------------------------------------- routing
def test_an_overview_word_wins_over_a_policy_word() -> None:
    plan = route("Combien de bons correctifs pour la cabine de peinture EQ-PEI-01 en 2025 ?")
    assert plan is not None and plan.purposes == ("maintenance_overview",)
    assert plan.scope.asset_id == "EQ-PEI-01"


def test_both_overviews_route_in_the_fixed_order() -> None:
    plan = route("Portrait : machines en panne et commandes de cli-003 en octobre 2026")
    assert plan is not None
    assert plan.purposes == ("orders_overview", "maintenance_overview")
    assert plan.scope == Scope("CLI-003", None, Period(dt.date(2026, 10, 1), dt.date(2026, 10, 31)))


def test_policy_questions_route_to_their_modules() -> None:
    assert route("Que dit la procédure d'étalonnage ?").purposes == ("quality_policy",)  # type: ignore[union-attr]
    assert route("Combien de jours de vacances après 3 ans ?").purposes == ("hr_policy",)  # type: ignore[union-attr]
    assert route("Quelle équipe de hockey gagnera ?") is None


def test_an_hr_absence_phrase_is_not_a_machine_stop() -> None:
    plan = route("Un employé en arrêt de travail garde-t-il son assurance collective ?")
    assert plan is not None and plan.purposes == ("hr_policy",)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("pannes en février 2025", "2025-02-01", "2025-02-28"),
        ("pannes d'octobre 2026", "2026-10-01", "2026-10-31"),
        ("pannes du T3 2026", "2026-07-01", "2026-09-30"),
        ("pannes du premier trimestre de 2026", "2026-01-01", "2026-03-31"),
        ("pannes en 2025", "2025-01-01", "2025-12-31"),
    ],
)
def test_periods_are_read_month_then_quarter_then_year(text: str, start: str, end: str) -> None:
    period = parse_period(fold(text))
    assert period == Period(dt.date.fromisoformat(start), dt.date.fromisoformat(end))


def test_no_period_is_read_from_an_id_or_without_a_year() -> None:
    assert parse_period(fold("la commande CMD-2026-0401 ce mois-ci")) is None


# --------------------------------------------------------------------------- overviews
def test_orders_overview_matches_the_oracle_and_hides_the_person(erp: SimErp, oracle: Any) -> None:
    natural = next(iter(oracle.natural_person_labels))
    for account in (None, "CLI-003", "CLI-008"):
        block = orders_block(erp.orders_overview(account))
        assert read_counts(block) == oracle.orders_counts(account)
        assert natural not in block
    assert erp.orders_overview("CLI-999").total == 0


def test_maintenance_overview_matches_the_oracle_and_has_no_person_or_duration(
    gmao: SimGmao, oracle: Any
) -> None:
    for asset, start, end in (
        (None, dt.date(2026, 1, 1), dt.date(2026, 12, 31)),
        ("EQ-PLI-01", dt.date(2025, 1, 1), dt.date(2026, 12, 31)),
        (None, dt.date(2025, 2, 1), dt.date(2025, 2, 28)),
    ):
        block = maintenance_block(gmao.maintenance_overview(asset, start, end))
        assert read_counts(block) == oracle.maintenance_counts(asset, (start, end))
        assert not [v for v in oracle.technician_fields | oracle.remarks if v in block]
        assert "min" not in block.split() and "durée" not in block


def test_an_empty_period_says_so(gmao: SimGmao) -> None:
    block = maintenance_block(
        gmao.maintenance_overview(None, dt.date(2025, 2, 1), dt.date(2025, 2, 28))
    )
    assert "Aucun bon de travail correctif" in block
    assert read_counts(block) == {"maintenance.corrective": 0}


# --------------------------------------------------------------------------- verify
def test_verify_keeps_only_sentences_grounded_in_the_block() -> None:
    block = "- Commandes au total : 40\n- Commandes en retard : 11\n  - CMD-2026-0403"
    summary = "Il y a 40 commandes. 12 sont en retard. La CMD-2026-0403 attend. EQ-PLI-01 bloque."
    kept, dropped = verify_summary(summary, block)
    assert kept == "Il y a 40 commandes. La CMD-2026-0403 attend."
    assert dropped == 2


def test_sections_are_assembled_in_order_under_their_titles() -> None:
    text = assemble(
        [
            Section("quality_policy", "served", text="A"),
            Section("orders_overview", "served", text="B"),
        ]
    )
    assert text == "## Qualité\n\nA\n\n## Commandes\n\nB"


# --------------------------------------------------------------------------- access log
def test_the_direction_record_rules() -> None:
    base = {"event_id": "0" * 32, "ts": "2026-11-10T14:00Z", "channel": "cli", "latency_ms": 3}
    DirectionAccessLogRecord(**base, decision="refused", reason="profiling")
    DirectionAccessLogRecord(
        **base, decision="answered", purposes=("orders_overview",), sections=("served",)
    )
    for bad in (
        {"decision": "refused"},
        {"decision": "answered", "reason": "profiling"},
        {"decision": "answered", "purposes": ("orders_overview",), "sections": ()},
        {"decision": "refused", "reason": "profiling", "sections": ("served",)},
        {"decision": "answered", "question": "texte"},
        {"decision": "answered", "channel": "web"},
    ):
        with pytest.raises(ValidationError):
            DirectionAccessLogRecord(**{**base, **bad})


def test_the_direction_log_is_its_own_prefix(tmp_path: Path) -> None:
    log = AccessLog(tmp_path, prefix="direction", record_type=DirectionAccessLogRecord)
    log.append(log.record(channel="mcp", decision="refused", reason="out_of_scope", latency_ms=1))
    assert [p.name[:10] for p in tmp_path.iterdir()] == ["direction-"]
    with pytest.raises(AccessLogError):
        AccessLog(tmp_path, prefix="direction")  # type: ignore[call-overload]


# --------------------------------------------------------------------------- graph
class _Calls:
    def __init__(self) -> None:
        self.n = 0


def _agent(tmp_path: Path, erp: SimErp, gmao: SimGmao, calls: _Calls, **kw: Any) -> DirectionAgent:
    from alverno_lab.agents.quality.graph import QualityAnswer

    def answer(question: str) -> QualityAnswer:
        calls.n += 1
        return QualityAnswer(
            question="",
            answer="Réponse.",
            citations=["QP-001#01"],
            cited_doc_ids=[],
            confidence=1.0,
            refused=False,
        )

    log = AccessLog(tmp_path, prefix="direction", record_type=DirectionAccessLogRecord)
    return DirectionAgent(quality=answer, hr=answer, erp=erp, gmao=gmao, log=log, **kw)


def test_a_refused_question_reaches_no_engine_and_is_logged(
    tmp_path: Path, erp: SimErp, gmao: SimGmao
) -> None:
    calls = _Calls()
    agent = _agent(tmp_path, erp, gmao, calls)
    result = agent.ask("Quel estimateur gère les commandes en retard ?")
    assert result.refused and result.refusal_reason == "person_join"
    assert calls.n == 0
    (line,) = (
        (tmp_path / next(iter(p.name for p in tmp_path.iterdir()))).read_text("utf-8").splitlines()
    )
    assert '"reason":"person_join"' in line and "estimateur" not in line


def test_the_summary_model_sees_one_block_and_never_the_question(
    tmp_path: Path, erp: SimErp, gmao: SimGmao
) -> None:
    chat = FakeChat(responder=lambda s, u, schema: {"summary": "Rien de plus."})
    agent = _agent(tmp_path, erp, gmao, _Calls(), summarizer=chat)
    question = "Portrait : commandes et pannes de machines en 2026, s'il vous plaît"
    result = agent.ask(question)
    assert [s["purpose"] for s in result.sections] == ["orders_overview", "maintenance_overview"]
    assert len(chat.calls) == 2
    for _system, user in chat.calls:
        assert "s'il vous plaît" not in user
        assert ("Synthèse des commandes" in user) != ("Synthèse de l'entretien" in user)
