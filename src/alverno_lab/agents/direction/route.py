"""Screen and routing of the management orchestrator (ADR 0018, I2 precisions).

The code decides: a closed lexicon screens the question, then picks the purposes and the
scope. The model never chooses a domain, a tool or an argument.

Order of the screen (the first rule that fires gives the refusal code):

1. ``sensitive_out_of_scope``: health, accident, disciplinary measure (level 5);
2. ``restricted_info``: prices, costs, margins, quotes (level 3);
3. ``profiling``: a repair duration or delay (any), or a ranking or performance word
   together with a person, a role or a group;
4. ``person_join``: a person, a role or a shift together with an orders or maintenance
   word (crossing a domain about a person);
5. routing: ``out_of_scope`` when no purpose is recognised.

A question that names an order or equipment id, or an orders or maintenance word, is an
overview (D3, D4 or both); the Quality and HR policy purposes (D1, D2) are read only when
it is not, so « la cabine de peinture EQ-PEI-01 » is not a painting procedure question.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata
from dataclasses import dataclass

from alverno_lab.privacy.direction_access_log import (
    DIRECTION_PURPOSES,
    DirectionPurpose,
    DirectionRefusalCode,
)

__all__ = [
    "PURPOSES",
    "DirectionRefusal",
    "Period",
    "Purpose",
    "RoutePlan",
    "Scope",
    "fold",
    "parse_period",
    "route",
    "screen",
]

Purpose = DirectionPurpose
PURPOSES: tuple[Purpose, ...] = DIRECTION_PURPOSES
DirectionRefusal = DirectionRefusalCode


def fold(text: str) -> str:
    """Lower case, accents removed, apostrophes unified, spaces collapsed."""
    text = unicodedata.normalize("NFKD", text.replace(chr(0x2019), "'"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(text.split())


def _words(*terms: str) -> re.Pattern[str]:
    """A whole-word pattern of folded terms (a trailing ``*`` matches any ending)."""
    parts = [re.escape(fold(t)).replace(r"\*", r"\w*") for t in terms]
    return re.compile(r"(?<![\w-])(?:" + "|".join(parts) + r")(?![\w-])")


SENSITIVE = _words(
    "accident*",
    "bless*",
    "sante",
    "maladie*",
    "malade*",
    "medical*",
    "medicaux",
    "invalidite",
    "disciplinaire*",
    "sanction*",
    "congediement*",
    "suspension*",
)
RESTRICTED = _words(
    "prix",
    "cout",
    "couts",
    "marge*",
    "rentabilite",
    "rentable*",
    "profit*",
    "soumission*",
    "tarif*",
    "facture*",
    "facturation",
    "montant*",
    "chiffre d'affaires",
    "rabais",
    "escompte*",
)
DURATION = _words(
    "delai* moyen*",
    "temps moyen*",
    "mttr",
    "mtbf",
    "temps de reparation*",
    "duree* de reparation*",
    "duree* des reparations",
    "temps d'arret*",
    "temps d'intervention*",
    "duree* des arrets",
    "duree* d'arret*",
)
RANKING = _words(
    "class*",
    "palmares",
    "rendement*",
    "productivite",
    "productif*",
    "productive*",
    "performance*",
    "performant*",
    "le plus lent*",
    "la plus lente*",
    "le moins",
    "la moins",
    "le plus efficace*",
    "meilleur* employe*",
    "pire*",
)
PERSON = _words(
    "technicien*",
    "mecanicien*",
    "operateur*",
    "operatrice*",
    "employe*",
    "salarie*",
    "soudeur",
    "soudeurs",
    "estimateur*",
    "estimatrice*",
    "charge* de projet*",
    "superviseur*",
    "contremaitre*",
    "chef* d'equipe",
    "quart*",
    "equipe de jour",
    "equipe de soir",
    "equipe de nuit",
    "service d'entretien",
    "equipe d'entretien",
    "personne*",
    "qui",
)
ORDERS = _words(
    "commande*",
    "carnet de commandes",
    "livraison*",
    "expedition*",
    "expedie*",
    "en retard",
    "retard*",
)
MAINTENANCE = _words(
    "panne*",
    "arret*",
    "bon* de travail",
    "bon* correctif*",
    "correctif*",
    "machine*",
    "equipement*",
    "entretien",
    "maintenance",
    "defaillance*",
    "gmao",
    "plieuse*",
    "soudeuse*",
    "usineuse*",
)
# HR phrases that contain a maintenance word: removed before the maintenance match.
HR_PHRASES = _words("arret de travail", "arrets de travail")
QUALITY = _words(
    "qualite",
    "procedure*",
    "instruction*",
    "inspection*",
    "etalonnage*",
    "non-conformite*",
    "non conformite*",
    "mrb",
    "soudage",
    "soudure*",
    "peinture",
    "inox",
    "certificat*",
    "tolerance*",
    "norme*",
    "iso",
    "taux de rejet",
    "premier article",
    "document* qualite",
    "contamination",
    "passivation",
    "specification*",
    "electrode*",
    "usinage",
    "non conforme*",
    "souder",
    "rugosite",
    "prechauffage",
)
HR = _words(
    "vacances",
    "conge*",
    "ferie*",
    "teletravail",
    "horaire*",
    "heures supplementaires",
    "taux et demi",
    "assurance collective",
    "avantages sociaux",
    "prime*",
    "paie",
    "salaire*",
    "politique rh",
    "ressources humaines",
    "rh",
    "depot direct",
    "absence*",
    "formulaire rh",
    "harcelement",
    "plainte*",
    "banque d'heures",
)

ACCOUNT = re.compile(r"(?<![\w-])CLI-\d{3}(?![\w-])", re.IGNORECASE)
ASSET = re.compile(r"(?<![\w-])EQ-[A-Z]{3}-\d{2}(?![\w-])", re.IGNORECASE)
ORDER = re.compile(r"(?<![\w-])CMD-\d{4}-\d{4}(?![\w-])", re.IGNORECASE)

MONTHS = (
    "janvier",
    "fevrier",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
)
_MONTH = re.compile(r"(?<!\w)(" + "|".join(MONTHS) + r")\s+(?:de\s+)?(20\d{2})(?!\d)")
_QUARTER_T = re.compile(r"(?<!\w)t([1-4])\s+(20\d{2})(?!\d)")
_QUARTER_WORD = re.compile(
    r"(?<!\w)(premier|1er|deuxieme|2e|troisieme|3e|quatrieme|4e)\s+trimestre\s+"
    r"(?:de\s+|d'\s*)?(20\d{2})(?!\d)"
)
_QUARTER_NUMBER = {
    "premier": 1,
    "1er": 1,
    "deuxieme": 2,
    "2e": 2,
    "troisieme": 3,
    "3e": 3,
    "quatrieme": 4,
    "4e": 4,
}
_YEAR = re.compile(r"(?<![\w-])(20\d{2})(?![\w-])")


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date


@dataclass(frozen=True)
class Scope:
    account_id: str | None = None
    asset_id: str | None = None
    period: Period | None = None


@dataclass(frozen=True)
class RoutePlan:
    """The routing result: purposes in the fixed section order, and the scope."""

    purposes: tuple[Purpose, ...]
    scope: Scope


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def parse_period(folded: str) -> Period | None:
    """A month, then a quarter, then a year; ``None`` when the question names none."""
    if match := _MONTH.search(folded):
        month, year = MONTHS.index(match.group(1)) + 1, int(match.group(2))
        return Period(dt.date(year, month, 1), _month_end(year, month))
    quarter = _QUARTER_T.search(folded)
    if quarter is not None:
        number, year = int(quarter.group(1)), int(quarter.group(2))
    elif word := _QUARTER_WORD.search(folded):
        number, year = _QUARTER_NUMBER[word.group(1)], int(word.group(2))
    else:
        number = 0
    if number:
        first = 3 * (number - 1) + 1
        return Period(dt.date(year, first, 1), _month_end(year, first + 2))
    if match := _YEAR.search(folded):
        year = int(match.group(1))
        return Period(dt.date(year, 1, 1), dt.date(year, 12, 31))
    return None


def screen(question: str) -> DirectionRefusal | None:
    """The refusal code of the direction screen, or ``None`` when the question passes."""
    folded = fold(question)
    if SENSITIVE.search(folded):
        return "sensitive_out_of_scope"
    if RESTRICTED.search(folded):
        return "restricted_info"
    person = PERSON.search(folded) is not None
    if DURATION.search(folded) or (person and RANKING.search(folded)):
        return "profiling"
    operational = _orders(question, folded) or _maintenance(question, folded)
    if person and operational:
        return "person_join"
    return None


def _orders(question: str, folded: str) -> bool:
    return bool(ACCOUNT.search(question) or ORDER.search(question) or ORDERS.search(folded))


def _maintenance(question: str, folded: str) -> bool:
    return bool(ASSET.search(question) or MAINTENANCE.search(HR_PHRASES.sub(" ", folded)))


def route(question: str) -> RoutePlan | None:
    """Purposes and scope of a screened question; ``None`` means ``out_of_scope``."""
    folded = fold(question)
    orders, maintenance = _orders(question, folded), _maintenance(question, folded)
    purposes: list[Purpose] = []
    if orders or maintenance:
        purposes += ["orders_overview"] if orders else []
        purposes += ["maintenance_overview"] if maintenance else []
    else:
        purposes += ["quality_policy"] if QUALITY.search(folded) else []
        purposes += ["hr_policy"] if HR.search(folded) else []
    if not purposes:
        return None
    account = ACCOUNT.search(question) if orders else None
    asset = ASSET.search(question) if maintenance else None
    scope = Scope(
        account_id=account.group(0).upper() if account else None,
        asset_id=asset.group(0).upper() if asset else None,
        period=parse_period(folded) if maintenance else None,
    )
    return RoutePlan(tuple(p for p in PURPOSES if p in purposes), scope)
