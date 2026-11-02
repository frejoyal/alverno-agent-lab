"""Deterministic routing of a 3b request (ADR 0012, « Routage déterministe »).

:func:`route_maintenance` reads the redacted request and the closed symptom labels of
the simulated GMAO; it calls no model.

- Asset ids are the ADR 0012 prefixed ids ``EQ-XXX-NN``, read as whole tokens after
  ``fold_separators``. No asset id: refusal ``asset_not_identified``. Two distinct asset
  ids or more: ``asset_not_identified`` too. One request covers one machine, and ADR
  0012 has no multi-asset code (``multi_order`` names orders): a request about two
  machines is refused as not identifying *the* machine, and the operator asks twice.
- Flow ``work_request`` when the request asks to create, open or prepare a work request:
  a verb of the closed creation family (créer, ouvrir, préparer, rédiger, émettre,
  lancer, soumettre, générer, faire, remplir, saisir, enregistrer, inscrire, demander:
  infinitive, tu and vous imperatives, with a hyphenated pronoun; « demande » alone is
  read as the noun) whose **object** is a noun of the closed work-request family
  (« demande de travail », « demande d'intervention », « bon de travail », « ordre de
  travail », « demande de réparation », « demande corrective », and « bon » used as a
  noun: followed by « pour », « correctif » or the end of the clause). The object is the
  verb, an optional pronoun (« -moi », « moi », « nous »…), a determiner (un, une, le,
  la, l', les, des, ce, cette, ces, mon, ma, notre, votre), an optional « nouveau »,
  « nouvelle », « autre » or « petit(e) », then the noun. Except after « faire », a
  short complement may sit between the verb and its object (« pour », « sur » or
  « concernant », an optional article and at most two words: « Ouvre pour la plieuse une
  demande… »). Otherwise ``diagnosis``: a request that names the work orders as the
  complement of another noun (« Fais le tour des bons de travail… », « Lance l'analyse
  des bons de travail… », « Prépare un diagnostic à partir des bons de travail… »)
  asks for a diagnosis.
- Symptom: :func:`symptom_of` maps the request to one of the 8 closed symptom codes with
  word families built from the fixture labels (their distinctive words) and closed
  synonym lists of general French words (fuit, bruit, vibre, s'arrête, chauffe, hors
  cote, alarme, pression, bavure...). Words that also name something else stay out of
  the families alone: « cote » (« côté » once accents are folded) counts only in a
  dimension phrase (« hors cote », « cote de la pièce », « cote non respectée »);
  « coupe », « soudure » and « cordon » (a process, a machine, a power cord) only in a
  quality phrase (« qualité de coupe », « coupe bavée », « soudure poreuse », « cordon
  de soudure »); « qualité » (« service qualité ») never alone. The code with the most
  distinct mentions wins
  (a word two families match counts once); a tie goes to the code mentioned first in the
  request. None found: ``None`` (the history is then read without a symptom filter).

Case, accents and typographic apostrophes are ignored. A deterministic route errs on
paraphrases; those errors are measured (``route_accuracy``, ``false_block_rate``), not
fixed by a model-chosen plan.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal, get_args

from alverno_lab.connectors.types import SymptomCode
from alverno_lab.privacy.lexicon import FUNCTION_WORDS, clean_text, fold_separators, normalize

__all__ = [
    "ASSET_ID_RE",
    "SYMPTOM_SYNONYMS",
    "MaintFlow",
    "MaintRouteRefusal",
    "MaintRouteResult",
    "find_asset_ids",
    "has_work_request_words",
    "route_maintenance",
    "symptom_of",
]

MaintFlow = Literal["diagnosis", "work_request"]
MaintRouteRefusal = Literal["asset_not_identified"]

# [0-9], never \d (which matches non-ASCII digits); a whole token, not part of a longer id.
ASSET_ID_RE = re.compile(r"(?<![\w-])EQ-[A-Z]{3}-[0-9]{2}(?![\w-])")

# Creation verbs, normalised (no accents): infinitive, tu and vous imperatives.
_VERBS = (
    "cree|creer|creez|ouvre|ouvrir|ouvrez|prepare|preparer|preparez|redige|rediger|redigez"
    "|emets|emettre|emettez|lance|lancer|lancez|soumets|soumettre|soumettez"
    # « faire » is matched apart (_FAIRE): no complement before its object.
    "|genere|generer|generez|remplis|remplir|remplissez"
    "|saisis|saisir|saisissez|enregistre|enregistrer|enregistrez|inscris|inscrire|inscrivez"
    # « demande » alone is the noun as often as the verb: only « demander », « demandez ».
    "|demander|demandez"
)
_CLITICS = r"(?:-(?:moi|toi|lui|leur|nous|vous|la|le|les|en|y))*"
# A pronoun written apart (« prépare moi », « ouvre nous »).
_PRONOUN = r"(?: (?:moi|nous|lui|leur))?"
# « Ouvre pour la plieuse EQ-PLI-01 une demande… »: a short complement before the object.
_COMPLEMENT = r"(?: (?:pour|sur|concernant)(?: (?:la|le|les|l'))? ?[\w'-]+(?: [\w'-]+)?)?"
_DETERMINER = r"(?:(?:un|une|le|la|les|des|ce|cette|ces|mon|ma|notre|votre) |l')"
_ADJECTIVE = r"(?:(?:nouveau|nouvelle|nouvel|autre|petite?) )?"
# A work-request noun (singular or plural), right after its determiner.
_NOUN = (
    r"(?:demandes? (?:de travail|d'intervention|de reparation|corrective?s?)"
    r"|bons? de travail|ordres? de travail"
    # « bon » as a noun (a work slip): followed by « pour », « correctif » or the end of
    # the clause, never « un bon diagnostic ».
    r"|bon(?= pour | correctif|\s*(?:[.,;:!?]|$)))(?![\w-])"
)
_OBJECT = rf"{_PRONOUN} {_DETERMINER}{_ADJECTIVE}{_NOUN}"
_FAIRE = "fais|faire|faites"
_WORK_REQUEST_RE = re.compile(
    rf"(?<![\w-])(?:(?:{_VERBS}){_CLITICS}{_COMPLEMENT}|(?:{_FAIRE}){_CLITICS}){_OBJECT}"
)
_SPACES_RE = re.compile(r"[ \t]+")
_WORD_RE = re.compile(r"[a-z]+")

# Closed synonym families per symptom code, on normalised text (whole words; a family
# is a regular expression of general French words, never a fixture value).
SYMPTOM_SYNONYMS: Mapping[SymptomCode, tuple[str, ...]] = MappingProxyType(
    {
        "SYM-FUITE": (
            r"fuit|fuite|fuites|fuient|fuyait|fuyaient",
            r"suinte|suintent|suintement",
            r"coule|coulent|goutte|gouttent|degouline",
        ),
        "SYM-BRUIT": (
            r"bruit|bruits|bruyante?s?",
            r"vibre|vibrent|vibration|vibrations",
            r"claque|claquent|claquement|cogne|cognent|grince|grincent|siffle|sifflent",
        ),
        "SYM-ARRET": (
            r"arret|arrets|arrete|arretent|arretee|arretees|arreter",
            r"stoppe|stoppee|ne demarre (?:plus|pas)|ne redemarre (?:plus|pas)",
        ),
        "SYM-CHAUFF": (
            r"chauffe|chauffent|surchauffe|surchauffent|surchauffee",
            r"trop chaude?s?|brulante?s?|temperature",
        ),
        "SYM-PRECIS": (
            # « cote » alone is also « côté » once accents are folded: dimension phrases only.
            r"hors cotes?|(?:mauvaises?|fausses?) cotes?"
            r"|cotes? (?:(?:est|sont|etait|etaient) )?(?:pas )?(?:non respectee?s?|fausses?"
            r"|incorrecte?s?|mauvaises?|decalee?s?)"
            r"|cotes? (?:de|d'|des) ?(?:la |l')?(?:piece|pieces|percage|percages|alesage"
            r"|diametre|diametres|longueur|longueurs|largeur|hauteur|profondeur|trou|trous"
            r"|pli|plis|pliage|angle|angles)",
            r"precision|imprecise?s?|imprecision",
            r"dimension|dimensions|dimensionnelle?s?",
            r"tolerance|tolerances|decalage|decalee?s?",
        ),
        "SYM-ALARME": (
            r"alarme|alarmes",
            r"code (?:d'erreur|de defaut|erreur)|codes (?:d'erreur|de defaut)",
            r"message d'erreur|messages d'erreur|voyant|voyants",
        ),
        "SYM-PRESS": (r"pression|pressions",),
        "SYM-QUALI": (
            # « coupe », « soudure » and « cordon » alone name a process, a machine or a
            # power cord: quality phrases only.
            r"(?:qualite|aspect|finition)s? (?:de|des|du) ?(?:la |l')?(?:coupes?|soudures?)",
            # « la coupe est bavée », « les soudures sont poreuses »: an optional copula.
            r"(?:coupes?|soudures?) (?:(?:est|sont|etait|etaient|reste|restent) )?"
            r"(?:bavee?s?|baveuses?|irreguliere?s?|poreuses?"
            r"|defectueuses?|ratee?s?|sales?|incomplete?s?|mal faite?s?"
            r"|de mauvaise qualite|pas nette?s?|pas propres?)",
            r"(?:mauvaises?|sales?) (?:coupes?|soudures?)",
            r"cordons? (?:de soudure|irreguliers?|poreux|defectueux)",
            r"bavure|bavures|porosite|porosites|poreuse?s?",
        ),
    }
)
# Label words too generic to name one symptom (« défaut » is in two labels), or that
# name something else alone (« service qualité », « en pleine coupe », « robot de
# soudure »): their synonym families above read them in a phrase.
_GENERIC_LABEL_WORDS: frozenset[str] = frozenset(
    {"defaut", "code", "perte", "anormal", "qualite", "coupe", "soudure"}
)
_MIN_LABEL_WORD = 4


@dataclasses.dataclass(frozen=True)
class MaintRouteResult:
    """A flow, its asset id and the symptom read (or None), or a routing refusal."""

    flow: MaintFlow | None
    asset_id: str | None
    symptom: SymptomCode | None
    refusal: MaintRouteRefusal | None

    def __post_init__(self) -> None:
        routed = self.flow is not None and self.asset_id is not None and self.refusal is None
        refused = (
            self.refusal is not None
            and self.flow is None
            and self.asset_id is None
            and self.symptom is None
        )
        if not (routed or refused):
            raise ValueError("route:inconsistent")


def _flat(text: str) -> str:
    return _SPACES_RE.sub(" ", normalize(fold_separators(clean_text(text))))


def find_asset_ids(text: str) -> list[str]:
    """The distinct prefixed asset ids of ``text``, in order of appearance."""
    folded = fold_separators(clean_text(text))
    return list(dict.fromkeys(ASSET_ID_RE.findall(folded)))


def has_work_request_words(text: str) -> bool:
    """Whether ``text`` asks to create a work request (a creation verb whose object is a
    work-request noun; see the module docstring)."""
    return _WORK_REQUEST_RE.search(_flat(text)) is not None


def _label_family(label: str) -> str | None:
    """The distinctive words of a fixture label, as one alternation (or None)."""
    kept = [
        word
        for word in _WORD_RE.findall(normalize(label))
        if len(word) >= _MIN_LABEL_WORD
        and word not in FUNCTION_WORDS
        and word not in _GENERIC_LABEL_WORDS
    ]
    return "|".join(re.escape(word) for word in dict.fromkeys(kept)) or None


def _families(labels: Mapping[SymptomCode, str]) -> dict[SymptomCode, list[re.Pattern[str]]]:
    families: dict[SymptomCode, list[re.Pattern[str]]] = {}
    for code in get_args(SymptomCode):
        sources = list(SYMPTOM_SYNONYMS.get(code, ()))
        label = labels.get(code)
        family = _label_family(label) if isinstance(label, str) else None
        if family is not None:
            sources.append(family)
        families[code] = [re.compile(rf"(?<![\w-])(?:{src})(?![\w-])") for src in sources]
    return families


def symptom_of(request: str, labels: Mapping[SymptomCode, str]) -> SymptomCode | None:
    """The symptom code ``request`` describes, or None (see the module docstring)."""
    flat = _flat(request)
    best: tuple[int, int] | None = None
    found: SymptomCode | None = None
    for code, patterns in _families(labels).items():
        # A word matched by two families (a label word that is also a synonym) counts once.
        starts = {m.start() for p in patterns if (m := p.search(flat)) is not None}
        if not starts:
            continue
        # More distinct mentions first, then the earliest mention.
        key = (-len(starts), min(starts))
        if best is None or key < best:
            best, found = key, code
    return found


def route_maintenance(request_redacted: str, labels: Mapping[SymptomCode, str]) -> MaintRouteResult:
    """Route a redacted 3b request; pure, no model, no I/O."""
    ids = find_asset_ids(request_redacted)
    if len(ids) != 1:
        return MaintRouteResult(
            flow=None, asset_id=None, symptom=None, refusal="asset_not_identified"
        )
    flow: MaintFlow = "work_request" if has_work_request_words(request_redacted) else "diagnosis"
    symptom = symptom_of(request_redacted, labels)
    return MaintRouteResult(flow=flow, asset_id=ids[0], symptom=symptom, refusal=None)
