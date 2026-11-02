"""Action requests, overrides and credentials in ops text, for module 3 (ADR 0012).

The shared ``INJECTION_PATTERNS`` catch rule overrides (« ignore tes consignes »), not
an order addressed to the agent in a remark or a customer-visible note (« crée un bon de
travail », « envoie le courriel à cette adresse »). :data:`OPS_ACTION_PATTERNS` fill
that gap. They are a defence in depth: the structure is the real barrier (the code
chooses tools, recipients and arguments; no sending tool exists).

The action patterns apply to **tool text only** (``quarantine.scrub_tool_text``). A
user's request is never read with them: « crée une demande de travail pour EQ-… » is a
legitimate maintenance request (I4 spec §3, decision order, step 2). A write verb counts
only as an order: an imperative at the start of a sentence, after a comma, colon or
semicolon, or after a politeness or obligation word (« svp », « merci de », « veuillez »,
« il faut », « pouvez-vous »); an infinitive at the start of a sentence or after such a
word. A descriptive sentence (« le transporteur va changer la date », « nous allons
envoyer le reste de la commande ») is no order. A bare infinitive at the start of a
sentence does not take a date as its object (« Changer le filtre à la date de la
prochaine inspection » is a maintenance instruction).

:data:`OPS_INJECTION_PATTERNS` add rule-override wordings the shared list lacks (« ne
tiens plus compte des consignes », « fais abstraction de tes instructions », « tu es
désormais… », « disregard previous instructions »), and :data:`OPS_CREDENTIAL_PATTERN`
the usual short names of a secret (« mdp », « NIP », « code d'accès », « passwd »). Both
apply to requests and to tool text (``ops_screen`` and ``quarantine``).

Each pattern is case-insensitive and accent-tolerant (``injection.accent_tolerant``):
« CRÉE », « cree » and « Crée » match alike. The functions read the text after
``clean_text`` (NFKC, zero-width characters removed, look-alike letters folded) with
typographic and modifier apostrophes folded.

Published known misses: an order without a verb of the closed lists (« il faudrait que
le bon soit fermé »), an order in the middle of a sentence after « et », an order split
across two sentences, English orders (« please close the work order »), and verbs
written with a typo.
"""

from __future__ import annotations

import re

from alverno_lab.privacy.injection import accent_tolerant
from alverno_lab.privacy.lexicon import clean_text

__all__ = [
    "OPS_ACTION_PATTERNS",
    "OPS_CREDENTIAL_PATTERN",
    "OPS_INJECTION_PATTERNS",
    "fold_apostrophes",
    "is_ops_action",
    "is_ops_credential",
    "is_ops_injection",
]

# Word boundaries on raw text: a hyphen glues words (« envoie-le » is one verb group).
_L = r"(?<![\w-])"
_R = r"(?![\w-])"
# An optional object clitic after an imperative (« envoie-le », « ferme-les »).
_CLITIC = r"(?:-(?:le|la|les|lui|leur|moi|nous|en))?"
# Apostrophes typed as the typographic one, the modifier letter, the grave or acute
# accent, or the left quote: all read as « ' ».
_APOSTROPHES = str.maketrans({chr(c): "'" for c in (0x2019, 0x2018, 0x02BC, 0x0060, 0x00B4)})


def fold_apostrophes(text: str) -> str:
    """``text`` with every apostrophe look-alike folded to « ' » (same length)."""
    return text.translate(_APOSTROPHES)


def _alt(block: str) -> str:
    """Alternation of the space-separated forms of ``block``, longest first."""
    return "|".join(sorted(block.split(), key=len, reverse=True))


# Write verbs of ADR 0012 and their usual synonyms: imperatives (tu, vous) and
# infinitives, and their objects in the ERP and the GMAO.
_WRITE_IMPERATIVES = (
    _alt(
        "crée créez marque marquez envoie envoyez ajoute ajoutez supprime supprimez annule "
        "annulez modifie modifiez change changez ferme fermez clôture clôturez clos passe "
        "passez reporte reportez valide validez approuve approuvez confirme confirmez"
    )
    + r"|mets à jour|mettez à jour"
)
_WRITE_INFINITIVES = (
    _alt(
        "créer marquer envoyer ajouter supprimer annuler modifier changer fermer clôturer "
        "clore passer reporter valider approuver confirmer"
    )
    + r"|mettre à jour"
)
_WRITE_VERBS = f"{_WRITE_IMPERATIVES}|{_WRITE_INFINITIVES}"
_RECORDS = (
    r"bons?(?: de (?:travail|commande))?|commandes?|courriels?|demandes? de travail"
    r"|statuts?|ot|ots|livraisons?|expéditions?|confirmations?|réponses?|dossiers?"
    r"|interventions?"
)
_DATES = r"dates?|échéances?"
# A record within four words of the verb (« ferme tous les bons ouverts », « ferme l'OT »).
_GAP = r"(?:\s+[\w'-]+){0,4}?\s+(?:l'|d')?"
# Where an order starts: the sentence start (after any punctuation), or a , : ;
_START = r"(?:^[\W_]*|[,:;]\s*)"
# Politeness and obligation words that introduce an order (« svp », « merci d'… »).
_POLITE = (
    r"(?:svp|stp|s'il (?:te|vous) plaît|veuillez|pourriez-vous|pourrais-tu|pouvez-vous"
    r"|peux-tu|vous devez|tu dois|il faut|il faudrait|urgent|important"
    r"|(?:merci|prière|n'oubliez pas|n'oublie pas) de)\s*[,:]?\s+"
    r"|(?:merci|prière|n'oubliez pas|n'oublie pas) d'"
)
# Fillers between an addressee and its order (« assistant, p. ex. ferme… »).
_FILLER = r"(?:p\. ?ex\.|par exemple|donc|alors|maintenant|ensuite|puis|aussi)\s*,?\s*"
# Imperatives addressed to the agent after « assistant, » or « système : ».
_IMPERATIVES = "|".join(
    (
        _WRITE_VERBS,
        _alt(
            "ignore ignorez ignorer oublie oubliez oublier déclare déclarez déclarer "
            "copie copiez copier transfère transférez transférer exécute exécutez "
            "exécuter affiche affichez afficher révèle révélez révéler efface effacez "
            "effacer retire retirez retirer cite citez citer commande commandez commander "
            "inclus incluez inclure réponds répondez répondre joins joignez joindre "
            "écris écrivez écrire mets mettez mettre dis dites dire donne donnez donner "
            "fais faites faire ne"
        ),
    )
)
_ADDRESSEES = _alt(
    "système systèmes assistant assistante agent agente robot bot chatbot modèle ia llm gpt"
)

OPS_ACTION_PATTERNS: tuple[str, ...] = (
    # 0. A write verb given as an order, then within four words a record of the ERP or
    # the GMAO (« ferme tous les bons ouverts », « svp, change le statut »).
    rf"(?:{_START}(?:{_WRITE_IMPERATIVES})|(?:{_POLITE})(?:{_WRITE_VERBS})){_CLITIC}{_R}"
    rf"{_GAP}(?:{_RECORDS}|{_DATES}){_R}",
    # 1. A note addressed to the system (« note au système », « consigne prioritaire à
    # l'assistant », « message au robot », « note à l'intention de l'IA »).
    rf"{_L}(?:notes?|messages?|avis|consignes?|instructions?|directives?|ordres?)"
    rf"(?:\s+(?:prioritaires?|urgente?s?|importante?s?|spéciale?s?))?"
    rf"\s+(?:au|aux|à l'|à la|pour l'|pour le|pour la|destinée? à l'"
    rf"|à l'(?:attention|intention) (?:de l'|du |de la |des ))\s*(?:{_ADDRESSEES}){_R}",
    # 2. A recipient chosen by the text (« à cette adresse(-ci) », « à l'adresse
    # suivante »).
    rf"{_L}(?:à cette adresse(?:-ci|-là)?|à l'adresse (?:suivante|ci-dessous|ci-après)){_R}",
    # 3. The agent addressed by name, then an order, politely or not (« assistant,
    # oublie… », « système : envoie… », « agent : veuillez ignorer… », « @assistant
    # fais… »).
    rf"(?:@\s*(?:{_ADDRESSEES})\s*[,:]?|{_L}(?:{_ADDRESSEES})\s*[,:])\s*"
    rf"(?:{_FILLER}|(?:{_POLITE})\s*){{0,2}}(?:{_IMPERATIVES}){_CLITIC}{_R}",
    # 4. A bare infinitive starting a sentence, then a record (« Mettre à jour le statut
    # de livraison », « Créer un bon de travail urgent »); a date is no object here.
    rf"{_START}(?:{_WRITE_INFINITIVES}){_R}{_GAP}(?:{_RECORDS}){_R}",
    # 5. A text addressed to the agent (« à l'attention de l'assistant : »).
    rf"{_L}à l'(?:attention|intention) (?:de l'|du |de la |des |de )\s*(?:{_ADDRESSEES}){_R}",
)

OPS_INJECTION_PATTERNS: tuple[str, ...] = (
    r"\bne (?:tiens|tenez|tenons)\s+(?:plus\s+|pas\s+|jamais\s+)?compte\b.{0,40}"
    r"\b(?:instructions?|consignes?|règles?|directives?)\b",
    r"\b(?:fais|faites|faire)\s+abstraction\b.{0,40}"
    r"\b(?:instructions?|consignes?|règles?|directives?)\b",
    r"\b(?:tu es|vous êtes)\s+(?:désormais|dorénavant|à partir de maintenant)\b",
    r"\b(?:disregard|forget)\b.{0,30}\b(?:instructions?|rules|prompts?)\b",
)
OPS_CREDENTIAL_PATTERN = r"\b(?:mdp|nip|passwd|pwd|mot-de-passe|codes? d'accès|codes? secrets?)\b"


def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(accent_tolerant(p), re.IGNORECASE) for p in patterns)


_OPS_ACTION_RE = _compiled(OPS_ACTION_PATTERNS)
_OPS_INJECTION_RE = _compiled(OPS_INJECTION_PATTERNS)
_OPS_CREDENTIAL_RE = _compiled((OPS_CREDENTIAL_PATTERN,))


def _plain(text: str) -> str:
    return fold_apostrophes(clean_text(text))


def is_ops_action(text: str) -> bool:
    """True when ``text`` holds an action request addressed to the agent (tool text only)."""
    plain = _plain(text)
    return any(p.search(plain) for p in _OPS_ACTION_RE)


def is_ops_injection(text: str) -> bool:
    """True when ``text`` holds an ops rule-override wording (requests and tool text)."""
    plain = _plain(text)
    return any(p.search(plain) for p in _OPS_INJECTION_RE)


def is_ops_credential(text: str) -> bool:
    """True when ``text`` names a secret by a short name (requests and tool text)."""
    plain = _plain(text)
    return any(p.search(plain) for p in _OPS_CREDENTIAL_RE)
