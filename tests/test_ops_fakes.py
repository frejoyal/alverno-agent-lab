"""The 3a fake responder (I7 spec decision 3, ADR 0015 « Moteurs simulés et mode outils »).

:func:`~alverno_lab.agents.ops.fakes.ops_draft_echo` reads the prompt only. On synthetic
facts blocks of every status, with and without the optional lines, its body starts with
``{salutation}``, ends with ``{signature}``, restates every fact value verbatim, cites the
order id of the block and passes ``verify``; its own wording holds no digit and no status
form. The orders here are synthetic: no fixture value, no gold field.
"""

from __future__ import annotations

import datetime as dt
from typing import get_args

import pytest

from alverno_lab.agents.ops.fakes import (
    FACT_SENTENCES,
    FAKE_OPS_MODEL,
    make_ops_fake_chat,
    ops_draft_echo,
)
from alverno_lab.agents.ops.rendering import FORM_STATUS, find_status_forms
from alverno_lab.agents.service import (
    DRAFT_SYSTEM_PROMPT,
    FACTS_HEADER,
    MAX_BODY_CHARS,
    DraftBody,
    OrderFacts,
    order_facts,
    verify_body,
)
from alverno_lab.agents.service.drafting import SALUTATION, SIGNATURE
from alverno_lab.connectors.types import OrderStatus, OrderView, QuarantinedText

STATUSES: tuple[OrderStatus, ...] = get_args(OrderStatus)
ORDER = "CMD-2099-0001"


class Labels:
    """Synthetic code labels."""

    def carrier_label(self, code: str) -> str | None:
        return "Transport Bleu" if code == "TRP-B" else None

    def delay_label(self, code: str) -> str | None:
        return "attente de matière" if code == "RET-MAT" else None


def synthetic(status: OrderStatus, *, full: bool) -> OrderView:
    """A synthetic order view; ``full`` adds every optional fact."""
    view = OrderView(
        order_id=ORDER,
        account_id="CLI-999",
        part_ref="ZZ-901",
        qty_ordered=40,
        qty_shipped=15 if full else 0,
        status=status,
        promised_date=dt.date(2099, 3, 1),
    )
    if not full:
        return view
    note = QuarantinedText(text="Livraison au quai arrière, de jour.", flags=())
    return view.model_copy(
        update={
            "revised_date": dt.date(2099, 3, 22),
            "carrier_code": "TRP-B",
            "tracking_ref": "TRK-765432",
            "delay_reason_code": "RET-MAT",
            "customer_visible_note": note,
        }
    )


@pytest.mark.parametrize("full", [False, True], ids=["bare", "full"])
@pytest.mark.parametrize("status", STATUSES)
def test_the_echo_restates_every_fact_and_passes_verify(status: OrderStatus, full: bool) -> None:
    view = synthetic(status, full=full)
    facts = order_facts(view, Labels())
    answer = DraftBody.model_validate(ops_draft_echo(DRAFT_SYSTEM_PROMPT, facts.block, DraftBody))
    body = answer.body
    assert len(body) <= MAX_BODY_CHARS
    assert body.startswith(SALUTATION) and body.endswith(SIGNATURE)
    assert answer.cited_ids == [ORDER]
    for line in facts.lines():
        assert line.split(" : ", 1)[1] in body, line.split(" : ", 1)[0]
    forms = find_status_forms(body)
    assert forms and all(FORM_STATUS[form] == status for form in forms), "status"
    assert verify_body(body, [ORDER], view, facts.block, frozenset({ORDER}))
    assert len(body.splitlines()) == len(facts.lines()) + 4


def test_every_fact_label_has_its_sentence_without_digit_or_status() -> None:
    facts = order_facts(synthetic("expediee", full=True), Labels())
    labels = [line.split(" : ", 1)[0] for line in facts.lines()]
    assert set(labels) == set(FACT_SENTENCES)
    for label, sentence in FACT_SENTENCES.items():
        assert "{value}" in sentence, label
        wording = sentence.replace("{value}", "")
        assert not any(char.isdigit() for char in wording), label
        assert find_status_forms(wording) == [], label


def test_the_echo_reads_the_block_only_after_its_header() -> None:
    facts = order_facts(synthetic("planifiee", full=False), Labels())
    user = f"Commande : CMD-2099-0002\n\n{facts.block}"
    answer = ops_draft_echo("", user, DraftBody)
    assert answer["cited_ids"] == [ORDER]
    assert "CMD-2099-0002" not in str(answer["body"])


def test_the_echo_without_a_block_gives_the_two_placeholders_and_no_citation() -> None:
    answer = ops_draft_echo("", "rien à lire", DraftBody)
    assert answer == {"body": f"{SALUTATION}\n\n{SIGNATURE}", "cited_ids": []}
    DraftBody.model_validate(answer)


def test_the_echo_keeps_an_unknown_label_and_skips_malformed_lines() -> None:
    user = "\n".join(
        [
            FACTS_HEADER,
            "Commande : pas un identifiant",
            "Atelier : le second",
            "ligne sans séparateur",
            "Vide : ",
            "Note visible par le client : Tout va bien !",
        ]
    )
    answer = ops_draft_echo("", user, DraftBody)
    body = str(answer["body"])
    assert answer["cited_ids"] == []
    assert "Atelier : le second." in body
    assert "ligne sans séparateur" not in body and "Vide" not in body
    assert "Tout va bien !" in body and "Tout va bien !." not in body


def test_make_ops_fake_chat_answers_through_fake_chat() -> None:
    chat = make_ops_fake_chat()
    assert chat.model_name == FAKE_OPS_MODEL == "fake-ops"
    facts: OrderFacts = order_facts(synthetic("recue", full=True), Labels())
    drafted = chat.complete_json(DRAFT_SYSTEM_PROMPT, facts.block, DraftBody)
    assert drafted.cited_ids == [ORDER]
    assert chat.calls == [(DRAFT_SYSTEM_PROMPT, facts.block)]
    assert make_ops_fake_chat("fake-other").model_name == "fake-other"
