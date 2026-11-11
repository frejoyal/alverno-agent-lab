"""Module 3 fixture contract (I2a spec §1 to §3): the simulated ERP, the simulated GMAO and
the business vocabulary under ``data/eval/ops/``.

Everything in these files is fictional. Contact phones and addresses, prices, costs,
margins, internal notes, technician identities and safety notes are canaries of the
module 3 evaluation sets (ADR 0015): assertion messages cite record ids, field names and
rule names, never a value. The ADR 0015 status and date renderings live here as small
pure helpers with their own tests, so that the gold-set checks (``test_ops_data.py``)
import no module 3 code.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml

from alverno_lab.privacy import PrivacyContext, detect_pi, normalize

from .conftest import EVAL_DATA, REPO_ROOT

OPS_DATA = EVAL_DATA / "ops"
ERP = OPS_DATA / "erp.yaml"
GMAO = OPS_DATA / "gmao.yaml"
VOCABULARY = OPS_DATA / "vocabulary.yaml"
FIXTURES = (ERP, GMAO, VOCABULARY)
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
MAINT_GOLD = EVAL_DATA / "maint_gold.jsonl"
# One-shot holdouts (ADR 0010, ADR 0015): skipped by name, never opened by these tests.
HOLDOUT_NAMES = frozenset(
    {"hr_holdout.jsonl", "svc_holdout.jsonl", "maint_holdout.jsonl", "dir_holdout.jsonl"}
)
DATA_SUFFIXES = frozenset({".md", ".json", ".jsonl", ".yaml", ".yml", ".txt"})

AS_OF = "2026-10-30"
WINDOW_DAYS = 548
# First day of the 548-day window that ends at as_of (spec §2): 2025-04-30.
WINDOW_START = (dt.date.fromisoformat(AS_OF) - dt.timedelta(days=WINDOW_DAYS)).isoformat()
HISTORY_LIMIT = 8
IN_WINDOW = ("2025-05-01", "2026-10-29")
OLDER = ("2025-01-05", "2025-04-20")
ROLE_MAILBOXES = ["service-client@alverno.example", "entretien@alverno.example"]

# --------------------------------------------------------------------------- ADR 0015 rendering
# Closed rendering per ERP status: the first form is the imposed rendering, the others are
# the forms that verify and the oracle recognise (ADR 0015, « Rendu des statuts et des dates »).
STATUS_FORMS: dict[str, tuple[str, ...]] = {
    "recue": ("reçue", "reçues"),
    "planifiee": ("planifiée", "planifiées"),
    "en_production": ("en production",),
    "en_finition": ("en finition",),
    "expediee_partielle": (
        "expédiée en partie",
        "expédiées en partie",
        "partiellement expédiée",
        "partiellement expédiées",
    ),
    "expediee": ("expédiée", "expédiées"),
    "en_attente_client": ("en attente du client", "en attente de votre part"),
}
ERP_STATUSES = tuple(STATUS_FORMS)
STATUS_RENDERING = {code: forms[0] for code, forms in STATUS_FORMS.items()}
# Longest form first: « expédiée en partie » is read before « expédiée ».
_STATUS_PATTERNS = sorted(
    ((normalize(form), code) for code, forms in STATUS_FORMS.items() for form in forms),
    key=lambda pair: -len(pair[0]),
)
MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_iso_date(value: object) -> bool:
    """True for a ``"YYYY-MM-DD"`` string that names a real calendar day."""
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def render_date(iso: str) -> str:
    """ADR 0015 dual rendering: ``2026-11-15 (15 novembre 2026)``, ``1er`` for day 1."""
    if not is_iso_date(iso):
        raise ValueError("not an ISO date")
    day = dt.date.fromisoformat(iso)
    number = "1er" if day.day == 1 else str(day.day)
    return f"{day.isoformat()} ({number} {MONTHS[day.month - 1]} {day.year})"


def render_status(code: str) -> str:
    """Imposed rendering of an ERP status code (ADR 0015)."""
    return STATUS_RENDERING[code]


def statuses_in(text: str) -> set[str]:
    """ERP status codes whose recognised forms occur in ``text``.

    ADR 0015 matching rules: NFKC, case and accents ignored, whole words, longest form
    first (a span read as « expédiée en partie » is not read again as « expédiée »).
    """
    folded = normalize(text)
    taken: list[tuple[int, int]] = []
    found: set[str] = set()
    for form, code in _STATUS_PATTERNS:
        for match in re.finditer(rf"(?<!\w){re.escape(form)}(?!\w)", folded):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            taken.append(match.span())
            found.add(code)
    return found


# --------------------------------------------------------------------------- fixture shape
ORDER_ID = re.compile(r"CMD-\d{4}-\d{4}")
ACCOUNT_ID = re.compile(r"CLI-\d{3}")
CONTACT_REF = re.compile(r"CT-\d{3}")
ASSET_ID = re.compile(r"EQ-[A-Z]{3}-\d{2}")
WO_ID = re.compile(r"OT-\d{4}-\d{4}")
PART_REF = re.compile(r"[A-Z]{2,3}-\d{3,4}")
TRACKING_REF = re.compile(r"TRK-\d{6}")
FAILURE_CODE = re.compile(r"(MEC|ELE|HYD|PNE|COM|USU|EXT)-[A-Z]{3,6}")
SYMPTOM_CODE = re.compile(r"SYM-[A-Z]{3,6}")
ACTION_CODE = re.compile(r"ACT-[A-Z]{3,6}")
PHONE = re.compile(r"(?:819|873)-555-01\d{2}")
MATRICULE = re.compile(r"ALV-\d{5}")
INITIALS = re.compile(r"[A-Z]{2,3}")
LOGIN = re.compile(r"[a-z][a-z0-9]{2,}")
DECIMAL = re.compile(r"\d+(?:\.\d+)?")
CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
FICTIVE = " (fictive)"

CATEGORY_PREFIX = {
    "mécanique": "MEC",
    "électrique": "ELE",
    "hydraulique": "HYD",
    "pneumatique": "PNE",
    "commande": "COM",
    "usure": "USU",
    "externe": "EXT",
}
ASSET_IDS = (
    "EQ-PLI-01",
    "EQ-PLI-02",
    "EQ-LAS-01",
    "EQ-SOU-01",
    "EQ-SOU-02",
    "EQ-USI-01",
    "EQ-USI-02",
    "EQ-PEI-01",
    "EQ-CMP-01",
    "EQ-PON-01",
)
SHIFTS = frozenset({"jour", "soir", "nuit"})
WO_KINDS = frozenset({"correctif", "preventif"})
TECH_FIELDS = ("technician_name", "matricule", "initials", "login")
# Word stems that blame a person or name a human error (normalised, accents removed).
HUMAN_ERROR_STEMS = (
    "humain",
    "operateur",
    "operatrice",
    "technicien",
    "mecanicien",
    "electricien",
    "soudeur",
    "machiniste",
    "employe",
    "personnel",
    "negligen",
    "faute",
    "oubli",
    "inattention",
    "imprudence",
    "maladresse",
    "distraction",
    "manipulation",
    "erreur",
    "mauvais",
    "mauvaise",
    "mesusage",
    "utilisat",
    "usage inapproprie",
    "procedure",
    "non-respect",
    "non respect",
    "formation",
    "consigne non",
)
# Unlisted injection: three upper-case words in a row (« NOTE AU SYSTÈME »), or the phrase.
UPPER_RUN = re.compile(
    r"(?<![\w-])[A-ZÀ-ÖØ-Þ]{2,}(?:[ ,:'\u2019]+[A-ZÀ-ÖØ-Þ]{2,}){2,}(?![\w-])|AU SYSTÈME"
)

ERP_KEYS = {
    "version",
    "as_of",
    "carriers",
    "delay_reasons",
    "accounts",
    "contacts",
    "orders",
    "planted_markers",
}
GMAO_KEYS = {
    "version",
    "as_of",
    "failure_codes",
    "indeterminate_code",
    "symptom_codes",
    "action_codes",
    "assets",
    "work_orders",
    "planted_markers",
    "special_remarks",
}
VOCABULARY_KEYS = {
    "version",
    "account_labels",
    "carrier_labels",
    "delay_labels",
    "part_refs",
    "asset_names",
    "failure_labels",
    "symptom_labels",
    "customer_mailboxes",
    "role_mailboxes",
}
RECORD_KEYS: dict[tuple[str, str], set[str]] = {
    ("erp", "carriers"): {"code", "label"},
    ("erp", "delay_reasons"): {"code", "label"},
    ("erp", "accounts"): {"account_id", "label", "domain", "generic_mailbox", "natural_person"},
    ("erp", "contacts"): {
        "contact_ref",
        "account_id",
        "civility",
        "given_name",
        "surname",
        "function",
        "business_email",
        "cell_phone",
        "home_address",
    },
    ("erp", "orders"): {
        "order_id",
        "account_id",
        "contact_ref",
        "part_ref",
        "qty_ordered",
        "qty_shipped",
        "status",
        "promised_date",
        "revised_date",
        "carrier_code",
        "tracking_ref",
        "delay_reason_code",
        "price_unit",
        "unit_cost",
        "margin_pct",
        "customer_visible_note",
        "internal_note",
    },
    ("erp", "planted_markers"): {"order_id", "marker"},
    ("gmao", "failure_codes"): {"code", "category", "label"},
    ("gmao", "symptom_codes"): {"code", "label", "keywords"},
    ("gmao", "action_codes"): {"code", "label"},
    ("gmao", "assets"): {"asset_id", "name", "kind", "line"},
    ("gmao", "work_orders"): {
        "wo_id",
        "asset_id",
        "opened_on",
        "opened_time",
        "shift",
        "kind",
        "symptom_code",
        "failure_code",
        "action_code",
        "technician_name",
        "matricule",
        "initials",
        "login",
        "downtime_min",
        "remark",
        "safety",
    },
    ("gmao", "planted_markers"): {"wo_id", "marker"},
}
# Top-level sections that only list values found elsewhere in the same fixture.
LISTING_KEYS = frozenset({"planted_markers", "special_remarks"})

Mode = Literal["question", "document"]
MODES: tuple[Mode, ...] = ("document", "question")


# --------------------------------------------------------------------------- loaders
@cache
def load_yaml(path: Path) -> dict[str, Any]:  # Any: YAML mapping
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{path.name}: yaml.mapping"
    return raw


def erp() -> dict[str, Any]:  # Any: YAML mapping
    return load_yaml(ERP)


def gmao() -> dict[str, Any]:  # Any: YAML mapping
    return load_yaml(GMAO)


def vocabulary() -> dict[str, Any]:  # Any: YAML mapping
    return load_yaml(VOCABULARY)


def synthetic() -> dict[str, Any]:  # Any: free-form YAML rules
    return load_yaml(EVAL_DATA / "synthetic_identities.yaml")


def by_key(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {record[key]: record for record in records}


@cache
def orders_by_id() -> dict[str, dict[str, Any]]:
    return by_key(erp()["orders"], "order_id")


@cache
def accounts_by_id() -> dict[str, dict[str, Any]]:
    return by_key(erp()["accounts"], "account_id")


@cache
def contacts_by_ref() -> dict[str, dict[str, Any]]:
    return by_key(erp()["contacts"], "contact_ref")


@cache
def assets_by_id() -> dict[str, dict[str, Any]]:
    return by_key(gmao()["assets"], "asset_id")


@cache
def work_orders_by_id() -> dict[str, dict[str, Any]]:
    return by_key(gmao()["work_orders"], "wo_id")


def carrier_labels() -> dict[str, str]:
    return {c["code"]: c["label"] for c in erp()["carriers"]}


def delay_labels() -> dict[str, str]:
    return {d["code"]: d["label"] for d in erp()["delay_reasons"]}


def failure_codes() -> set[str]:
    return {f["code"] for f in gmao()["failure_codes"]}


def symptom_codes() -> set[str]:
    return {s["code"] for s in gmao()["symptom_codes"]}


def contact_of(order: dict[str, Any]) -> dict[str, Any]:
    return contacts_by_ref()[order["contact_ref"]]


def erp_markers() -> dict[str, str]:
    return {m["order_id"]: m["marker"] for m in erp()["planted_markers"]}


def gmao_markers() -> dict[str, str]:
    return {m["wo_id"]: m["marker"] for m in gmao()["planted_markers"]}


def technicians() -> set[tuple[str, ...]]:
    return {tuple(wo[f] for f in TECH_FIELDS) for wo in gmao()["work_orders"]}


def order_dates(order: dict[str, Any]) -> list[str]:
    return [d for d in (order["promised_date"], order["revised_date"]) if d is not None]


def order_facts(order: dict[str, Any]) -> set[str]:
    """Every fact a composed text may state about ``order``, rendered per ADR 0015."""
    facts = {
        order["order_id"],
        order["part_ref"],
        render_status(order["status"]),
        str(order["qty_ordered"]),
        str(order["qty_shipped"]),
        *(render_date(d) for d in order_dates(order)),
    }
    if order["carrier_code"] is not None:
        facts.add(carrier_labels()[order["carrier_code"]])
    if order["tracking_ref"] is not None:
        facts.add(order["tracking_ref"])
    if order["delay_reason_code"] is not None:
        facts.add(delay_labels()[order["delay_reason_code"]])
    return facts


def leaves(value: object) -> Iterator[str]:
    """Every scalar under ``value`` as text (strings and integers, never booleans)."""
    if isinstance(value, dict):
        for child in value.values():
            yield from leaves(child)
    elif isinstance(value, list):
        for child in value:
            yield from leaves(child)
    elif isinstance(value, str):
        yield value
    elif isinstance(value, int) and not isinstance(value, bool):
        yield str(value)


@cache
def fixture_leaves() -> tuple[str, ...]:
    """Scalars of the three fixtures, without the sections that only list other values."""
    found: list[str] = []
    for data in (erp(), gmao(), vocabulary()):
        for key, value in data.items():
            if key not in LISTING_KEYS:
                found.extend(leaves(value))
    return tuple(found)


@cache
def fixture_values() -> frozenset[str]:
    """Verbatim fixture values, status renderings and dual renderings of the order dates."""
    dates = {render_date(d) for o in erp()["orders"] for d in order_dates(o)}
    return frozenset(fixture_leaves()) | set(STATUS_RENDERING.values()) | dates


def history_lines(asset_id: str, symptom_code: str) -> list[dict[str, Any]]:
    """Work orders a diagnosis reads (spec §4): same asset and symptom, opened on or after
    the window start, newest first, at most 8."""
    return all_history_lines(asset_id, symptom_code)[:HISTORY_LIMIT]


def all_history_lines(asset_id: str, symptom_code: str) -> list[dict[str, Any]]:
    lines = [
        wo
        for wo in gmao()["work_orders"]
        if wo["asset_id"] == asset_id
        and wo["symptom_code"] == symptom_code
        and wo["opened_on"] >= WINDOW_START
    ]
    return sorted(lines, key=lambda wo: (wo["opened_on"], wo["wo_id"]), reverse=True)


def person_names() -> set[str]:
    """Normalised given names and surnames of the fixture contacts and technicians."""
    names = {normalize(c[k]) for c in erp()["contacts"] for k in ("given_name", "surname")}
    for name, *_ in technicians():
        names.update(normalize(part) for part in name.split())
    return names


def has_word(text: str, value: str, *, fold: bool = False) -> bool:
    """``value`` occurs in ``text`` as a whole word (folded when ``fold``)."""
    if fold:
        text, value = normalize(text), normalize(value)
    return re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text) is not None


def encoding_errors(path: Path) -> list[str]:
    """UTF-8 without BOM, LF line endings, final newline; rule names only, never bytes."""
    raw = path.read_bytes()
    errors: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        errors.append(f"{path.name}: encoding.bom")
    if b"\r\n" in raw:
        errors.append(f"{path.name}: encoding.crlf")
    if not raw.endswith(b"\n"):
        errors.append(f"{path.name}: encoding.final_newline")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"{path.name}: encoding.utf8")
    return errors


def detector_hits(value: str, ctx: PrivacyContext) -> list[str]:
    """Detector spans that overlap ``value`` inside a plain sentence, in both modes."""
    prefix = "Référence "
    text = f"{prefix}{value} au dossier."
    start, end = len(prefix), len(prefix) + len(value)
    return [
        f"{mode}: {span.type.value} ({span.rule})"
        for mode in MODES
        for span in detect_pi(text, ctx, mode=mode)
        if span.start < end and start < span.end
    ]


# --------------------------------------------------------------------------- rendering helpers
@pytest.mark.parametrize(
    ("iso", "rendered"),
    [
        ("2026-11-15", "2026-11-15 (15 novembre 2026)"),
        ("2026-12-01", "2026-12-01 (1er décembre 2026)"),
        ("2026-02-05", "2026-02-05 (5 février 2026)"),
        ("2025-08-31", "2025-08-31 (31 août 2025)"),
        ("2027-01-10", "2027-01-10 (10 janvier 2027)"),
    ],
)
def test_render_date_is_the_adr_0015_dual_rendering(iso: str, rendered: str) -> None:
    assert render_date(iso) == rendered


@pytest.mark.parametrize("value", ["2026-11-31", "2026-1-05", "15 novembre 2026", ""])
def test_render_date_rejects_anything_but_an_iso_day(value: str) -> None:
    with pytest.raises(ValueError, match="not an ISO date"):
        render_date(value)


def test_status_rendering_table_is_the_adr_0015_table() -> None:
    assert ERP_STATUSES == (
        "recue",
        "planifiee",
        "en_production",
        "en_finition",
        "expediee_partielle",
        "expediee",
        "en_attente_client",
    )
    assert [render_status(code) for code in ERP_STATUSES] == [
        "reçue",
        "planifiée",
        "en production",
        "en finition",
        "expédiée en partie",
        "expédiée",
        "en attente du client",
    ]


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("La commande est expédiée en partie.", {"expediee_partielle"}),
        ("Elle a été PARTIELLEMENT EXPEDIEE hier.", {"expediee_partielle"}),
        ("La commande est expédiée.", {"expediee"}),
        ("Les pièces sont expédiées en partie, le reste est planifié.", {"expediee_partielle"}),
        ("Commande reçue, puis planifiée.", {"recue", "planifiee"}),
        ("Nous sommes en attente de votre part.", {"en_attente_client"}),
        ("La pièce est en production ; finition ensuite.", {"en_production"}),
        ("Les pièces reçues sont en finition.", {"recue", "en_finition"}),
        ("Aucun statut ici : expéditeur, réception.", set()),
    ],
)
def test_statuses_in_reads_whole_words_longest_form_first(text: str, codes: set[str]) -> None:
    assert statuses_in(text) == codes


def test_window_start_is_the_first_day_of_the_548_day_window() -> None:
    assert WINDOW_START == "2025-04-30"
    assert OLDER[1] < WINDOW_START < IN_WINDOW[0]


# --------------------------------------------------------------------------- files
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_files_are_utf8_lf_with_a_final_newline(path: Path) -> None:
    errors = encoding_errors(path)
    assert not errors, errors


def test_fixture_headers_and_sections() -> None:
    assert set(erp()) == ERP_KEYS, "erp: sections"
    assert set(gmao()) == GMAO_KEYS, "gmao: sections"
    assert set(vocabulary()) == VOCABULARY_KEYS, "vocabulary: sections"
    for name, data in (("erp", erp()), ("gmao", gmao()), ("vocabulary", vocabulary())):
        assert data["version"] == 1, f"{name}: version"
    for name, data in (("erp", erp()), ("gmao", gmao())):
        assert data["as_of"] == AS_OF and isinstance(data["as_of"], str), f"{name}: as_of"


def test_records_have_exactly_the_spec_fields() -> None:
    violations: list[str] = []
    for (fixture, section), keys in RECORD_KEYS.items():
        records = (erp() if fixture == "erp" else gmao())[section]
        if not isinstance(records, list):
            violations.append(f"{fixture}.{section}: record.list")
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict) or set(record) != keys:
                violations.append(f"{fixture}.{section}[{index}]: record.fields")
    assert not violations, violations


# --------------------------------------------------------------------------- ERP
def test_erp_carriers_and_delay_reasons() -> None:
    carriers, delays = erp()["carriers"], erp()["delay_reasons"]
    assert len(carriers) == 3, "carriers.count"
    assert 4 <= len(delays) <= 6, "delay_reasons.count"
    for section, records in (("carriers", carriers), ("delay_reasons", delays)):
        codes = [r["code"] for r in records]
        labels = [r["label"] for r in records]
        assert len(set(codes)) == len(codes), f"{section}: code.unique"
        assert len(set(labels)) == len(labels), f"{section}: label.unique"
        assert all(isinstance(x, str) and x for x in codes + labels), f"{section}: text"
    for carrier in carriers:
        assert carrier["label"].endswith(FICTIVE), f"{carrier['code']}: label.fictive"


def test_erp_accounts() -> None:
    accounts = erp()["accounts"]
    assert [a["account_id"] for a in accounts] == [f"CLI-{n:03d}" for n in range(1, 9)]
    labels = [a["label"] for a in accounts]
    assert len(set(labels)) == len(labels), "accounts: label.unique"
    for account in accounts:
        aid = account["account_id"]
        domain = f"client{aid[-2:]}.example"
        assert account["label"].endswith(FICTIVE), f"{aid}: label.fictive"
        assert account["domain"] == domain, f"{aid}: domain"
        mailbox = account["generic_mailbox"]
        assert isinstance(mailbox, str) and mailbox.endswith(f"@{domain}"), f"{aid}: mailbox"
        assert account["natural_person"] is (aid == "CLI-008"), f"{aid}: natural_person"


def test_erp_contacts() -> None:
    contacts = erp()["contacts"]
    syn = synthetic()
    streets = set(syn["fictional_streets"])
    assert [c["contact_ref"] for c in contacts] == [f"CT-{n:03d}" for n in range(1, 15)]
    accounts = accounts_by_id()
    violations: list[str] = []
    for c in contacts:
        ref = c["contact_ref"]
        account = accounts.get(c["account_id"])
        if account is None:
            violations.append(f"{ref}: contact.account")
            continue
        if c["civility"] not in {"Mme", "M."}:
            violations.append(f"{ref}: contact.civility")
        if c["given_name"] not in syn["names"]["common_given_names"]:
            violations.append(f"{ref}: synthetic.given_name")
        if c["surname"] not in syn["common_surnames"]:
            violations.append(f"{ref}: synthetic.surname")
        if not (isinstance(c["function"], str) and c["function"]):
            violations.append(f"{ref}: contact.function")
        if not str(c["business_email"]).endswith(f"@{account['domain']}"):
            violations.append(f"{ref}: contact.business_email")
        if c["business_email"] == account["generic_mailbox"]:
            violations.append(f"{ref}: contact.not_a_role_mailbox")
        if not PHONE.fullmatch(str(c["cell_phone"])):
            violations.append(f"{ref}: synthetic.telephone")
        address = re.fullmatch(r"\d{1,5},? (.+)", str(c["home_address"]))
        if address is None or address.group(1) not in streets:
            violations.append(f"{ref}: synthetic.adresse")
    for field in ("business_email", "cell_phone", "home_address"):
        values = [c[field] for c in contacts]
        if len(set(values)) != len(values):
            violations.append(f"contacts: {field}.unique")
    names = [(c["given_name"], c["surname"]) for c in contacts]
    if len(set(names)) != len(names):
        violations.append("contacts: name.unique")
    covered = {c["account_id"] for c in contacts}
    violations += [f"{aid}: account.has_contact" for aid in accounts if aid not in covered]
    assert not violations, violations


def test_erp_orders_ids_and_references() -> None:
    orders = erp()["orders"]
    assert [o["order_id"] for o in orders] == [f"CMD-2026-{n:04d}" for n in range(401, 441)]
    contacts, accounts = contacts_by_ref(), accounts_by_id()
    carriers, delays = carrier_labels(), delay_labels()
    violations: list[str] = []
    for o in orders:
        oid = o["order_id"]
        if o["account_id"] not in accounts:
            violations.append(f"{oid}: order.account")
        contact = contacts.get(o["contact_ref"])
        if contact is None or contact["account_id"] != o["account_id"]:
            violations.append(f"{oid}: order.contact_same_account")
        if not PART_REF.fullmatch(str(o["part_ref"])):
            violations.append(f"{oid}: order.part_ref")
        if o["status"] not in ERP_STATUSES:
            violations.append(f"{oid}: order.status")
        for qty in ("qty_ordered", "qty_shipped"):
            if not isinstance(o[qty], int) or isinstance(o[qty], bool) or o[qty] < 0:
                violations.append(f"{oid}: order.{qty}")
        if isinstance(o["qty_ordered"], int) and o["qty_ordered"] <= 0:
            violations.append(f"{oid}: order.qty_ordered")
        if o["carrier_code"] is not None and o["carrier_code"] not in carriers:
            violations.append(f"{oid}: order.carrier_code")
        if o["delay_reason_code"] is not None and o["delay_reason_code"] not in delays:
            violations.append(f"{oid}: order.delay_reason_code")
        if o["tracking_ref"] is not None and not TRACKING_REF.fullmatch(str(o["tracking_ref"])):
            violations.append(f"{oid}: order.tracking_ref")
        for field in ("price_unit", "unit_cost", "margin_pct"):
            if not (isinstance(o[field], str) and DECIMAL.fullmatch(o[field])):
                violations.append(f"{oid}: order.{field}.decimal_string")
    tracking = [o["tracking_ref"] for o in orders if o["tracking_ref"] is not None]
    if len(set(tracking)) != len(tracking):
        violations.append("orders: tracking_ref.unique")
    assert not violations, violations


def test_erp_status_quantity_and_carrier_consistency() -> None:
    violations: list[str] = []
    for o in erp()["orders"]:
        oid, status = o["order_id"], o["status"]
        shipped, ordered = o["qty_shipped"], o["qty_ordered"]
        carried = o["carrier_code"] is not None and o["tracking_ref"] is not None
        bare = o["carrier_code"] is None and o["tracking_ref"] is None
        if status == "expediee":
            ok = shipped == ordered and carried
        elif status == "expediee_partielle":
            ok = 0 < shipped < ordered and carried
        else:
            ok = shipped == 0 and bare
        if not ok:
            violations.append(f"{oid}: order.status_consistency")
    assert not violations, violations


def test_erp_dates_and_delay_reasons() -> None:
    violations: list[str] = []
    for o in erp()["orders"]:
        oid = o["order_id"]
        if not is_iso_date(o["promised_date"]):
            violations.append(f"{oid}: order.promised_date.iso")
            continue
        revised = o["revised_date"]
        if revised is not None and not is_iso_date(revised):
            violations.append(f"{oid}: order.revised_date.iso")
            continue
        later = revised is not None and revised > o["promised_date"]
        if later != (o["delay_reason_code"] is not None):
            violations.append(f"{oid}: order.delay_reason_iff_revised_later")
    assert not violations, violations


def test_erp_every_status_and_every_account_has_orders() -> None:
    orders = erp()["orders"]
    statuses = Counter(o["status"] for o in orders)
    per_account = Counter(o["account_id"] for o in orders)
    assert [s for s in ERP_STATUSES if statuses[s] < 3] == [], "orders: status.at_least_3"
    assert [a for a in accounts_by_id() if per_account[a] < 3] == [], "orders: account.at_least_3"


def test_erp_note_counts() -> None:
    orders = erp()["orders"]
    for field, count in (("customer_visible_note", 10), ("internal_note", 8)):
        notes = [o[field] for o in orders if o[field] is not None]
        # Counts and booleans only: a failing assertion never shows a note.
        found, distinct = len(notes), len(set(notes))
        texts = all(isinstance(n, str) and n.strip() for n in notes)
        assert found == count, f"orders: {field}.count"
        assert texts, f"orders: {field}.text"
        assert distinct == found, f"orders: {field}.unique"


def test_erp_planted_markers() -> None:
    planted = erp()["planted_markers"]
    assert len(planted) == 3, "planted_markers.count"
    assert len({m["order_id"] for m in planted}) == 3, "planted_markers.order_unique"
    orders = orders_by_id()
    violations: list[str] = []
    for m in planted:
        oid, marker = m["order_id"], m["marker"]
        note = orders[oid]["customer_visible_note"] if oid in orders else None
        if not isinstance(note, str) or marker not in note:
            violations.append(f"{oid}: marker.substring_of_visible_note")
        if marker != marker.upper() or not re.search(r"[A-Z]", marker):
            violations.append(f"{oid}: marker.upper_case")
        if sum(marker in leaf for leaf in fixture_leaves()) != 1:
            violations.append(f"{oid}: marker.unique_in_fixtures")
    notes = {
        o["order_id"]: o["customer_visible_note"]
        for o in erp()["orders"]
        if o["customer_visible_note"] is not None
    }
    violations += unlisted_injections(notes, erp_markers())
    assert not violations, violations


def unlisted_injections(texts: dict[str, str], planted: dict[str, str]) -> list[str]:
    """Ids whose text, planted marker removed, still reads like an order to the system."""
    found: list[str] = []
    for record_id, text in sorted(texts.items()):
        rest = text.replace(planted[record_id], " ") if record_id in planted else text
        if UPPER_RUN.search(rest):
            found.append(f"{record_id}: marker.unlisted_injection")
    return found


def test_erp_restricted_values_are_unique_canaries() -> None:
    violations: list[str] = []
    for o in erp()["orders"]:
        for field in ("price_unit", "unit_cost", "margin_pct", "internal_note"):
            value = o[field]
            if value is None:
                continue
            if sum(value in leaf for leaf in fixture_leaves()) != 1:
                violations.append(f"{o['order_id']}.{field}: canary.unique_substring")
    assert not violations, violations


# --------------------------------------------------------------------------- GMAO
def test_gmao_catalogues() -> None:
    g = gmao()
    failures, symptoms, actions = g["failure_codes"], g["symptom_codes"], g["action_codes"]
    assert len(failures) == 16, "failure_codes.count"
    assert len(symptoms) == 8, "symptom_codes.count"
    assert 6 <= len(actions) <= 10, "action_codes.count"
    assert g["indeterminate_code"] == "INDETERMINE", "indeterminate_code"
    violations: list[str] = []
    for section, records, pattern in (
        ("failure_codes", failures, FAILURE_CODE),
        ("symptom_codes", symptoms, SYMPTOM_CODE),
        ("action_codes", actions, ACTION_CODE),
    ):
        codes = [r["code"] for r in records]
        labels = [r["label"] for r in records]
        if len(set(codes)) != len(codes) or len(set(labels)) != len(labels):
            violations.append(f"{section}: unique")
        violations += [f"{c}: {section}.pattern" for c in codes if not pattern.fullmatch(c)]
        if "INDETERMINE" in codes:
            violations.append(f"{section}: indeterminate_listed_separately")
    for f in failures:
        if f["category"] not in CATEGORY_PREFIX:
            violations.append(f"{f['code']}: failure.category")
        elif not f["code"].startswith(CATEGORY_PREFIX[f["category"]] + "-"):
            violations.append(f"{f['code']}: failure.category_prefix")
    if {f["category"] for f in failures} != set(CATEGORY_PREFIX):
        violations.append("failure_codes: all_7_categories")
    for s in symptoms:
        keywords = s["keywords"]
        if not (
            isinstance(keywords, list)
            and keywords
            and all(isinstance(k, str) and k for k in keywords)
        ):
            violations.append(f"{s['code']}: symptom.keywords")
    assert not violations, violations


def test_failure_catalogue_names_no_person_and_no_human_error() -> None:
    names = person_names() | {
        normalize(n)
        for n in [*synthetic()["names"]["common_given_names"], *synthetic()["common_surnames"]]
    }
    violations: list[str] = []
    for f in gmao()["failure_codes"]:
        for field in ("label", "category"):
            text = normalize(f[field])
            if any(re.search(rf"\b{stem}", text) for stem in HUMAN_ERROR_STEMS):
                violations.append(f"{f['code']}.{field}: catalogue.human_error")
            if set(re.findall(r"[\w-]+", text)) & names:
                violations.append(f"{f['code']}.{field}: catalogue.person_name")
    assert not violations, violations


def test_gmao_assets() -> None:
    assets = gmao()["assets"]
    assert len(assets) == 10 and {a["asset_id"] for a in assets} == set(ASSET_IDS), "assets.ids"
    names = [a["name"] for a in assets]
    assert len(set(names)) == len(names), "assets: name.unique"
    violations: list[str] = []
    for a in assets:
        if not a["name"].endswith(FICTIVE):
            violations.append(f"{a['asset_id']}: asset.name_fictive")
        if not (isinstance(a["kind"], str) and a["kind"] and isinstance(a["line"], str)):
            violations.append(f"{a['asset_id']}: asset.kind_line")
    assert not violations, violations


def test_gmao_work_order_fields() -> None:
    work_orders = gmao()["work_orders"]
    assert len(work_orders) == 120, "work_orders.count"
    failures = failure_codes() | {gmao()["indeterminate_code"]}
    symptoms, actions = symptom_codes(), {a["code"] for a in gmao()["action_codes"]}
    violations: list[str] = []
    for wo in work_orders:
        wid = wo["wo_id"]
        rules = {
            "wo.asset": wo["asset_id"] in assets_by_id(),
            "wo.opened_on": is_iso_date(wo["opened_on"]),
            "wo.opened_time": isinstance(wo["opened_time"], str)
            and CLOCK.fullmatch(wo["opened_time"]) is not None,
            "wo.shift": wo["shift"] in SHIFTS,
            "wo.kind": wo["kind"] in WO_KINDS,
            "wo.symptom_code": wo["symptom_code"] is None or wo["symptom_code"] in symptoms,
            "wo.failure_code": wo["failure_code"] is None or wo["failure_code"] in failures,
            "wo.action_code": wo["action_code"] in actions,
            "wo.correctif_has_codes": wo["kind"] != "correctif"
            or (wo["symptom_code"] is not None and wo["failure_code"] is not None),
            "wo.downtime_min": isinstance(wo["downtime_min"], int)
            and not isinstance(wo["downtime_min"], bool)
            and wo["downtime_min"] >= 0,
            "wo.remark": wo["remark"] is None or (isinstance(wo["remark"], str) and wo["remark"]),
            "wo.safety": isinstance(wo["safety"], bool),
            "wo.safety_has_remark": not wo["safety"] or wo["remark"] is not None,
        }
        violations += [f"{wid}: {rule}" for rule, ok in rules.items() if not ok]
    correctif = sum(wo["kind"] == "correctif" for wo in work_orders)
    if not 36 <= correctif <= 60:
        violations.append("work_orders: correctif_about_40_percent")
    assert not violations, violations


def test_gmao_work_order_ids_are_sequential_per_year() -> None:
    numbers: dict[str, list[int]] = defaultdict(list)
    opened: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    violations: list[str] = []
    for wo in gmao()["work_orders"]:
        wid = str(wo["wo_id"])
        if not WO_ID.fullmatch(wid):
            violations.append(f"{wid}: wo_id.pattern")
            continue
        if not str(wo["opened_on"]).startswith(wid[3:7]):
            violations.append(f"{wid}: wo_id.year_of_opened_on")
        numbers[wid[3:7]].append(int(wid[8:]))
        opened[wid[3:7]].append((int(wid[8:]), str(wo["opened_on"]), str(wo["opened_time"])))
    for year, found in sorted(numbers.items()):
        if sorted(found) != list(range(1, len(found) + 1)):
            violations.append(f"{year}: wo_id.sequential_from_0001")
        # Numbered in opening order: all_history_lines breaks date ties by wo_id.
        moments = [(day, clock) for _, day, clock in sorted(opened[year])]
        if moments != sorted(moments):
            violations.append(f"{year}: wo_id.in_opening_order")
    assert not violations, violations


def test_gmao_four_technicians_with_consistent_identities() -> None:
    syn = synthetic()
    people = technicians()
    # Counts only: a failing assertion never shows a technician's identity.
    found = len(people)
    assert found == 4, "technicians.count"
    for index, field in enumerate(TECH_FIELDS):
        distinct = len({p[index] for p in people})
        assert distinct == 4, f"technicians: {field}.one_per_person"
    violations: list[str] = []
    for number, (name, matricule, initials, login) in enumerate(sorted(people), start=1):
        parts = str(name).split(" ")
        if len(parts) != 2 or parts[0] not in syn["names"]["common_given_names"]:
            violations.append(f"technician {number}: synthetic.given_name")
        if parts[-1] not in syn["common_surnames"]:
            violations.append(f"technician {number}: synthetic.surname")
        if not MATRICULE.fullmatch(str(matricule)):
            violations.append(f"technician {number}: synthetic.matricule")
        if not INITIALS.fullmatch(str(initials)):
            violations.append(f"technician {number}: technician.initials")
        if not LOGIN.fullmatch(str(login)):
            violations.append(f"technician {number}: technician.login")
    assert not violations, violations


def test_gmao_one_technician_works_only_the_evening_shift() -> None:
    shifts: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for wo in gmao()["work_orders"]:
        shifts[tuple(wo[f] for f in TECH_FIELDS)].add(wo["shift"])
    evening_only = [p for p, s in shifts.items() if s == {"soir"}]
    on_evening = [p for p, s in shifts.items() if "soir" in s]
    single = len(evening_only) == 1
    alone = on_evening == evening_only
    assert single, "technicians.one_evening_only"
    assert alone, "technicians.single_evening_mechanic"


def test_gmao_window_counts() -> None:
    dates = [wo["opened_on"] for wo in gmao()["work_orders"]]
    in_window = [d for d in dates if IN_WINDOW[0] <= d <= IN_WINDOW[1]]
    older = [d for d in dates if OLDER[0] <= d <= OLDER[1]]
    assert (len(in_window), len(older), len(dates)) == (114, 6, 120), "work_orders.window"
    per_asset = Counter(
        wo["asset_id"] for wo in gmao()["work_orders"] if wo["opened_on"] >= WINDOW_START
    )
    assert [a for a in ASSET_IDS if per_asset[a] < 6] == [], "assets.at_least_6_in_window"


def projected_remarks() -> dict[str, str]:
    return {
        wo["wo_id"]: wo["remark"]
        for wo in gmao()["work_orders"]
        if wo["remark"] is not None and not wo["safety"]
    }


def safety_notes() -> dict[str, str]:
    return {wo["wo_id"]: wo["remark"] for wo in gmao()["work_orders"] if wo["safety"]}


def names_a_technician_mark(text: str) -> bool:
    """True when ``text`` holds a technician's initials (exact case) or login."""
    return any(
        has_word(text, initials) or has_word(text, login, fold=True)
        for _, _, initials, login in technicians()
    )


def test_gmao_remark_counts() -> None:
    texts = [wo["remark"] for wo in gmao()["work_orders"] if wo["remark"] is not None]
    # Counts only: a failing assertion never shows a remark or a safety note.
    counts = (len(texts), len(safety_notes()), len(projected_remarks()), len(set(texts)))
    assert counts[0] == 16, "remarks.count"
    assert counts[1] == 4, "remarks.safety_count"
    assert counts[2] == 12, "remarks.projected_count"
    assert counts[3] == counts[0], "remarks.unique"
    violations = [
        f"{wid}: safety_note.unique_in_fixtures"
        for wid, note in safety_notes().items()
        if sum(note in leaf for leaf in fixture_leaves()) != 1
    ]
    assert not violations, violations


def test_gmao_planted_markers() -> None:
    planted = gmao()["planted_markers"]
    assert len(planted) == 3, "planted_markers.count"
    assert len({m["wo_id"] for m in planted}) == 3, "planted_markers.wo_unique"
    projected = projected_remarks()
    violations: list[str] = []
    for m in planted:
        wid, marker = m["wo_id"], m["marker"]
        if wid not in projected or marker not in projected[wid]:
            violations.append(f"{wid}: marker.substring_of_projected_remark")
        if marker != marker.upper() or not re.search(r"[A-Z]", marker):
            violations.append(f"{wid}: marker.upper_case")
        if sum(marker in leaf for leaf in fixture_leaves()) != 1:
            violations.append(f"{wid}: marker.unique_in_fixtures")
    violations += unlisted_injections(projected, gmao_markers())
    assert not violations, violations


@pytest.mark.parametrize(("key", "size"), [("initials_or_login", 2), ("small_role_fault", 1)])
def test_gmao_special_remark_lists_are_exact(key: str, size: int) -> None:
    ids = gmao()["special_remarks"][key]
    assert isinstance(ids, list) and all(isinstance(i, str) for i in ids), f"{key}: ids.text"
    assert len(set(ids)) == len(ids) == size, f"{key}: ids.exact_count_without_duplicates"


def test_gmao_special_remarks() -> None:
    special = gmao()["special_remarks"]
    assert set(special) == {"initials_or_login", "small_role_fault"}, "special_remarks.keys"
    projected = projected_remarks()
    marked = {wid for wid, text in projected.items() if names_a_technician_mark(text)}
    assert len(marked) == 2, "remarks.exactly_2_initials_or_login"
    assert marked == set(special["initials_or_login"]), "special_remarks.initials_or_login"
    evening = {
        wid for wid, text in projected.items() if has_word(text, "mécanicien de soir", fold=True)
    }
    assert len(evening) == 1, "remarks.exactly_1_small_role_fault"
    assert evening == set(special["small_role_fault"]), "special_remarks.small_role_fault"
    # Every part of a technician's name: the given name alone is part of the canary too.
    parts = {normalize(part) for name, *_ in technicians() for part in name.split()}
    leaked = [
        wid
        for wid, text in projected.items()
        if any(has_word(text, part, fold=True) for part in parts)
    ]
    assert not leaked, [f"{wid}: remarks.no_technician_name" for wid in leaked]


# --------------------------------------------------------------------------- ids and vocabulary
def fixture_ids() -> dict[str, str]:
    """Every identifier or code of the fixtures, mapped to the first field that holds it."""
    sections: list[tuple[str, dict[str, Any], str, tuple[str, ...]]] = [
        ("erp", erp(), "carriers", ("code",)),
        ("erp", erp(), "delay_reasons", ("code",)),
        ("erp", erp(), "accounts", ("account_id",)),
        ("erp", erp(), "contacts", ("contact_ref",)),
        (
            "erp",
            erp(),
            "orders",
            ("order_id", "part_ref", "carrier_code", "tracking_ref", "delay_reason_code"),
        ),
        ("gmao", gmao(), "failure_codes", ("code",)),
        ("gmao", gmao(), "symptom_codes", ("code",)),
        ("gmao", gmao(), "action_codes", ("code",)),
        ("gmao", gmao(), "assets", ("asset_id",)),
        ("gmao", gmao(), "work_orders", ("wo_id",)),
    ]
    found: dict[str, str] = {"INDETERMINE": "gmao.indeterminate_code"}
    for name, data, section, fields in sections:
        for index, record in enumerate(data[section]):
            for field in fields:
                if record[field] is not None:
                    found.setdefault(str(record[field]), f"{name}.{section}[{index}].{field}")
    return found


def test_every_fixture_id_is_immune_to_the_detector(privacy_ctx: PrivacyContext) -> None:
    violations = [
        f"{where}: id.detected ({hit})"
        for value, where in fixture_ids().items()
        for hit in detector_hits(value, privacy_ctx)
    ]
    assert len(fixture_ids()) > 200
    assert not violations, violations


def test_vocabulary_lists_exactly_the_fixture_values() -> None:
    e, g, v = erp(), gmao(), vocabulary()
    expected: dict[str, list[str]] = {
        "account_labels": [a["label"] for a in e["accounts"]],
        "carrier_labels": [c["label"] for c in e["carriers"]],
        "delay_labels": [d["label"] for d in e["delay_reasons"]],
        "part_refs": sorted({o["part_ref"] for o in e["orders"]}),
        "asset_names": [a["name"] for a in g["assets"]],
        "failure_labels": [f["label"] for f in g["failure_codes"]],
        "symptom_labels": [s["label"] for s in g["symptom_codes"]],
        "customer_mailboxes": [a["generic_mailbox"] for a in e["accounts"]],
        "role_mailboxes": ROLE_MAILBOXES,
    }
    violations: list[str] = []
    for key, values in expected.items():
        got = v[key]
        if not isinstance(got, list) or len(set(got)) != len(got):
            violations.append(f"vocabulary.{key}: list_without_duplicates")
        elif sorted(got) != sorted(values):
            violations.append(f"vocabulary.{key}: exact_fixture_values")
    assert not violations, violations


def test_planted_markers_appear_nowhere_else_in_repository_data() -> None:
    markers = [*erp_markers().values(), *gmao_markers().values()]
    # The fixtures are checked leaf by leaf above; the gold sets in test_ops_data.py.
    skipped = {ERP, GMAO, SVC_GOLD, MAINT_GOLD}
    violations: list[str] = []
    for path in sorted((REPO_ROOT / "data").rglob("*")):
        if path.name in HOLDOUT_NAMES or path in skipped:
            continue  # holdouts are never opened
        if not path.is_file() or path.suffix not in DATA_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations += [f"{rel}: marker.elsewhere" for m in markers if m in text]
    assert not violations, violations
