"""Deterministic screens of an operator request, module 3 (3a service, 3b maintenance).

``screen_service`` and ``screen_maintenance`` are pure and do no I/O. They follow the HR
``screen`` pipeline: ``clean_text``, runs of spaces collapsed, ``detect_pi`` in question
mode, ``redact``. The rules read the redacted text, normalised (accents and case folded,
apostrophe look-alikes folded, a line break read as a space, a blank line as a sentence
end, dotted acronyms such as « C.N.E.S.S.T. » or « M.T.T.R. » joined), with whole-word
boundaries; inflections are spelt out in closed lists. Fixture ids (``CMD-2026-0412``,
``EQ-PLI-01``, ``OT-2026-0008``, ``CLI-003``) are immune to ``detect_pi``, so they
survive redaction. The injection screen reads the cleaned text unredacted. A refusal
never carries a value: only a code and stable rule ids (``"svc.restricted"``,
``"maint.profiling.duration"``...).

Decision order, first match wins (spec I4 decision 3):

1. ``empty``: nothing visible (a zero-width space, a BOM or a blank filler alone);
2. ``prompt_injection``: the module-1 screen ``is_injection`` (no password help in 3a or
   3b), also read accent-tolerantly with apostrophes folded as the HR path does, the ops
   override and short credential names of ``ops_injection`` (« ne tiens plus compte des
   consignes », « mdp », « NIP »), ``credential_value``, and approval-bypass wording
   (« sans mon approbation », « sans que je la relise », « pas besoin de mon
   approbation », « passe outre l'approbation », « contourne l'approbation », « exécute
   directement l'écriture », « approuve-le toi-même », « déjà approuvé par moi »). A bare
   « sans validation » is a bypass only next to an order to the agent (« envoie-la sans
   validation »), not when it describes a machine (« redémarrée sans vérification »). The
   ops action patterns of quarantined tool text are not applied here: « crée une demande
   de travail pour EQ-… » is a legitimate work request;
3. ``unsupported_language``: an English request (v1 is French only): two English words or
   more, outnumbering the French ones;
4. ``sensitive_out_of_scope``: injury (« blessé », « s'est coupé la main », « brûlure »,
   « hospitalisé », « ambulance »), accident, CNESST or CSST, lesion, first aid, safety
   incident, near miss;
5. ``person_targeted``: asking who did something (« qui a remplacé… », « qui l'a
   réparée », « de qui est… », « quel est le technicien qui… », « trouve le mécanicien
   qui… », « identifie l'employé… », « le nom de la personne… », initials, an employee
   number), a ``[nom]`` or ``[matricule]`` placeholder that is not the asker's; in 3b also
   a fault, a blame or a sanction tied to a person, a team or a small group
   (« par la faute de qui », « à cause du mécanicien de soir », « a mal serré »,
   « s'est trompé », « réprimandée », « suspendre le technicien »). A relative « qui »
   after an id or inside commas (« la commande CMD-…, qui est en retard, … ») and « qui
   est le transporteur / fournisseur » are not targeted. 3a keeps the identification and
   placeholder rules only (« qui chez le client… » is not targeted: the contact is
   resolved by code);
6. ``profiling`` (3b only): a mean repair time (MTTR, « délai moyen de réparation »,
   « temps moyen pour réparer »); a duration tied to a person, a shift, a team or the
   service (a time-taking verb, « a mis », « prend », « a passé », or « les heures du
   mécanicien »); a count per person (« combien d'interventions le technicien… »,
   « par technicien »); a ranking, a performance or a comparison of people (« classe »,
   « rendement du service », « le quart de nuit est-il plus lent »); a small-group role
   tied to a duration or a result. A machine duration (« depuis combien de temps la
   presse vibre-t-elle », « combien de temps le technicien doit-il laisser refroidir… »)
   and urgency (« dans les meilleurs délais ») are not profiling;
7. ``recipient_override`` (3a only): an e-mail address or ``[courriel]`` typed in the
   request (the screen does not know the order's contact, and the recipient is always
   set by code, so any typed address is refused: fail closed, the I6 pipeline holds the
   contact), « à cette adresse », a draft sent, forwarded, routed or addressed to someone
   other than the client or the contact (« envoie le brouillon de CMD-… au
   transporteur », « réponds directement au transporteur », « réponse adressée à… »), a
   copy (« en copie », « cc », « c/c »), « au lieu du contact », « le destinataire
   sera… ». A time or purpose after the verb (« je vais l'envoyer à midi », « me
   l'envoyer à relire ») names no recipient;
8. ``minimization_refused``: a phone number (« tél. », « no de tél »), a home address
   (« où habite… », « adresse perso »), an internal note or memo;
9. ``restricted_info``: price, cost, margin, quote (« soumissionné », « un estimé »),
   discount (« réduction »), rate, invoiced amount, order value, drawing, part plan,
   price list;
10. ``claim_out_of_scope``: warranty, claim, credit hold or note, refund (« sera-t-elle
    remboursée »), compensation, dispute, lawsuit, formal notice, late-delivery penalty;
11. ``purpose_drift``: sales, prospecting, promotion (« promouvoir », « pub »), an offer,
    solicitation, newsletter, mailing list, campaign;
12. otherwise allowed (``refusal=None``).

An allowed request whose wording asks to send the draft directly (« envoie-la
directement », « envoie-le toi-même », « envoie-le sans attendre ») gets the flag
``auto_send_requested``: never a refusal on its own, the draft is still held for
approval. The same phrase naming a destination is ``recipient_override``; with bypass
wording it is ``prompt_injection``.

Sentence rules (a person tied to a duration, a fault or a sanction) read each sentence
part on its own (split at . ? ! : ; « " and blank lines); word rules read the whole text.

Published known misses: a destination given without a sending verb or a copy word
(« le brouillon est pour le transporteur »), a second recipient after the client
(« envoie-la au client et au transporteur »), an address spelt out (« arobase »), names
the detector does not catch, English words inside a French sentence, terse English
(« ETA? »), and words whose normalised form is another word (« mise en marché »
reads as « mise en marche », a machine start-up).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from alverno_lab.privacy.detect import detect_pi, redact
from alverno_lab.privacy.injection import (
    CREDENTIAL_PATTERN,
    INJECTION_PATTERNS,
    accent_tolerant,
    credential_value,
    is_injection,
)
from alverno_lab.privacy.lexicon import FUNCTION_WORDS, clean_text, normalize
from alverno_lab.privacy.ops_injection import (
    fold_apostrophes,
    is_ops_credential,
    is_ops_injection,
)
from alverno_lab.privacy.ops_types import OpsAssessment, OpsFlag, OpsRefusalCode
from alverno_lab.privacy.roles import OrgRoles
from alverno_lab.privacy.types import PISpan, PrivacyContext
from alverno_lab.text import FRENCH_STOPWORDS

__all__ = ["OpsScreening", "screen_maintenance", "screen_service"]

Module = Literal["service", "maint"]
_PREFIX: dict[Module, str] = {"service": "svc", "maint": "maint"}

# Word boundaries on normalised text, as in the HR classifier: a placeholder's inside
# (« [nom] ») is never a word, and a hyphen glues words (« après-vente » is not « vente »).
_L = r"(?<![\w\[-])"
_R = r"(?![\w\]-])"


def _words(pattern: str) -> re.Pattern[str]:
    """``pattern`` as whole words."""
    return re.compile(f"{_L}(?:{pattern}){_R}")


def _wordlist(block: str) -> list[str]:
    """Space-separated words of a block (easier to review than a list literal)."""
    return block.split()


def _alt(words: Iterable[str]) -> str:
    """Alternation of literal phrases, longest first."""
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


# --------------------------------------------------------------------------- text helpers
_HSPACES_RE = re.compile(r"[^\S\n]+")
# Sentence parts: a part ends at . ? ! : ; « " (a decimal point is kept).
_PART_SPLIT_RE = re.compile(r'(?<!\d)\.|\.(?!\d)|[?!:;«"]')
# Characters that show nothing: format, control and space categories, and the blank
# fillers of Braille and Hangul.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Zs", "Zl", "Zp"})
_BLANK_FILLERS = frozenset(chr(c) for c in (0x2800, 0x3164, 0x115F, 0x1160, 0xFFA0))
_TOKEN_RE = re.compile(r"\[[a-z_]+\]|[^\W\d_]+")
_BLANK_LINE_RE = re.compile(r"[^\S\n]*\n\s*\n\s*")
_ANY_SPACE_RE = re.compile(r"\s+")
# Dotted acronyms: « c.n.e.s.s.t. », « m.t.t.r. » (one letter between dots).
_DOTTED_RE = re.compile(r"(?<![\w.])((?:[a-z]\.){2,}[a-z]?)(?![\w])")


def _visible(text: str) -> bool:
    """True when ``text`` has a visible character."""
    return any(
        unicodedata.category(c) not in _INVISIBLE_CATEGORIES and c not in _BLANK_FILLERS
        for c in text
    )


def _flat(text: str) -> str:
    """The text the rules read: normalised, apostrophes folded, a blank line as a sentence
    end, any other line break as a space, dotted acronyms joined."""
    norm = normalize(fold_apostrophes(text))
    norm = _BLANK_LINE_RE.sub(" ; ", norm)
    norm = _ANY_SPACE_RE.sub(" ", norm)
    return _DOTTED_RE.sub(lambda m: m.group(1).replace(".", ""), norm)


def _parts(text: str) -> list[str]:
    """Non-blank sentence parts of a flattened text."""
    return [p for p in _PART_SPLIT_RE.split(text) if p.strip()]


# --------------------------------------------------------------------------- language
# English function words that are not also French words, as in the HR screen.
_ENGLISH_STOPWORDS = frozenset(
    _wordlist(
        "the is are was were what which who whom whose how many much do does did i my mine "
        "you your yours can could should would will shall get got have has had of to in for "
        "after at with and when where why this that these those be been it its we our they "
        "their them from about there any please pls tell give know"
    )
)
# English business words of the two flows (none is also a French word).
_EN_OPS_RE = _words(
    "orders?|shipped|shipping|delivery|delivered|status|prices?|quotes?|invoices?|customers?"
    "|reply|draft|repairs?|repaired|broken|leaking|warranty|send|sent|technicians?|updates?"
    "|eta|asap|thx|advise|since|morning|overheating|noise|loud|issue|problem"
)
_FRENCH_STOPWORDS = frozenset(normalize(w) for w in FRENCH_STOPWORDS)


def _is_english(text: str) -> bool:
    """Two English words or more, more than the French ones (« Status CMD-… ? » is a
    common loanword, not an English request)."""
    tokens = _TOKEN_RE.findall(text)
    english = sum(t in _ENGLISH_STOPWORDS for t in tokens) + len(_EN_OPS_RE.findall(text))
    french = sum(t in _FRENCH_STOPWORDS for t in tokens)
    return english >= 2 and english > french


# --------------------------------------------------------------------------- injection
# The module-1 patterns read as the HR path reads them: accent-tolerant, apostrophes
# folded; without the HR help relaxation, any credential mention is refused.
_TOLERANT_RE = tuple(
    re.compile(accent_tolerant(p), re.IGNORECASE) for p in (*INJECTION_PATTERNS, CREDENTIAL_PATTERN)
)
_APPROVAL_NOUNS = r"approbation|validation|autorisation|accord|feu vert|relecture|verification"
_OWN = r"(?:mon |ma |ton |ta |votre |notre |aucune |aucun )"
_PARTICIPLE = (
    r"(?:relue?s?|lue?s?|vue?s?|revue?s?|validee?s?|approuvee?s?|verifiee?s?|autorisee?s?"
    r"|signee?s?)"
)
_BYPASS_RE = _words(
    # « sans mon approbation », « sans attendre l'approbation », « sans passer par… »
    rf"sans {_OWN}(?:{_APPROVAL_NOUNS})"
    rf"|sans (?:attendre|passer par) (?:{_OWN}|l'|la |le |une |un )?(?:{_APPROVAL_NOUNS})"
    # « sans que je la relise », « sans attendre que je l'approuve », « sans que je ne
    # l'aie relue »
    r"|sans (?:attendre )?qu(?:e |')(?:(?:je|personne|quelqu'un|on|nous|vous) (?:ne )?"
    r"(?:le |la |les |l')?|j')"
    r"(?:relise|relisions|valide|validions|approuve|approuvions|verifie|revoie|revise|lise"
    rf"|autorise|signe|(?:aie|ait|ayons|ayez|aient) {_PARTICIPLE})"
    # « sans me demander », « sans me consulter »
    r"|sans (?:me|nous) (?:demander|consulter|prevenir|aviser)"
    # « pas besoin de mon approbation », « aucun besoin de valider »
    rf"|(?:pas|aucun|nul) besoin (?:de |d')(?:{_OWN}|l'|la |une )?(?:{_APPROVAL_NOUNS}"
    r"|approuver|valider|relire|verifier)"
    # « contourne l'approbation », « ignore la vérification », « saute l'étape d'approbation »
    r"|(?:contourne|ignore|saute|evite|desactive|outrepasse|court-circuite)(?:r|z|s)?"
    r"(?:-(?:la|le|les))? (?:l'|la |les |le |cette |ces |toute |tout |toutes |ton |votre |tes )?"
    r"(?:etapes? (?:de |d')|processus (?:de |d'))?"
    r"(?:approbation|validation|verification|regles?|controles?|garde-fous?|filtres?)"
    # « passe outre l'approbation »
    r"|passe(?:r|z)? outre (?:a |a l'|l'|la |le |les |mon |ma |ton |ta |votre |cette |ce )?"
    rf"(?:{_APPROVAL_NOUNS}|regles?|controles?|processus|etapes?)"
    # « exécute directement l'écriture », « exécute-la toi-même »
    r"|execute(?:r|z)?(?:-(?:la|le|les))? (?:directement|toi-meme|automatiquement|tout de suite"
    r"|immediatement)"
    r"|ecriture directe|directement dans (?:l'erp|la gmao|le systeme)"
    # « approuve-le toi-même », « valide automatiquement », « approuve-la pour moi »
    r"|(?:approuve|valide|autorise|accepte)(?:r|z)?(?:-(?:la|le|les))? (?:toi-meme|toi meme"
    r"|vous-meme|automatiquement|a ma place|d'office|tout seul|toute seule|par toi-meme|sans moi"
    r"|pour moi)"
    r"|considere(?:r|z)?(?:-(?:la|le|les))? (?:comme )?(?:deja )?(?:approuvee?s?|validee?s?)"
    # « c'est déjà approuvé (par moi) »
    r"|(?:approuvee?s?|validee?s?) par moi|c'est deja (?:approuvee?|validee?)"
)
# A bare « sans validation » is a bypass next to an order to the agent only.
_BARE_BYPASS_RE = _words(rf"sans (?:l'|la |une )?(?:{_APPROVAL_NOUNS})")
_AGENT_ORDER_RE = _words(
    r"(?:envoie|envoyez|envoyer|cree|creez|creer|ouvre|ouvrez|ouvrir|execute|executez|executer"
    r"|enregistre|enregistrez|enregistrer|approuve|approuvez|approuver|transmets|transmettez"
    r"|transmettre|ferme|fermez|fermer|fais|faites|faire|mets|mettez|mettre|prepare|preparez"
    r"|preparer|redige|redigez|rediger|ecris|ecrivez|ecrire|soumets|soumettez|soumettre"
    r"|valide|validez|valider|publie|publiez|publier|expedie|expediez|expedier|ajoute|ajoutez"
    r"|ajouter|modifie|modifiez|modifier|change|changez|changer|annule|annulez|annuler"
    r"|supprime|supprimez|supprimer|marque|marquez|marquer|lance|lancez|lancer|traite|traitez"
    r"|traiter|reponds|repondez|repondre)(?:-(?:la|le|les|lui|moi))?"
)


def _known_word(ctx: PrivacyContext, word: str) -> bool:
    """A normalised word that cannot be a password: corpus, function word, allowlist."""
    return word in ctx.vocabulary or word in FUNCTION_WORDS or word in ctx.allowlist


def _injection(raw: str, ctx: PrivacyContext) -> bool:
    # ``credential_value`` first: ``is_injection`` without the HR help relaxation already
    # refuses any password mention, so the order changes no verdict.
    plain = _ANY_SPACE_RE.sub(" ", fold_apostrophes(raw))
    return (
        credential_value(raw, functools.partial(_known_word, ctx))
        or is_injection(raw, credential_help_ok=False)
        or any(p.search(plain) for p in _TOLERANT_RE)
        or is_ops_injection(plain)
        or is_ops_credential(plain)
    )


def _bypass(flat_raw: str) -> bool:
    if _BYPASS_RE.search(flat_raw):
        return True
    return any(
        _BARE_BYPASS_RE.search(p) is not None and _AGENT_ORDER_RE.search(p) is not None
        for p in _parts(flat_raw)
    )


# --------------------------------------------------------------------------- word families
_BODY = (
    r"main|mains|doigt|doigts|pouce|bras|jambe|jambes|pied|pieds|orteil|orteils|poignet"
    r"|poignets|visage|tete|oeil|yeux|genou|genoux|cheville|chevilles|dos|epaule|epaules|cou"
    r"|hanche|cote|cotes|crane"
)
_SENSITIVE_RE = _words(
    r"blessures?|blessee?s?|se blesser|accidents?|accidentee?s?|cnesst|csst|lesions?"
    r"|premiers? (?:soins|secours)|secouristes?|incidents? (?:de |en )?(?:securite|sst"
    r"|sante et securite)|(?:quasi|presque)[- ]accidents?|hospitalisee?s?|hospitalisation"
    r"|ambulances?|(?:s'est|se sont|s'etait|me suis)(?:-(?:il|elle|ils|elles))? fait mal"
    r"|(?:s'est|se sont|s'etait)(?:-(?:il|elle|ils|elles))? (?:coupee?s?|brulee?s?"
    rf"|ecrasee?s?|fracturee?s?|tordue?s?|foulee?s?|cassee?s?) (?:la |le |les |l'|un |une )?"
    rf"(?:{_BODY})"
    r"|(?<!traces de )(?<!trace de )(?<!marques de )(?<!marque de )brulures?"
    rf"|fractures? (?:du |de la |de l'|d'un |d'une |au |a la )(?:{_BODY})"
)
# Generic nouns that designate people (the organisation's role aliases are added).
_PERSON_NOUNS = _wordlist(
    "technicien technicienne techniciens techniciennes mecanicien mecanicienne mecaniciens "
    "mecaniciennes electricien electricienne electriciens electriciennes employe employee "
    "employes employees ouvrier ouvriere ouvriers ouvrieres travailleur travailleuse "
    "travailleurs travailleuses collegue collegues equipier equipiere equipiers equipieres "
    "prepose preposee preposes preposees intervenant intervenante intervenants intervenantes "
    "reparateur reparatrice reparateurs reparatrices gars magasinier magasiniere magasiniers "
    "magasinieres manutentionnaire manutentionnaires emballeur emballeuse emballeurs "
    "emballeuses preparateur preparatrice preparateurs preparatrices"
)
# Groups of people: a team, a shift, the staff, the (maintenance) service.
_TEAM = (
    r"equipes?|personnel|service d'entretien|service de l'entretien|service de maintenance"
    r"|departement d'entretien|quarts? (?:de |du )?(?:jour|soir|nuit)"
    r"|(?:quel|quelle|quels|quelles|chaque|par|le|les|un|des|du|au|ce|ces|nos|notre|leur"
    r"|deux) quarts?(?! de tour| d'heure)"
    r"|(?:le|du|au|notre|ce|un|des|les|aux|votre) services?"
    r"(?! (?:a la clientele|clients?|apres-vente|des ventes|de livraison|de transport))"
)
# The same groups as bare nouns, after a preposition (« les heures du service »).
_TEAM_NOUNS = (
    r"equipes?|personnel|departement d'entretien|quarts?(?! de tour| d'heure)"
    r"|services?(?! (?:a la clientele|clients?|apres-vente|des ventes|de livraison"
    r"|de transport))"
)
_QUI_AUX = (
    r"a|ont|avait|avaient|aurait|auraient|a-t-il|a-t-elle|est|sont|etait|etaient|sera|serait"
    r"|s'est|s'etait|s'occupe|s'occupait|s'occupera|fait|faisait|fera|travaille|travaillait"
    r"|repare|reparait|entretient|entretenait|signe|signait"
)
_QUI_CLITIC = r"(?:l'|s'en |s'y |s'|y |en |le |la |les |lui |leur |m'|me |nous |vous )?"
_QUI_LEADS = (
    r"savoir|dire|dis|dites|dis-moi|dites-moi|sais|savez|sais-tu|savez-vous|trouve|trouver"
    r"|indique-moi|indiquez-moi|identifie|identifier|demander|connaitre|verifie"
    r"|verifier|confirme|confirmer|et|mais|alors|donc|pis|puis|bref|enfin"
)
# « Qui est le contact… », « qui chez le client… », « et qui est le transporteur » ask for
# a company or the contact, which code resolves: not a person target.
_NOT_TARGETED = (
    r"(?! (?:le |la |les |l')?(?:contacts?|clients?|clientes?|transporteurs?|fournisseurs?"
    r"|fabricants?|distributeurs?|sous-traitants?)(?![\w-]))"
)
_QUI_RE = re.compile(
    rf"(?:^\s*|{_L}(?:{_QUI_LEADS}) )qui {_QUI_CLITIC}(?:{_QUI_AUX}){_R}{_NOT_TARGETED}"
    rf"|{_L}(?:c'est|c'etait) qui{_R}"
    rf"|{_L}(?:par qui|a qui (?:correspond|correspondent|appartient|appartiennent|revient"
    rf"|reviennent|incombe|incombent)){_R}"
    rf"|{_L}de qui (?:est|sont|etait|etaient|vient|venait|releve|relevait|depend){_R}"
)
# « …, qui a resserré le raccord ? » asks who; « La commande CMD-…, qui est en retard, … »
# and « La plieuse, qui a été réparée hier, fuit » are relative clauses.
_QUI_COMMA_RE = re.compile(rf",\s*qui {_QUI_CLITIC}(?:{_QUI_AUX}){_R}{_NOT_TARGETED}")
_ID_BEFORE_RE = re.compile(r"(?:[a-z]{2,4}-[0-9a-z]+(?:-[0-9a-z]+)*|[0-9])$")
_PLACEHOLDER_RE = re.compile(r"\[nom\]|\[matricule\]")
_OWN_ID_RE = re.compile(r"(?:mon|ma|mes) $")
_OWNED_NAME_RE = re.compile(r"(?:je m'appelle|mon nom est|je suis|ici|c'est) $")
_OWNED_NUMBER_RE = re.compile(
    r"(?:mon|ma) (?:matricule|numero d'employe|numero)(?: est)?(?: :)? ?$"
)
_MAL_PARTICIPLES = (
    r"serree?s?|montee?s?|installee?s?|ajustee?s?|reglee?s?|branchee?s?|fixee?s?|faite?s?"
    r"|reparee?s?|remontee?s?|entretenue?s?|graissee?s?|alignee?s?|resserree?s?"
    r"|programmee?s?|posee?s?|lubrifiee?s?|calibree?s?|nettoyee?s?|verifiee?s?|controlee?s?"
)
# Blame words: a sentence with one of them and a person, a team or a small group.
_FAULT_RE = _words(
    r"fautifs?|fautives?|coupables?|fautes?|negligences?|negligente?s?|blamer|blamee?s?"
    r"|erreurs? humaines?|erreurs? de manipulation|responsabilites?|incompetente?s?"
    r"|incompetences?"
    r"|(?:est|sont|etait|etaient|serait|seraient|etre|soit)(?:-(?:il|elle|ils|elles)|-tu)?"
    r" (?:[^\W\d_]+ )?responsables?|tenue?s? responsables?"
    r"|(?:fait|faite|faits|commis|commise|faire|commettre|fais|font)"
    r" (?:une |des |l'|cette |son |leur |un )?(?:erreurs?|gaffes?|bourdes?)"
    r"|(?:a|ont|avait|avaient|aurait|auraient|a-t-il|a-t-elle|ont-ils|ont-elles|aurait-il"
    rf"|aurait-elle) mal (?:{_MAL_PARTICIPLES})"
    r"|baclee?s?|bacler|baclent"
    r"|(?:s'est|se sont|s'etait|s'etaient|se serait|se seraient)(?:-(?:il|elle|ils|elles))?"
    r" trompee?s?|de travers"
    r"|(?:a|ont|avait|avaient) oublie (?:de |d')"
    r"|n'(?:a|avait|ont|avaient) pas (?:verifie|serre|resserre|controle|fait|teste|graisse)"
)
_OWN_FAULT_RE = _words(r"(?:sa|son|leur|leurs) (?:fautes?|negligence|responsabilite)")
_SANCTION_RE = _words(
    r"sanctions?|sanctionner|sanctionnee?s?|mesures? disciplinaires?|disciplinaires?|discipliner"
    r"|reprimand(?:e|ee|es|ees|er|ez|ons)|congedier|congediement|congediee?s?|licencier"
    r"|licenciement|licenciee?s?|mise a pied|mettre a pied|mis a pied|mise a la porte"
    r"|mettre a la porte|mis a la porte|avertissements? (?:ecrits?|verbal|verbaux|formels?"
    r"|disciplinaires?)"
)
# A warning given to someone (« donner un avertissement »): « un avertissement de
# surchauffe » is a machine alarm.
_WARNING_FRAME_RE = _words(
    r"(?:donner|donne|donnez|recevoir|recu|recue|recoit|merite|meriter|envoyer|remettre"
    r"|emettre|servir) (?:un |des |l'|son |cet |ce |leur )?(?:avertissements?|blames?)"
    r"(?! (?:de |d'|du |des |sonores?|lumineux|visuels?|a l'ecran|sur ))"
)
# Passive sanctions (« a été suspendu »), with a person in the same sentence.
_PASSIVE_SANCTION_RE = _words(
    r"(?:a|ont|sera|seront|serait|seraient|soit|etre|a-t-il|a-t-elle|ont-ils|ont-elles"
    r"|doit-il|doit-elle|devrait-il|devrait-elle) (?:ete |etre )?(?:suspendue?s?|renvoyee?s?"
    r"|congediee?s?|licenciee?s?|punie?s?|blamee?s?)"
)
# Mean repair times: always about the service (EFVP « Constat k = 5 »).
_METRIC_RE = _words(
    r"mttr|mtbf du service|delais? moyens?(?! (?:de |d')(?:livraison|approvisionnement"
    r"|commande|expedition|reception|transport|fabrication|production))"
    r"|(?:temps moyens?|durees? moyennes?) (?:de |d')(?:reparation|intervention|depannage"
    r"|reponse|remise en (?:marche|service|route))"
    r"|(?:temps moyens?|durees? moyennes?) (?:pour|a) (?:reparer|intervenir|depanner"
    r"|remettre|regler|corriger|traiter)"
)
# Durations that only work time has: with a person or a team in the sentence.
_WORK_TIME_RE = _words(
    r"mtbf|entre (?:deux |les )?pannes|en moyenne|au total|nombre d'heures"
    r"|heures (?:passees|travaillees|consacrees|facturees)"
    r"|temps (?:passe|consacre|total|d'intervention|de reparation|de depannage|de reponse)"
)
# Any duration word; with a time-taking verb and a person, it is profiling.
_DURATION_RE = _words(
    r"combien (?:de |d')(?:temps|heures|minutes|jours|semaines)|longtemps|temps|heures"
    r"|minutes|durees?|(?<!meilleurs )delais?"
)
_TIME_VERB_RE = _words(
    r"(?:met|mettent|mis|mise|mettre|mettait|mettra|mettrait|prend|prennent|pris|prise"
    r"|prendre|prenait|prendra|prendrait|passe|passent|passee?s?|passer|passait|consacre"
    r"|consacrent|consacree?s?|consacrer|dure|durent|travaille|travaillent|travaillee?s?)"
    r"(?:-t-(?:il|elle|on)|-(?:il|elle|ils|elles|on))?"
)
_COUNT_RE = _words(
    r"combien (?:de |d')(?:interventions?|reparations?|bons?|ot|ots|demandes?|pannes?"
    r"|appels?|depannages?|heures supplementaires)"
)
_RESULT_RE = _words(r"resultats?|lente?s?|rapides?|efficaces?|retards?")
# Ranking words: with a person or a team anywhere in the sentence.
_RANK_RE = _words(
    r"classements?|classer|classe|classez|classe-les|classee?s?|palmares|rendements?"
    r"|productivite|performances?|performante?s?|productive?s?|productifs?|compare|comparer"
    r"|comparez|comparaisons?|comparatif|top [0-9]+"
)
# Speed and superlative words: only when they qualify a person or a team.
_SPEED = (
    r"(?:(?:le|la|les) )?(?:(?:plus|moins|aussi) (?:rapides?|lente?s?|lentement|vite|rapidement"
    r"|efficaces?|efficacement|performante?s?|productive?s?|productifs)|meilleure?s?|pires?)"
)
_COPULA = (
    r"est|sont|etait|etaient|sera|seront|serait|seraient|est-il|est-elle|sont-ils|sont-elles"
    r"|etait-il|etait-elle|a ete|ont ete|reste|semble|parait|devient|repare|reparent|travaille"
    r"|travaillent|intervient|interviennent|fait|font|agit|agissent|va|vont"
)
_SHIFT = r"(?:de |du |d')(?:jour|soir|nuit|garde|entretien|l'entretien|maintenance)"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\[courriel\]")
_ADDRESS_RE = _words(
    r"(?:a|vers) (?:cette|cet) (?:adresse|boite)(?:-ci|-la)?|a (?:ce|cet) courriel"
    r"|a cette adresse courriel"
)
# « à l'adresse suivante » imposes a recipient when the sentence sends something, not when
# it is about the delivery address of the order.
_ADDRESS_FOLLOW_RE = _words(r"a l'adresse (?:suivante|ci-dessous|ci-jointe|que|indiquee)")
_DELIVERY_RE = _words(r"livraisons?|livrer|livree?s?|livre|recevoir|reception")
_SEND_STEMS = (
    r"envoie|envoies|envoyer|envoyez|transmets|transmettre|transmettez|transfere|transferer"
    r"|transferez|redirige|rediriger|redirigez|achemine|acheminer|acheminez|expedie|expedier"
    r"|expediez|fais suivre|faire suivre|faites suivre|ecris|ecrire|ecrivez|reponds|repondre"
    r"|repondez|fais parvenir|faire parvenir|faites parvenir|adresser"
)
_SEND_CLITIC = (
    r"(?:(?:envoie|envoyez|transmets|transmettez|transfere|transferez|redirige|redirigez"
    r"|achemine|acheminez|expedie|expediez|adresse|adressez|ecris|ecrivez|reponds|repondez)"
    r"-(?:le|la|les|lui|leur)|(?:fais|faites)-(?:le|la|les|lui|leur) (?:suivre|parvenir))"
)
_SEND_VERBS = f"{_SEND_CLITIC}|{_SEND_STEMS}"
_DRAFT_OBJECT = (
    r"(?:le|la|les|ce|cette|mon|ma|notre|ton|ta|votre|un|une) (?:brouillon|reponse|message"
    r"|courriel|mot|texte|lettre|copie|confirmation)|tout|ca|cela"
)
_SEND_ADVERBS = (
    r"directement|plutot|aussi|egalement|toi-meme|tout de suite|immediatement|automatiquement"
)
_CLIENT = (
    r"(?:(?:le|la|l'|les|notre|nos|ce|cette|mon|ma|son|sa|leur|leurs) ?)?"
    r"(?:client|cliente|clients|clientes|contact|contacts)(?![\w-])"
)
# Destinations: « au transporteur », « à mon directeur », « chez le fournisseur », never the
# client or the contact, never a time, a purpose or a message (« à midi », « au plus
# vite », « à relire », « à sa question »).
_AU_IDIOMS = (
    r"plus|besoin|sujet|moment|lieu|complet|total|fur|courant|prealable|cas|debut|cours"
    r"|final|nom|sein|retour|pire|mieux|depart|maximum|minimum|meme|niveau|point|dela"
    r"|quotidien|matin|soir|jour|bout|jour le jour"
)
_DEST_DETS = (
    r"la |l'|les |mon |ma |mes |notre |nos |ton |ta |tes |votre |vos |son |sa |ses |leur "
    r"|leurs |un |une |des |ce |cet |cette |ces "
)
_NOT_DEST_NOUNS = (
    r"fin|date|minute|seconde|main|demande|demandes|avance|heure|instant|occasion|aide"
    r"|moment|temps|reunion|semaine|journee|question|questions|courriel|courriels|message"
    r"|messages|relance|plainte|lettre|appel|commentaire|commentaires|note|suite|place"
    r"|lumiere|reception|livraison|expedition|date prevue"
)
_DEST = (
    rf"(?:chez (?!{_CLIENT})|(?:au|aux) (?!{_CLIENT})(?!(?:{_AU_IDIOMS})(?![\w-]))[^\W\d_]"
    rf"|(?:a (?!{_CLIENT})(?:{_DEST_DETS})|vers (?!{_CLIENT})(?:le |{_DEST_DETS}))"
    rf"(?!(?:{_NOT_DEST_NOUNS})(?![\w-]))[^\W\d_]"
    r"|(?:a|vers) quelqu'un|a tout le monde|a tous)"
)
# A short complement after the verb; it never steps over a destination (« envoie-la au
# contact au dossier » names the contact).
_GAP = r"(?: (?!(?:a|au|aux|chez|vers)(?![\w-]))[^\s.?!:;,]+){0,6}?"
_SEND_RE = re.compile(
    # A verb with its clitic (« envoie-la ») or a draft object (« envoie le brouillon »),
    # then a short complement (« de CMD-… », « pour CMD-… »); a bare verb or a bare
    # clitic (« envoie la au… ») takes adverbs only, so « envoyer les pièces à son
    # usine » names no recipient of a draft.
    rf"{_L}(?:{_SEND_CLITIC}{_GAP}|(?:{_SEND_STEMS})(?: (?:{_DRAFT_OBJECT}){_GAP}"
    rf"| (?:le|la|les))?)(?: (?:{_SEND_ADVERBS}))* {_DEST}"
)
# A draft addressed or meant for someone (« réponse adressée au transporteur »).
_ADDRESSED_RE = re.compile(
    rf"{_L}(?:brouillon|reponse|message|courriel|lettre|mot)(?: [^\s.?!:;,]+){{0,4}}?"
    rf" (?:adressee?s?|destinee?s?) {_DEST}"
)
_COPY_RE = re.compile(
    r"(?<![\w\[-])(?:mets|met|mettre|mettez|ajoute|ajouter|ajoutez|inclus|inclure|incluez)"
    r"(?:-(?:le|la|les|moi|nous|lui|leur))? (?:[^\s.?!:;,]+ ){0,5}?(?:en (?:copie|cc|cci)|c/c)"
    rf"(?![\w-])|{_L}(?:en copie|copie conforme|copie cachee|cc|cci|bcc){_R}"
    r"|(?<![\w\[-])c\.c\.|(?<![\w\[-])c/c(?![\w])"
)
_INSTEAD_RE = _words(
    r"(?:au lieu|a la place) (?:du|de la|de l'|des) (?:contacts?|clients?|clientes?"
    r"|destinataires?|personnes?-ressources?)"
    r"|plutot qu'(?:au|a la|a l'|aux) (?:contacts?|clients?|clientes?)"
    r"|(?:comme|nouveau|autre|seul|vrai) destinataire"
    r"|(?:change|changer|changez|remplace|remplacer|remplacez|modifie|modifier|modifiez)"
    r" (?:le |les |de )destinataires?"
    r"|destinataires? ?(?::|=|sera|seront|est|sont|devient|deviendra)(?= )"
    r"(?! (?:le |la |les |l')?(?:contacts?|clients?|clientes?)(?![\w-]))"
)
_MINIMIZATION_RE = _words(
    r"numeros? de (?:telephone|cellulaire|cell|tel)|(?:no|num) (?:de )?(?:tel|telephone|cell"
    r"|cellulaire)"
    r"|(?:son|sa|ses|leur|leurs|le|les|du|un|une|ton|ta|tes|votre|vos) (?:telephones?"
    r"|cellulaires?|cell)|telephones|cellulaires"
    r"|(?<!a )(?<!as )(?<!ont )(?<!avait )(?<!au )(?<!par )(?:telephone|cellulaire)"
    r" (?:du|de la|de l'|des|d'un|d'une|de)"
    r"|(?:le|son|sa|ton|votre|leur|du) tel(?=\.|$| (?:du|de|des|d'|pour|au|:))"
    r"|adresses? (?:personnelles?|residentielles?|privees?|perso|a la maison"
    r"|de (?:la )?(?:maison|residence)|du domicile)|(?:son|sa|ses|leur|leurs|du|au) domicile"
    r"|(?:notes?|commentaires?|remarques?|memos?) (?:internes?|a l'interne)"
    r"|ou (?:il|elle|ils|elles) (?:habite|habitent|demeure|demeurent|reste|restent|vit|vivent)"
    r"|ou (?:habite|habitent|demeure|demeurent|vit|vivent)(?:-t-(?:il|elle))?"
    r"|ou (?:reste|restent)(?:-t-(?:il|elle))? (?:le |la |l'|son |sa )?(?:contact|client"
    r"|cliente|personne)"
)
_RESTRICTED_RE = _words(
    r"(?<!a tout )prix|couts?|coute|coutent|couter|coutait|coutera|couteront|couterait|coutant"
    r"|marges?|soumissions?|soumissionn(?:e|ee|es|ees|er|ez|ons|ait|aient)|devis|rabais"
    r"|escomptes?|tarifs?|tarification|tarifaires?|dessins?"
    r"|reductions?(?! (?:de |d'|du |des ))"
    r"|(?:un|l'|votre|notre|ton|mon|cet|ce|leur|les|des|vos|nos|d') ?estimes?"
    r"(?! (?:de |d')(?:livraison|arrivee|temps|date))"
    r"|plans? (?:de |d')(?:la |les |l')?(?:piece|pieces|fabrication|atelier|usinage)"
    r"|listes? de prix|montants? (?:de la |du |des |de l')?(?:commande|facture|soumission"
    r"|contrat)|valeurs? (?:de la |du )(?:commande|contrat|facture)"
    r"|(?:a|ont|sera|seront|est|etre|ete|a-t-il|a-t-elle|a-t-on) (?:ete )?facturee?s?"
)
_CLAIM_RE = _words(
    r"garantis?|garanties?|reclamations?|retenues? (?:de |sur (?:le |les )?)?(?:credit"
    r"|paiements?)|notes? de credit|rembours(?:e|ee|es|ees|er|era|eront|erait|eraient|ez|ons"
    r"|ement|ements|able|ables)|litiges?|penalites?"
    r"|dedommag(?:e|ee|es|ees|er|era|ement|ements)|indemnisations?|indemniser"
    r"|reclam(?:er|ez|ons)(?! (?:des|une|un|plus|du|de la|de l'|d'|les|le|la) )"
    r"|credit(?:er|e|ee|es|ees|era)|compensations?(?! (?:de |d'|du |des ))"
    r"|compenser (?:le |la |les |notre |ce )(?:client|cliente|clients)"
    r"|poursuites? (?:judiciaires?|en justice)|en justice|mises? en demeure|tribunal"
    r"|tribunaux|avocats?|petites creances"
    r"|(?:nous|vous|les|le|la|l') ?(?:poursuit|poursuivent|poursuivre|poursuivra|poursuivront)"
    r"(?! (?:l'|la |le |les )?(?:inspection|travail|verification|essai|diagnostic"
    r"|production))"
)
_PURPOSE_RE = _words(
    r"ventes?|vendre|vends|vendez|vendions|revendre|prospection|prospecter|prospects?"
    r"|promotions?|promotionnel(?:le)?s?|promos?|promouvoir|promeus|promeut|promouvez"
    r"|promotionner|pubs?|publicites?|publicitaires?|marketing|upsell"
    r"|offres? (?:commerciales?|promotionnelles?|speciales?|du mois|de la semaine)"
    r"|(?:une|l'|cette|notre|nos|des|votre|son|ton|leur) ?offres?"
    r"(?! (?:de |d')(?:livraison|service|ramassage|transport|emploi))"
    r"|sollicitations?|solliciter|sollicite-(?:le|la|les)|sollicitez|infolettres?"
    r"|newsletters?|listes? (?:d'envoi|de diffusion)|campagnes?"
)
_AUTO_SEND_RE = re.compile(
    rf"{_L}(?:envoie|envoies|envoyer|envoyez|transmets|transmettre|transmettez|expedie"
    r"|expedier|expediez)"
    r"(?:-(?:le|la|les)| (?:le|la|les))?(?: (?:le|la|les|ce|cette) (?:brouillon|reponse|message"
    r"|courriel|mot|texte|lettre))?(?: [^\s.?!:;,]+){0,3}? (?:directement|toi-meme|toi meme"
    r"|vous-meme|automatiquement|tout de suite|immediatement|sans attendre|sans delai|aussitot"
    rf"|tout seul|tout de go|des que possible|au plus vite|sur-le-champ){_R}"
)


# --------------------------------------------------------------------------- per-organisation
@dataclass(frozen=True)
class _Lex:
    """Patterns built once per organisation: people (generic nouns and role aliases)."""

    person: re.Pattern[str]
    team: re.Pattern[str]
    which: re.Pattern[str]
    name: re.Pattern[str]
    ordered: re.Pattern[str]
    blame: re.Pattern[str]
    discipline: re.Pattern[str]
    per_person: re.Pattern[str]
    speed: re.Pattern[str]


@functools.lru_cache(maxsize=8)
def _lex(roles: OrgRoles) -> _Lex:
    aliases = [a for r in roles.roles for a in r.aliases]
    person = _alt([*_PERSON_NOUNS, *aliases])
    people = f"{person}|personnes?|celui|celle|ceux|celles|quelqu'un"
    group = f"{person}|{_TEAM}"
    dets = r"(?:(?:la|le|les|l'|ce|cet|cette|ces|notre|nos|chaque|un|une) ?)?"
    adjectives = r"(?:(?:autre|nouveau|nouvel|nouvelle|ancien|ancienne) )?"
    copula = r"(?:(?:est|etait|sont|etaient|sera|serait|fut)(?:-(?:il|elle|ce))? )"
    return _Lex(
        person=_words(person),
        team=_words(f"{group}|quelqu'un"),
        which=_words(
            rf"(?:quel|quelle|quels|quelles) {adjectives}(?:{people})"
            rf"|(?:quel|quelle|quels|quelles) {copula}(?:(?:le|la|les|l') ?){adjectives}"
            rf"(?:{person}|personnes?)"
            rf"|(?:lequel|laquelle|lesquels|lesquelles) (?:des|de nos|de ces|de vos|de tes)"
            rf" (?:[^\W\d_]+ )?(?:{person})"
        ),
        name=re.compile(
            rf"{_L}(?:nom|noms|prenom|prenoms|identite|identites)"
            r"(?: et (?:le |la |les |son |sa |ses )?[^\W\d_]+)? (?:de|du|des|d')\s?"
            rf"{dets}(?:[^\W\d_]+ )?(?:{people}){_R}"
            rf"|{_L}(?:son|sa|ses|leur|leurs) (?:nom|noms|prenom|prenoms|identite){_R}"
            rf"|{_L}(?:initiales?|matricules?|numeros? (?:d'employee?s?|de matricule)){_R}"
        ),
        ordered=_words(
            # « Trouve le mécanicien qui a changé… », « identifie l'employé responsable »
            r"(?:donne|donnez|trouve|trouvez|nomme|nommez|dis|dites|indique|indiquez)"
            rf"(?:-moi|-nous)? (?:le|la|les|l') ?(?:{person})(?: [^\s.?!:;,]+){{0,3}}? qui"
            r"|(?:identifie|identifiez|identifier)(?:-moi|-nous)?"
            rf" (?:le|la|les|l'|ce|cet|cette|ces) ?(?:{person}|personnes?)"
        ),
        blame=_words(
            # « par la faute de qui », « à cause du mécanicien de soir », « erreur du
            # technicien »
            rf"par la faute (?:de |du |des |d')\s?{dets}(?:qui|quelqu'un|personne|{group})"
            r"|(?:la )?faute (?:de|a) qui|a qui (?:la faute|revient la faute|la responsabilite)"
            rf"|a cause (?:du |de la |de l'|des |d'un |d'une |de )\s?(?:{group}|quelqu'un)"
            rf"|erreurs? (?:du |de la |de l'|des |d'un |d'une )\s?(?:{group})"
        ),
        discipline=_words(
            # « suspendre le technicien », « avertir l'employé qui… » (« avertis le
            # technicien que… » only informs)
            r"(?:suspendre|suspends|suspendez|renvoyer|renvoie|renvoyez|virer|vire|virez"
            r"|congedie|punir|punis|punissez|avertir|avertis|avertissez|blamer)"
            rf"(?:-le|-la|-les)? {dets}(?:{person})(?! que| qu')"
            rf"|avertissements? (?:a |au |aux )\s?{dets}(?:{person})"
        ),
        per_person=_words(
            # « les heures du mécanicien », « la durée des interventions de chaque
            # technicien », « combien d'OT par technicien »
            r"(?:durees?|temps|heures|minutes)(?: [^\W\d_']+){0,2}? (?:du |de la |de l'|des |de )"
            rf"\s?(?:chaque |nos |ces |notre |cet |ce |cette |leur )?(?:{person}|{_TEAM_NOUNS})"
            rf"|par (?:{person}|quart|quarts|equipe|equipes|employe|personne|tete)"
        ),
        speed=_words(
            # « le quart de nuit est-il plus lent », « les techniciens les plus rapides »,
            # « le plus rapide des techniciens », « meilleur technicien »
            rf"(?:{group})(?: {_SHIFT})?(?: (?:{_COPULA}))?(?: le| la| les)? {_SPEED}"
            rf"|{_SPEED} (?:(?:de|des|du|d'|parmi|entre) ?)?(?:(?:les|nos|ces|mes|tes) )?"
            rf"(?:{person}|{_TEAM_NOUNS})"
        ),
    )


def _small(part: str, ctx: PrivacyContext) -> bool:
    """A role (by shift) mention with fewer than k people."""
    return any(m.headcount < ctx.k for m in ctx.roles.mentions(part))


def _any_part(text: str, test: Callable[[str], bool]) -> bool:
    return any(test(p) for p in _parts(text))


# --------------------------------------------------------------------------- rules
def _qui_asks(part: str) -> bool:
    """« qui » asking who did something; a relative clause after an id or between commas
    is no question."""
    if _QUI_RE.search(part):
        return True
    for match in _QUI_COMMA_RE.finditer(part):
        before = part[: match.start()].rstrip()
        if _ID_BEFORE_RE.search(before) or "," in part[match.end() :]:
            continue
        return True
    return False


def _identify(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del ctx
    if _any_part(text, _qui_asks) or lex.which.search(text) or lex.ordered.search(text):
        return True
    return any(
        not _OWN_ID_RE.search(text[max(0, m.start() - 4) : m.start()])
        for m in lex.name.finditer(text)
    )


def _placeholder(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    """A ``[nom]`` or ``[matricule]`` that is not the asker's own."""
    del lex, ctx
    for match in _PLACEHOLDER_RE.finditer(text):
        before = text[max(0, match.start() - 40) : match.start()]
        owned = _OWNED_NAME_RE if match.group(0) == "[nom]" else _OWNED_NUMBER_RE
        if not owned.search(before):
            return True
    return False


def _disciplinary(part: str, lex: _Lex) -> bool:
    """A sanction word, a warning given to someone, or a passive sanction with a person."""
    return bool(
        _SANCTION_RE.search(part)
        or lex.discipline.search(part)
        or (
            (_WARNING_FRAME_RE.search(part) or _PASSIVE_SANCTION_RE.search(part))
            and lex.team.search(part)
        )
    )


def _small_role_fault(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    return _any_part(
        text,
        lambda p: (
            _small(p, ctx)
            and bool(_FAULT_RE.search(p) or lex.blame.search(p) or _disciplinary(p, lex))
        ),
    )


def _fault(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del ctx
    if _OWN_FAULT_RE.search(text) or lex.blame.search(text):
        return True
    return _any_part(text, lambda p: bool(_FAULT_RE.search(p) and lex.team.search(p)))


def _sanction(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del ctx
    return _any_part(text, lambda p: _disciplinary(p, lex))


def _metric(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del lex, ctx
    return _METRIC_RE.search(text) is not None


def _small_role_profiling(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del lex
    return _any_part(
        text,
        lambda p: (
            _small(p, ctx)
            and bool(
                _DURATION_RE.search(p)
                or _WORK_TIME_RE.search(p)
                or _RANK_RE.search(p)
                or _RESULT_RE.search(p)
            )
        ),
    )


def _duration_part(part: str, lex: _Lex) -> bool:
    if lex.per_person.search(part):
        return True
    if not lex.team.search(part):
        return False
    return bool(
        _WORK_TIME_RE.search(part)
        or _COUNT_RE.search(part)
        or (_DURATION_RE.search(part) and _TIME_VERB_RE.search(part))
    )


def _duration(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del ctx
    return _any_part(text, lambda p: _duration_part(p, lex))


def _rank(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del ctx
    if lex.speed.search(text):
        return True
    return _any_part(text, lambda p: bool(_RANK_RE.search(p) and lex.team.search(p)))


def _matches(pattern: re.Pattern[str]) -> Callable[[str, _Lex, PrivacyContext], bool]:
    def check(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
        del lex, ctx
        return pattern.search(text) is not None

    return check


def _address(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del lex, ctx
    if _EMAIL_RE.search(text) or _ADDRESS_RE.search(text):
        return True
    return _any_part(
        text,
        lambda p: bool(
            _ADDRESS_FOLLOW_RE.search(p)
            and (_SEND_RE.search(p) or re.search(rf"{_L}(?:{_SEND_VERBS}|courriel)", p))
            and not _DELIVERY_RE.search(p)
        ),
    )


def _send(text: str, lex: _Lex, ctx: PrivacyContext) -> bool:
    del lex, ctx
    return bool(_SEND_RE.search(text) or _ADDRESSED_RE.search(text))


@dataclass(frozen=True)
class _Rule:
    """One screen rule: its id suffix, its refusal code and its check."""

    name: str
    code: OpsRefusalCode
    check: Callable[[str, _Lex, PrivacyContext], bool]


_SENSITIVE = _Rule("sensitive", "sensitive_out_of_scope", _matches(_SENSITIVE_RE))
_IDENTIFY = _Rule("person.identify", "person_targeted", _identify)
_PLACEHOLDER = _Rule("person.placeholder", "person_targeted", _placeholder)
_TAIL = (
    _Rule("minimization", "minimization_refused", _matches(_MINIMIZATION_RE)),
    _Rule("restricted", "restricted_info", _matches(_RESTRICTED_RE)),
    _Rule("claim", "claim_out_of_scope", _matches(_CLAIM_RE)),
    _Rule("purpose", "purpose_drift", _matches(_PURPOSE_RE)),
)
_RULES: dict[Module, tuple[_Rule, ...]] = {
    "service": (
        _SENSITIVE,
        _IDENTIFY,
        _PLACEHOLDER,
        _Rule("recipient.address", "recipient_override", _address),
        _Rule("recipient.send", "recipient_override", _send),
        _Rule("recipient.copy", "recipient_override", _matches(_COPY_RE)),
        _Rule("recipient.instead", "recipient_override", _matches(_INSTEAD_RE)),
        *_TAIL,
    ),
    "maint": (
        _SENSITIVE,
        _IDENTIFY,
        _PLACEHOLDER,
        _Rule("person.small_role", "person_targeted", _small_role_fault),
        _Rule("person.sanction", "person_targeted", _sanction),
        _Rule("person.fault", "person_targeted", _fault),
        _Rule("profiling.metric", "profiling", _metric),
        _Rule("profiling.small_role", "profiling", _small_role_profiling),
        _Rule("profiling.duration", "profiling", _duration),
        _Rule("profiling.rank", "profiling", _rank),
        *_TAIL,
    ),
}


# --------------------------------------------------------------------------- screens
@dataclass(frozen=True)
class OpsScreening:
    """Outcome of an ops screen: redacted text, spans, assessment.

    ``raw_values`` lives in memory only, for the output guard's echo check; it is kept out
    of ``repr`` and of equality, as in the HR ``Screening``.
    """

    redacted: str
    spans: tuple[PISpan, ...]
    assessment: OpsAssessment
    raw_values: tuple[str, ...] = field(default=(), repr=False, compare=False)


def _refuse(prefix: str, rule: str, code: OpsRefusalCode) -> OpsAssessment:
    return OpsAssessment(refusal=code, rule_ids=(f"{prefix}.{rule}",), flags=())


def _assess(redacted: str, ctx: PrivacyContext, *, raw: str, module: Module) -> OpsAssessment:
    """The decision order of the module docstring; ``raw`` is the cleaned unredacted text."""
    prefix = _PREFIX[module]
    if not _visible(raw) or not _visible(redacted):
        return _refuse(prefix, "empty", "empty")
    if _injection(raw, ctx):
        return _refuse(prefix, "injection", "prompt_injection")
    if _bypass(_flat(raw)):
        return _refuse(prefix, "injection.bypass", "prompt_injection")
    text = _flat(redacted)
    if _is_english(text):
        return _refuse(prefix, "lang.en", "unsupported_language")
    lex = _lex(ctx.roles)
    for rule in _RULES[module]:
        if rule.check(text, lex, ctx):
            return _refuse(prefix, rule.name, rule.code)
    if _AUTO_SEND_RE.search(text):
        flags: tuple[OpsFlag, ...] = ("auto_send_requested",)
        return OpsAssessment(
            refusal=None, rule_ids=(f"{prefix}.allowed", f"{prefix}.auto_send"), flags=flags
        )
    return OpsAssessment(refusal=None, rule_ids=(f"{prefix}.allowed",), flags=())


def _screen(request: str, ctx: PrivacyContext, module: Module) -> OpsScreening:
    text = _HSPACES_RE.sub(" ", clean_text(request))
    spans = detect_pi(text, ctx, mode="question")
    redaction = redact(text, spans)
    return OpsScreening(
        redacted=redaction.text,
        spans=redaction.spans,
        assessment=_assess(redaction.text, ctx, raw=text, module=module),
        raw_values=tuple(text[s.start : s.end] for s in redaction.spans),
    )


def screen_service(request: str, ctx: PrivacyContext) -> OpsScreening:
    """Screen a customer-service (3a) request: rules 1-4, 5 (identification and
    placeholders), 7-11 of the module docstring; pure, no I/O."""
    return _screen(request, ctx, "service")


def screen_maintenance(request: str, ctx: PrivacyContext) -> OpsScreening:
    """Screen a maintenance (3b) request: rules 1-6 and 8-11 of the module docstring (no
    recipient rule: 3b sends nothing); pure, no I/O."""
    return _screen(request, ctx, "maint")
