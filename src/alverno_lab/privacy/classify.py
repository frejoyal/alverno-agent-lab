"""Deterministic classification of a question: what it asks for, about whom (spec §5.4, §5.5).

Two axes stay apart (D5): the *requested level* (what an answer would disclose) decides
refusal, while the *contained PI* (what the question includes) only decides redaction.
A name or « mon superviseur » on its own discloses nothing; a record attribute tied to a
person, a small group or an identification request does.

Every signal is a word-bounded regular expression applied to the normalised redacted
text (§5.1), where placeholders read ``[nom]``, ``[nas]``... Each match emits a rule id;
rule ids carry no value and no offset, so they may go into traces.

A compound question is refused if any part of it matches: the refusal rules run on each
sentence part, so an exemption in one sentence never covers a request in another. Two
things are read on the whole question: the ownership of placeholders (« ma nouvelle
adresse : [ADRESSE], [CODE_POSTAL] ») and whether the asker asks how to proceed
(« Mon superviseur m'a averti hier. Que dois-je faire ? »). The asker must be involved
(je, j', me, moi) in the very sentence that ties a person to a record, or, under M13, in
another sentence when no sentence asks to identify, names someone, asks for someone's
pay, contact details or return date, or asks to know something about them (« Mon
collègue s'est blessé hier. Que dois-je faire ? »). The sentence that ties the person to
the record must never ask to know or check something about the person (« Comment savoir
si le cariste est malade ? »), ask for pay, contact details or a return date, or ask
about the person with an inverted verb (« Que dois-je faire, mon collègue est-il
malade ? »). Asking which role to contact (« Qui dois-je avertir si mon collègue est
blessé ? », « Qui contacter... », « À qui dois-je signaler... ») involves the asker and is
never an identification request on its own; « Qui est malade, qui contacter ? » still is.

A policy eligibility question (« Quels employés ont droit au télétravail ? ») is not an
identification request: « quel(le)s » + a plural person noun or « quel(le) » + a generic
one, with an entitlement and no record attribute, named person, small group or singling
(M13).

A record question whose subject is only a pronoun (« Est-elle en congé de maladie ? »),
a person word without a role (« l'homme du quai »), a third-person possessive or a
lowercase given name targets a person when an earlier sentence names one, or when the
question mentions no indefinite person (« un employé », « quelqu'un »). An institution
as the subject (« Le service de la paie a-t-il reçu... ») does not.

Published known misses: questions in a foreign language other than English, a record
word in English inside a French sentence (« [NOM] is sick this week »), verbless
questions (« [NOM], malade ? »), « fait combien » with no pay tail (« La présidente fait
combien ? »), a trailing « qui » (« Les employés en congé sont qui ? ») and names the
detector does not catch (lowercase, nicknames, initials).
"""

from __future__ import annotations

import bisect
import functools
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from alverno_lab.privacy.detect import detect_pi, redact
from alverno_lab.privacy.injection import credential_value, is_injection
from alverno_lab.privacy.lexicon import FUNCTION_WORDS, clean_text, given_names, normalize
from alverno_lab.privacy.roles import OrgRoles
from alverno_lab.privacy.types import (
    Assessment,
    Kind,
    PISpan,
    PrivacyContext,
    ScreenCode,
    Screening,
    Subject,
)
from alverno_lab.text import FRENCH_STOPWORDS

# Word boundaries on normalised text: a placeholder's inside (« [nas] ») is never a word,
# and a hyphen glues words (« donne-moi » is not « moi »).
_L = r"(?<![\w\[-])"
_R = r"(?![\w\]-])"


def _alt(words: Iterable[str]) -> str:
    """Alternation of literal phrases, longest first."""
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


def _words(pattern: str) -> re.Pattern[str]:
    """``pattern`` as whole words."""
    return re.compile(f"{_L}(?:{pattern}){_R}")


# --------------------------------------------------------------------------- lexicons
def _wordlist(block: str) -> list[str]:
    """Space-separated words of a block (easier to review than a list literal)."""
    return block.split()


# Generic nouns that designate a person (§5.4 other.role), singular then plural.
_GENERIC_PERSON_SINGULAR = _wordlist(
    "collegue employe employee salarie salariee travailleur travailleuse personne "
    "gestionnaire superviseur superviseure remplacant remplacante"
)
_GENERIC_PERSON_PLURAL = [f"{w}s" for w in _GENERIC_PERSON_SINGULAR]
# Singular determiners that point at one person (§5.4 other.role).
_SINGULAR_DETERMINERS = (
    "mon ",
    "ma ",
    "notre ",
    "le ",
    "la ",
    "l'",
    "du ",
    "de la ",
    "de l'",
    "au ",
    "a la ",
    "ce ",
    "cet ",
    "cette ",
    "son ",
    "sa ",
)
# Determiners allowed before the person of a record noun (« dossier de mon collègue »).
_PERSON_DETERMINERS = (
    "la ",
    "l'",
    "mon ",
    "ma ",
    "mes ",
    "notre ",
    "nos ",
    "ce ",
    "cet ",
    "cette ",
    "son ",
    "sa ",
    "ses ",
    "leur ",
    "leurs ",
)
# Adjectives allowed between a determiner and the role noun (« mon ancien superviseur »).
_ROLE_ADJECTIVES = _wordlist("ancien ancienne nouveau nouvel nouvelle autre seul seule")
_RECORD_NOUNS = [
    *_wordlist(
        "salaire salaires remuneration paie paies absence absences dossier dossiers "
        "evaluation evaluations avertissement avertissements diagnostic diagnostics sante "
        "maladie maladies blessure blessures accident accidents plainte plaintes adresse "
        "adresses telephone courriel courriels nas matricule matricules horaire horaires "
        "solde soldes anciennete age coordonnees"
    ),
    "date de naissance",
    # Review 2: the spelling « paye ».
    "paye",
    "payes",
]
_PERSON_PLACEHOLDERS = r"\[nom\]|\[matricule\]|\[courriel\]|\[telephone\]|\[nas\]"
_STATUS_WORDS = [
    "en conge",
    "en arret",
    "en vacances",
    "en retrait preventif",
    "en invalidite",
    # M14 (2026-11-01): common health synonyms.
    "en depression",
    "en burn-out",
    "en burnout",
    # Review 2: discipline.
    "en probation",
    *_wordlist(
        "absent absente absents absentes malade malades enceinte enceintes blesse blessee "
        "blesses blessees suspendu suspendue suspendus suspendues congedie congediee "
        "congedies congediees hospitalise hospitalisee hospitalises hospitalisees"
    ),
]
_EVENT_PARTICIPLES = [
    *_wordlist(
        "pris prise prises eu eue eues recu recue recus recues depose deposee deposes "
        "deposees cause causee causes causees averti avertie avertis averties suspendu "
        "suspendue suspendus suspendues blesse blessee blesses blessees congedie congediee "
        "congedies congediees"
    ),
    # M14 (2026-11-01): dismissal and health synonyms.
    *_wordlist(
        "licencie licenciee licencies licenciees renvoye renvoyee renvoyes renvoyees "
        "hospitalise hospitalisee hospitalises hospitalisees"
    ),
    "mis a pied",
    "mise a pied",
    "mises a pied",
    "porte plainte",
    # Review 2: dismissal (« a été mis dehors », « a-tu été mis à la porte »).
    "mis dehors",
    "mise dehors",
    "mis a la porte",
    "mise a la porte",
]
# Adverbs allowed between a verb and its status or participle (« est-il encore malade »,
# M14). « pas » and « jamais » are left out: « n'est pas malade » is no record.
_ADVERBS = r"(?:(?:encore|toujours|deja|actuellement|presentement|vraiment|rendu) ){0,2}"
_DEMOGRAPHIC_RE = re.compile(
    rf"{_L}(?:enceintes?|agee?s? de|de \d+ ans|plus jeunes?|plus agee?s?|femmes?|hommes?"
    rf"|handicapee?s?|origine|religion){_R}"
)
_SMALL_NUMBERS = {
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
}
# Record attributes whose disclosure is level 5: health, accident, discipline, complaint,
# SIN, bank account (§5.4).
_SENSITIVE_RE = _words(
    "sante|maladies?|malades?|blessures?|blessee?s?|diagnostics?|enceintes?|retrait preventif"
    "|invalidite|en arret|arrets? de travail|accidents?|avertissements?|avertie?s?"
    "|suspendue?s?|congediee?s?|hospitalisee?s?|en depression|en burn-?out|licenciee?s?"
    "|renvoyee?s?|mise?s? a pied"
    r"|mesures? disciplinaires?|plaintes?|porte plainte|nas|\[nas\]|compte bancaire"
    r"|\[compte_bancaire\]"
    # Review 2 (« avertir » alone stays out: « qui dois-je avertir » is a contact verb).
    r"|en probation|mise? dehors|mise? a la porte|fait (?:avertir|suspendre|renvoyer)"
    r"|cancer|depression|cnesst|operee?|souffre|antidepresseurs|bebe|fait mal"
)

# English function words that are not also French words (« a », « on », « or », « an »
# and « as » are left out), for lang.en (§5.4).
_ENGLISH_STOPWORDS = frozenset(
    _wordlist(
        "the is are was were what which who whom whose how many much do does did i my mine "
        "you your yours can could should would will shall get got have has had of to in for "
        "after at with and when where why this that these those be been it its we our they "
        "their them from about there any please tell give know"
    )
)
# English record words count as English: « Is [NOM] sick ? » has a single stopword.
_EN_RECORD_RE = re.compile(
    rf"{_L}(?:sick|salary|salaries|wages?|fired|pregnant|address|phone|leave|injured"
    rf"|warnings?|complaints?|pay|paid|earns?){_R}"
)
_FRENCH_STOPWORDS = frozenset(normalize(w) for w in FRENCH_STOPWORDS)
_TOKEN_RE = re.compile(r"\[[a-z_]+\]|[^\W\d_]+")
# « où » (where) is procedural, « ou » (or) is not; they differ only by the accent.
_OU_RE = re.compile(r"(?<![\w-])où(?![\w-])", re.IGNORECASE)
_OU_MARK = "ou-lieu"
# Sentence parts: a part ends at . ? ! : ; « " or a newline (a decimal point is kept).
_PART_SPLIT_RE = re.compile(r'(?<!\d)\.|\.(?!\d)|[?!:;«"\n]')
# Horizontal whitespace, collapsed before detection (review 2: « 819  555  0142 »).
_HSPACES_RE = re.compile(r"[^\S\n]+")

# --------------------------------------------------------------------------- signals
_SELF_RE = re.compile(rf"(?<![\w\[])je{_R}|{_L}(?:me|moi|mon|ma|mes){_R}|{_L}[jm]'(?=[^\W\d_])")
_SELF_INVOLVED_RE = re.compile(rf"(?<![\w\[])je{_R}|{_L}(?:me|moi){_R}|{_L}[jm]'(?=[^\W\d_])")
_SELF_NAME_RE = re.compile(r"(?:je m'appelle|mon nom est|je suis) \[nom\]")
_OWNER_WORDS = frozenset({"mon", "ma", "mes"})
_OWNER_WINDOW = 3
# A non-name placeholder is the asker's when a noun of its type, itself right after
# mon/ma/mes (one adjective allowed), occurs among the 6 tokens before it: « mon numéro de
# téléphone est le [téléphone] », but not « le salaire de mon [matricule] ».
_TYPE_NOUN_WINDOW = 6
_TYPE_NOUNS: dict[str, frozenset[str]] = {
    "[nas]": frozenset({"nas", "assurance"}),
    "[matricule]": frozenset({"matricule", "numero"}),
    "[adresse]": frozenset({"adresse", "domicile"}),
    "[code_postal]": frozenset({"code", "postal", "adresse"}),
    "[telephone]": frozenset({"telephone", "cellulaire", "cell", "numero"}),
    "[courriel]": frozenset({"courriel", "adresse"}),
    "[compte_bancaire]": frozenset({"compte", "transit", "institution"}),
    "[date_naissance]": frozenset({"naissance", "date"}),
    "[nam]": frozenset({"nam", "carte", "assurance"}),
    "[numero]": frozenset({"numero", "permis", "carte", "dossier", "compte"}),
}

_IDENTIFY_RE = re.compile(
    rf"^\s*qui{_R}|{_L}(?:a|de|pour|avec|chez|savoir|dire|dis|dites|sais|savez) qui{_R}"
    rf"|{_L}(?:lequel|laquelle|lesquels|lesquelles){_R}"
    rf"|{_L}le nom (?:de|du|des|d'){_R}|{_L}son nom{_R}"
    # After a lead-in (« Au fait, qui est malade, svp ? », « Et qui a été congédié »):
    # « qui » then a third-person verb. A relative clause after a person noun phrase
    # (« Un employé, qui est en arrêt, garde-t-il... ») is masked first (relative_qui).
    r"|(?:^|,)\s*(?:(?:et|alors|mais|donc|pis|puis|bref|enfin) )?qui "
    rf"(?=(?:est|sont|etait|sera|a|ont|avait|remplace|s'est){_R})"
    # Review 2: « Sais-tu qui... », « C'est qui qui... », « Y a qui en congé... »,
    # « les noms des employés... », « Nomme-moi... ».
    rf"|{_L}(?:sais|savez|savais|saviez)-(?:tu|vous) qui{_R}"
    rf"|{_L}(?:c'est|c'etait|y a|y'a|il y a) qui{_R}"
    rf"|{_L}les noms (?:de|du|des|d'){_R}|{_L}leurs noms{_R}"
    rf"|{_L}(?:nomme|nommez|nommer)(?:-moi)?{_R}"
)
_ENTITLEMENT_RE = _words(
    r"a droit|a-t-(?:il|elle) droit|ont droit|droit a|admissibles?|peut-(?:il|elle)"
    r"|doit-(?:il|elle)"
)
_PROCEDURAL_RE = re.compile(
    rf"{_L}(?:comment|ou-lieu|a qui|qui contacter|quel formulaire|quelle procedure|que faire"
    r"|que dois-je faire|quoi faire|quelles etapes|dois-je|faut-il|puis-je"
    r"|est-ce que je (?:peux|dois)|est-ce que j'(?:ai le droit|aurai)"
    rf"|en cas de|en cas d'|lorsque|lorsqu'|doit-(?:il|elle) figurer){_R}"
    # « si je comprends bien » restates a fact; it does not ask how to proceed.
    rf"|{_L}(?:si|quand) (?:je{_R}(?! comprends)|j'(?=[^\W\d_]))"
)
# Asking which role to contact (« Qui dois-je avertir si mon collègue est blessé ? »,
# « Qui contacter... », « À qui dois-je signaler... »): the asker's own procedure, so it
# is procedural and involves the asker (G1, review 2).
_CONTACT_ROLE = (
    r"(?:a )?qui (?:(?:dois-je|devrais-je|faut-il|doit-on|je dois|on doit) )?"
    r"(?:contacter|avertir|appeler|prevenir|aviser|informer|joindre)"
    # An object clitic and more verbs (« à qui dois-je le signaler », « à qui dois-je
    # remettre le rapport », review 2).
    r"|a qui (?:(?:dois-je|devrais-je|faut-il|doit-on|je dois|on doit|je peux|puis-je) )?"
    r"(?:(?:le|la|les|lui|l'|en) ?)?"
    r"(?:m'adresser|ecrire|signaler|parler|en parler|remettre|envoyer|donner|dire|transmettre"
    r"|declarer|faire parvenir)"
    # The asker's present tense (« à qui je remets ma feuille de temps », « à qui
    # j'envoie »); a third-person subject (« à qui le superviseur a donné ») stays out.
    r"|a qui (?:je |j')(?:le |la |les |l')?"
    r"(?:remets|envoie|donne|dis|signale|transmets|declare|parle|ecris)"
)
_CONTACT_ROLE_RE = _words(_CONTACT_ROLE)
# Role questions: they never count as identification requests (the matched phrase is
# masked before the identify rules run), so « Qui est malade, qui contacter ? » still is.
_PROCEDURAL_ROLE_RE = _words(
    _CONTACT_ROLE + r"|a qui je m'adresse"
    r"|qui (?:approuve|signe|autorise|recoit|traite)"
    r"|(?:qui est|c'est qui) (?:la personne designee|le responsable|la responsable)"
    r"|personne designee"
)
_OWN_NOUNS = r"solde|dossier|evaluation|avertissement|absence|banque d'heures|paie"
_OWN_RE = re.compile(
    rf"{_L}(?:me reste-t-il|il me reste|ai-je (?:pris|eu|recu|accumule)e?s?){_R}"
    # Bounded gap: an unbounded one makes a long question quadratic.
    rf"|{_L}combien{_R}[^?.!]{{0,80}}{_L}ai-je{_R}"
    # « À combien de semaines de vacances ai-je droit ? » asks for the policy, not a
    # record; « ai-je encore droit » and « ai-je pris » stay own (freeze review).
    rf"(?! droit{_R})"
    rf"|{_L}pourquoi (?:ma|mon) (?:paie|salaire|talon|releve){_R}"
    rf"|{_L}(?:mon|ma|mes) (?:solde|dossier|evaluation|avertissement|absence|banque d'heures"
    # A short complement (« mon solde de vacances », « de congés de maladie »).
    rf"|paie|demande)s?(?: d'?[^\W\d_]+| (?:de|des|du|d')\s?[^\W\d_]+(?: de [^\W\d_]+)?)?"
    rf" (?:est|a|a ete|a-t-elle ete|a-t-il ete|est-elle|est-il){_R}"
    # A policy predicate asks for no record value (« Mon dossier est-il protégé ? »,
    # « Ma paie est-elle versée le jeudi ? », review 2).
    rf"(?! {_ADVERBS}(?:confidenti(?:el)|conserve|transferable|verse|protege|accessible|archive"
    rf"|partage|communique|detruit)(?:le|e)?s?{_R})"
    # The interrogative first (« Quel est mon solde de vacances ? », « C'est quoi mon
    # solde », « Quel est le solde de mes vacances ? »), M14.
    rf"|{_L}(?:quel(?:le)?s? (?:est|sont)|c'est quoi|combien (?:est|reste-t-il (?:dans|sur)))"
    rf" (?:(?:mon|ma|mes) (?:{_OWN_NOUNS})s?|(?:le|la|les) (?:{_OWN_NOUNS})s? (?:de |d')"
    rf"(?:mon|ma|mes)){_R}"
)
_RESTRICTED_RE = _words(
    r"grilles? salariales?|echelles? salariales?|masse salariale"
    r"|budget (?:des salaires|rh|de la paie)|marges?|prix de vente|soumissions?"
)
_LEGAL_RE = _words(
    r"est-ce (?:legal|illegal)|contraire a la loi|viole (?:la loi|les normes)|avocate?s?"
    r"|poursuivre|poursuites?|tribunal|tribunaux|recours"
)
_SINGLING_RE = _words(
    r"celle qui|celui qui|(?:le |la |l')seule?|(?:le|la) (?:nouveau|nouvel|nouvelle)"
    r"|(?:le|la) plus [^\W\d_]+|qui a ete embauchee?|qui remplace"
)
# « -tu » is the Québec interrogative particle (« Le cariste est-tu malade ? », M14).
_REC_STATUS_RE = re.compile(
    rf"{_L}(?:est|sont|etait|sera)(?:(?:-t)?-(?:il|elle|ils|elles)|-tu)? {_ADVERBS}"
    rf"(?:{_alt(_STATUS_WORDS)}){_R}"
)
# An inverted third person or « -tu » after a verb (review 2).
_INV = r"(?:-t-(?:il|elle)|-(?:il|elle)|-tu)"
_INV_PLURAL = r"(?:-t-(?:il|elle|ils|elles)|-(?:il|elle|ils|elles)|-tu)"
_REC_EVENT_RE = re.compile(
    rf"{_L}(?:a|ont|a-t-(?:il|elle)|ont-(?:ils|elles)|a-tu|ont-tu) {_ADVERBS}"
    rf"(?:ete {_ADVERBS})?(?:{_alt(_EVENT_PARTICIPLES)}){_R}"
    rf"|{_L}(?:a-t-(?:il|elle)|ont-(?:ils|elles)|a-tu) (?:des|un|une)"
    rf" (?:absences?|avertissements?|plaintes?|mesures? disciplinaires?){_R}"
    # Review 2, discipline: « se faire » + infinitive (« s'est fait suspendre »).
    rf"|{_L}s'est{_INV}? faite? (?:avertir|suspendre|renvoyer|congedier|licencier"
    rf"|mettre dehors|mettre a la porte|sacrer dehors|taper sur les doigts){_R}"
    # Review 2, health and absence: these still need a target to refuse (« Que faire si
    # un employé a manqué son quart ? » has none). « off » is left out: too ambiguous.
    rf"|{_L}(?:n')?(?<!ce n')(?<!c')(?:est|etait){_INV}? pas la{_R}"
    rf"|{_L}n'(?:est|etait)(?:-(?:il|elle))? pas (?:venue?|rentree?){_R}"
    rf"|{_L}(?:est|etait){_INV}? (?:partie?|tombee?) (?:en conge|en arret|malade){_R}"
    rf"|{_L}(?:est|etait){_INV}? sur (?:la cnesst|l'assurance-invalidite|l'assurance invalidite)"
    rf"{_R}"
    rf"|{_L}a{_INV}? {_ADVERBS}(?:calle malade|ete operee?|le cancer){_R}"
    # « a manqué » as an absence only (« a manqué combien de jours », « a manqué son
    # quart »), not « a manqué une étape de la procédure ».
    rf"|{_L}a{_INV}? {_ADVERBS}manque(?:\s*$| (?:combien|souvent|beaucoup|plusieurs|des jours"
    rf"|de jours|des journees|une journee|sa journee|son quart|des quarts|le travail"
    rf"|l'ouvrage|aujourd'hui|hier|ce matin|cette semaine|la semaine){_R})"
    rf"|{_L}s'est{_INV}? (?:portee? malade|fait mal|fait operer){_R}"
    rf"|{_L}fait{_INV}? une (?:depression|burn-?out){_R}"
    rf"|{_L}souffre{_INV}?{_R}"
    rf"|{_L}prend{_INV}? des (?:medicaments|antidepresseurs){_R}"
    rf"|{_L}attend{_INV}? un bebe{_R}"
)
_REC_PAY_RE = _words(
    r"combien gagne(?:nt)?|gagne(?:nt)? combien|gagne-t-(?:il|elle)|combien touche(?:nt)?"
    r"|combien (?:est|sont) payee?s?|quel(?:le)? salaire|quelle augmentation|revenu (?:de|du|d')"
    r"|salaire (?:annuel )?(?:de|du|d'|des)|taux horaire (?:de|du|d'|des)"
    r"|pa(?:i|y)e (?:de|du|d'|des)"
    # Review 2: spoken forms (« touche combien », « gagne-tu », « combien il gagne »,
    # « combien est-ce que le cariste gagne »), with a bounded gap.
    rf"|touche(?:nt)?{_INV_PLURAL}? combien|gagne(?:nt)?-tu"
    r"|combien (?:il|elle|ils|elles) (?:gagne(?:nt)?|touche(?:nt)?)"
    r"|combien est-ce qu(?:e |')[^?.!]{0,40}(?:gagne(?:nt)?|touche(?:nt)?)"
)
# « fait combien » and « est payé combien » ask for pay only with a pay tail or about a
# person, never about a uniform policy rate (« La prime de soir fait combien de l'heure ? »,
# « Les heures supplémentaires sont payées combien ? » has no target anyway). Review 2.
_PAY_TAIL = r"(?:de l'heure|a l'heure|par (?:annee|an|semaine|mois|heure)|de salaire|d'argent)"
_REC_PAY_LOOSE_RE = _words(
    rf"(?:fait|font)(?:-(?:il|elle|ils|elles)|-tu)? combien {_PAY_TAIL}"
    rf"|combien (?:fait|font) [^?.!]{{0,40}}{_PAY_TAIL}"
    r"|(?:est|sont)(?:-(?:il|elle|ils|elles)|-tu)? payee?s? combien"
)
_RATE_NOUN_RE = _words(r"primes?|taux|baremes?|indemnites?|allocations?")
_REC_CONTACT_RE = _words(
    # « je m'adresse », « s'adresse » are the verb, not an address (freeze review).
    r"telephone|cellulaire|cell|courriel|(?<![mst]')adresse|nas|matricule|date de naissance"
    r"|age (?:de|du|d')|habite|demeure"
)
_REC_RETURN_RE = _words(
    r"revient-(?:il|elle)|date de retour (?:de|du|d')|sera-t-(?:il|elle) de retour"
    # Review 2: interrogative forms only; a bare « revient » or « est de retour » would
    # refuse « Le travailleur qui revient d'un arrêt retrouve-t-il son poste ? ».
    rf"|revien(?:t|nent){_INV_PLURAL}|revien(?:t|nent) quand"
    r"|quand est-ce qu(?:e |')[^?.!]{0,40}revien(?:t|nent)"
    rf"|(?:est|sont|sera|seront){_INV_PLURAL} de retour|de retour quand"
    r"|quand est-ce qu(?:e |')[^?.!]{0,40}de retour"
    r"|(?:quand|date) (?:le |du )?retour (?:du|de la|de l'|de mon|de ma|de \[nom\]|d'\[nom\])"
    r"|c'est quand le retour"
)
_REC_POSSESSIVE_RE = re.compile(
    rf"{_L}(?:son|sa|ses|leur|leurs) (?:[^\W\d_]+ )?(?:{_alt(_RECORD_NOUNS)}){_R}"
)
# A request to know or check something (« savoir si », « connaître le salaire »): the
# asker is then not asking how to proceed in their own situation.
_DISCLOSE_RE = re.compile(
    rf"{_L}(?:savoir|connaitre|verifier|confirmer|apprendre|decouvrir|me dire|m'indiquer"
    rf"|me donner|obtenir|consulter|voir) "
    rf"(?:(?:si|pourquoi|quand|combien|qui|le|la|les|son|sa|ses|leur|leurs){_R}|s'|l')"
    rf"|{_L}(?:savoir|comprendre|connaitre) comment{_R}"
    rf"|{_L}me demande (?:si|comment|pourquoi|quand){_R}"
    rf"|{_L}(?:depuis quand|jusqu'a quand){_R}"
)
# An inverted third-person verb: the sentence asks about someone else.
_INVERTED_THIRD_RE = re.compile(
    # « c'est-tu » is an idiom (« c'est-tu normal ? »), not a question about someone.
    rf"{_L}(?<!c')(?:est|sont|etait|sera|a|ont|revient)-(?:(?:t-)?(?:il|elle|ils|elles)|tu){_R}"
)
# A non-inverted question about someone else (« Est-ce que le cariste est malade ? »);
# « est-ce que je » is the asker's own question.
_ESTCEQUE_THIRD_RE = re.compile(rf"{_L}est-ce qu(?:e |')(?!je{_R}|j')")
_LEADING_PRONOUN_RE = re.compile(r"^\s*(?:il|elle|ils|elles) ")
# A subject pronoun before its verb anywhere in the part (« Est-ce qu'elle est
# enceinte ? », « Pourquoi elle est absente ? »).
_PRONOUN_VERB_RE = re.compile(
    r"(?:^|\s|qu')(?:il|elle|ils|elles) (?:est|sont|etait|sera|a|ont)(?![\w-])"
)
_TARGETS = ("other.named", "other.role", "other.small", "other.singling", "other.pronoun")
# Words that designate one person without being a role (« l'homme du quai », « le gars de
# l'expédition », « mon voisin de poste »), and the third-person possessives.
_PERSON_WORDS = frozenset(
    _wordlist(
        "homme femme gars fille garcon dame monsieur madame voisin voisine ami amie chum "
        "blonde conjoint conjointe patron patronne boss"
    )
)
_THIRD_POSSESSIVES = frozenset({"son", "sa", "ses", "leur", "leurs"})
# The asker's own contact details (« mon adresse », « mon numéro de téléphone »).
_OWN_CONTACT_BEFORE_RE = re.compile(r"(?:^|\s)(?:mon|ma|mes)(?: [^\W\d_']+){0,2} $")


@dataclass(frozen=True)
class _Lexicon:
    """Role patterns built once per organisation."""

    other_role: re.Pattern[str]
    identify_person: re.Pattern[str]
    identify_generic: re.Pattern[str]
    aggregate: re.Pattern[str]
    rec_of_person: re.Pattern[str]
    explicit_count: re.Pattern[str]
    person_any: re.Pattern[str]
    indefinite: re.Pattern[str]
    status_list: re.Pattern[str]
    status_plural: re.Pattern[str]
    relative_qui: re.Pattern[str]


@functools.lru_cache(maxsize=8)
def _lexicon(roles: OrgRoles) -> _Lexicon:
    aliases = [a for r in roles.roles for a in r.aliases]
    singular = _alt([*aliases, *_GENERIC_PERSON_SINGULAR])
    person = _alt([*aliases, *_GENERIC_PERSON_SINGULAR, *_GENERIC_PERSON_PLURAL])
    generic = _alt(_GENERIC_PERSON_SINGULAR)
    dets = _alt(_SINGULAR_DETERMINERS)
    adjectives = _alt(_ROLE_ADJECTIVES)
    possessives = _alt(_PERSON_DETERMINERS)
    numbers = _alt(_SMALL_NUMBERS)
    status = _alt(_STATUS_WORDS)
    # « Quels sont les employés... » reads like « Quels employés... » (review 2).
    which = r"quel(?:le)?s (?:(?:sont|etaient) (?:les|nos) )?"
    person_ref = (
        f"(?:un des |une des )?(?:(?:{possessives}))?"
        f"(?:(?:{adjectives}) )?(?:{person}|{_PERSON_PLACEHOLDERS}){_R}"
    )
    return _Lexicon(
        other_role=re.compile(f"{_L}(?:{dets})(?:(?:{adjectives}) )?(?:{singular}){_R}"),
        identify_person=re.compile(f"{_L}(?:{which}|quel(?:le)? )(?:{person}){_R}"),
        # « Quels employés », « Quelles soudeuses », « Quel employé »: a plural or a
        # generic person noun, as in a policy eligibility question (M13).
        identify_generic=re.compile(f"{_L}(?:{which}(?:{person})|quel(?:le)? (?:{generic})){_R}"),
        aggregate=re.compile(
            f"{_L}(?:combien (?:de |d')|liste des |noms des |{which})(?:{person}){_R}"
        ),
        # « le salaire de mon collègue », « le salaire d'un des mécaniciens », and the
        # spoken « à » possessive after a determiner (« le salaire à [nom] », « l'horaire
        # au cariste »), review 2; « est payé au travailleur » has no determiner.
        rec_of_person=re.compile(
            f"{_L}(?:{_alt(_RECORD_NOUNS)}) (?:de |d'|du |des ){person_ref}"
            f"|{_L}(?:le |la |l'|les )(?:{_alt(_RECORD_NOUNS)}) (?:a |au ){person_ref}"
        ),
        explicit_count=re.compile(rf"{_L}(?:les|ces|nos|aux|des) (\d+|{numbers}) (?:{person}){_R}"),
        person_any=re.compile(f"{_L}(?:{person}){_R}"),
        indefinite=re.compile(
            f"{_L}(?:(?:un|une|tout|toute|chaque|aucun|aucune) (?:{person})|quelqu'un){_R}"
        ),
        # « la liste des employés absents », « les noms des employés en congé » (M14,
        # review 2), « Y a qui en congé de maladie ? »: a status after the listed people.
        status_list=re.compile(
            f"{_L}(?:liste|noms?) des (?:{person}) (?:{status}){_R}"
            f"|{_L}(?:y a|y'a|il y a) qui (?:{status}){_R}"
        ),
        # « les employés suspendus »: a record only in an identification or count
        # request; « Les employés suspendus sont-ils payés ? » stays a policy question.
        status_plural=re.compile(
            f"{_L}(?:les|des|nos) (?:{person}) (?:{status}|{_alt(_EVENT_PARTICIPLES)}){_R}"
        ),
        # A relative clause after one person (« J'ai un collègue, qui est malade. »): not
        # an identification after a lead-in (review 2).
        relative_qui=re.compile(
            rf"{_L}(?:(?:un|une|le|la|ce|cet|cette|mon|ma|notre|votre) |l')"
            rf"(?:(?:{adjectives}) )?(?:{singular}|{_alt(sorted(_PERSON_WORDS))}|\[nom\])"
            r"(?: [^\W\d_]+)?, qui "
        ),
    )


# --------------------------------------------------------------------------- helpers
def _owned_placeholders(text: str) -> list[tuple[int, bool]]:
    """(offset, owned_by_asker) of every placeholder of a normalised text.

    A non-name placeholder is the asker's when a noun of its type introduced by mon/ma/mes
    occurs among the 6 tokens before it (« mon numéro de téléphone est le [telephone] »),
    or when an owned placeholder occurs among the 3 tokens before it (« ma nouvelle
    adresse : [adresse], [code_postal] »). A possessive alone does not make it the asker's
    (« le salaire de mon [matricule] »). A [nom] is the asker's only in « je m'appelle
    [nom] », « mon nom est [nom] » or « je suis [nom] ».
    """
    self_names = {m.end() - len("[nom]") for m in _SELF_NAME_RE.finditer(text)}
    tokens = list(_TOKEN_RE.finditer(text))
    words = [t.group(0) for t in tokens]
    found: list[tuple[int, bool]] = []
    owned_tokens: set[int] = set()
    for i, token in enumerate(tokens):
        word = words[i]
        if not word.startswith("["):
            continue
        if word == "[nom]":
            found.append((token.start(), token.start() in self_names))
            continue
        chained = any(j in owned_tokens for j in range(max(0, i - _OWNER_WINDOW), i))
        nouns = _TYPE_NOUNS.get(word, frozenset())
        typed = any(
            words[j] in nouns and any(w in _OWNER_WORDS for w in words[max(0, j - 2) : j])
            for j in range(max(0, i - _TYPE_NOUN_WINDOW), i)
        )
        owned = chained or typed
        if owned:
            owned_tokens.add(i)
        found.append((token.start(), owned))
    return found


def _parts(text: str) -> list[tuple[int, str]]:
    """Sentence parts of a normalised text, with their start offsets."""
    parts: list[tuple[int, str]] = []
    start = 0
    for match in _PART_SPLIT_RE.finditer(text):
        parts.append((start, text[start : match.start()]))
        start = match.end()
    parts.append((start, text[start:]))
    return [(s, p) for s, p in parts if p.strip()]


def _other_small(part: str, roles: OrgRoles, k: int, lex: _Lexicon) -> bool:
    """A role (by shift) mention with fewer than k people, or an explicit count of k or less."""
    if any(m.headcount < k for m in roles.mentions(part)):
        return True
    for match in lex.explicit_count.finditer(part):
        raw = match.group(1)
        count = int(raw) if raw.isdigit() else _SMALL_NUMBERS[raw]
        if count <= k:
            return True
    return False


# An indefinite person that opens a conditional clause (« si une », « quand un »,
# « lorsqu'une »), read on the 20 characters before the person noun.
_HYPOTHETICAL_RE = re.compile(r"(?:^|\s)(?:si|quand|lorsque?) (?:un|une) $|lorsqu'(?:un|une) $")


def _singling(part: str, lex: _Lexicon) -> bool:
    """« celle qui », « le seul »..., or a person noun with a demographic qualifier nearby."""
    if _SINGLING_RE.search(part):
        return True
    tokens = list(_TOKEN_RE.finditer(part))
    starts = [t.start() for t in tokens]
    ends = [t.end() for t in tokens]
    for person in lex.person_any.finditer(part):
        if _HYPOTHETICAL_RE.search(part[max(0, person.start() - 20) : person.start()]):
            # « Quand une employée est enceinte, a-t-elle droit... »: a hypothesis about
            # anyone, not one person (review 2).
            continue
        # Tokens are ordered and never overlap: bisect keeps a long question linear.
        before = tokens[: bisect.bisect_right(ends, person.start())][-_OWNER_WINDOW:]
        first_after = bisect.bisect_left(starts, person.end())
        after = tokens[first_after : first_after + _OWNER_WINDOW]
        lo = before[0].start() if before else person.start()
        hi = after[-1].end() if after else person.end()
        window = part[lo:hi]
        if _DEMOGRAPHIC_RE.search(window):
            return True
    return False


def _record_ids(part: str, lex: _Lexicon, *, listing: bool) -> list[str]:
    """Record rule ids of a part; ``listing`` tells whether the part asks to identify or
    count people, where a plural status (« les employés suspendus ») is a record."""
    checks = (
        ("rec.possessive", _REC_POSSESSIVE_RE),
        ("rec.of_person", lex.rec_of_person),
        ("rec.status", _REC_STATUS_RE),
        ("rec.event", _REC_EVENT_RE),
        ("rec.pay", _REC_PAY_RE),
        ("rec.contact", _REC_CONTACT_RE),
        ("rec.return", _REC_RETURN_RE),
    )
    found = [rule for rule, pattern in checks if pattern.search(part)]
    if "rec.pay" not in found and _REC_PAY_LOOSE_RE.search(part) and not _RATE_NOUN_RE.search(part):
        found.append("rec.pay")
    if "rec.status" not in found and (
        lex.status_list.search(part) or (listing and lex.status_plural.search(part))
    ):
        found.append("rec.status")
    return found


def _is_english(text: str) -> bool:
    tokens = _TOKEN_RE.findall(text)
    english = sum(t in _ENGLISH_STOPWORDS for t in tokens) + len(_EN_RECORD_RE.findall(text))
    french = sum(t in _FRENCH_STOPWORDS for t in tokens)
    # A short English question has a single English word and no French one.
    return (english >= 2 and english > french) or (english >= 1 and french == 0)


def _prepare(redacted: str) -> str:
    """Normalised text, where « où » first becomes the marker ``ou-lieu``: accent removal
    would make it the conjunction « ou »."""
    return normalize(_OU_RE.sub(_OU_MARK, unicodedata.normalize("NFKC", redacted)))


@dataclass
class _Signals:
    """Signals of one sentence part (rule ids in emission order)."""

    ids: list[str]

    def __contains__(self, rule: str) -> bool:
        return rule in self.ids

    def any(self, *rules: str) -> bool:
        return any(r in self.ids for r in rules)

    @property
    def record(self) -> bool:
        return any(r.startswith("rec.") for r in self.ids)


def _pronoun_subject(part: str) -> bool:
    """True when the subject of a third-person question is a pronoun or a bare person.

    « Est-elle... », « Pourquoi est-il... », « Est-ce qu'elle est... », « Pourquoi elle
    est... », « Bérubé, est-elle... » (left dislocation), « L'homme du quai est-il... »,
    « Son dossier est-il... » and a lowercase given name (« isabelle lavoie est-elle... »)
    qualify; an
    institution or a thing (« Le service de la paie a-t-il reçu... », « Pourquoi ma paie
    est-elle... ») does not.
    """
    if _LEADING_PRONOUN_RE.match(part) or _PRONOUN_VERB_RE.search(part):
        return True
    verb = _INVERTED_THIRD_RE.search(part)
    if verb is None:
        return False
    if part[: verb.start()].rstrip().endswith(","):
        # A left dislocation (« Bérubé, est-elle en congé ? »): the subject is the pronoun.
        return True
    before = _TOKEN_RE.findall(part[: verb.start()])
    content = [w for w in before if w not in FUNCTION_WORDS]
    if not content or any(w in _THIRD_POSSESSIVES for w in before):
        return True
    names = given_names()
    return any(w in _PERSON_WORDS or w in names for w in content)


def _others_contact(part: str) -> bool:
    """A contact detail in ``part`` that is not introduced by mon/ma/mes."""
    return any(
        not _OWN_CONTACT_BEFORE_RE.search(part[max(0, m.start() - 30) : m.start()])
        for m in _REC_CONTACT_RE.finditer(part)
    )


def _part_signals(
    part: str,
    offset: int,
    placeholders: Sequence[tuple[int, bool]],
    ctx: PrivacyContext,
    lex: _Lexicon,
    *,
    prior_target: bool,
    indefinite: bool,
) -> _Signals:
    """Signals of one part; ``prior_target`` tells whether an earlier part names a person
    and ``indefinite`` whether the question mentions an indefinite person."""
    ids: list[str] = []
    end = offset + len(part)
    # The relative clause is masked before the role phrases, which would erase
    # « personne désignée » in « J'ai parlé à la personne désignée, qui a reçu... ».
    asks_identity = lex.relative_qui.sub(lambda m: m.group(0)[: -len(", qui ")] + " ", part)
    asks_identity = _PROCEDURAL_ROLE_RE.sub(" ", asks_identity)
    identify = bool(_IDENTIFY_RE.search(asks_identity) or lex.identify_person.search(asks_identity))
    aggregate = lex.aggregate.search(part) is not None
    record = _record_ids(part, lex, listing=identify or aggregate)
    if _SELF_RE.search(part) or any(owned and offset <= at < end for at, owned in placeholders):
        ids.append("self")
    if _SELF_INVOLVED_RE.search(part):
        ids.append("self.involved")
    if any(not owned and offset <= at < end for at, owned in placeholders):
        ids.append("other.named")
    if lex.other_role.search(part):
        ids.append("other.role")
    if _other_small(part, ctx.roles, ctx.k, lex):
        ids.append("other.small")
    if _singling(part, lex):
        ids.append("other.singling")
    if (
        record
        and not any(t in ids for t in _TARGETS)
        and _pronoun_subject(part)
        and (prior_target or not indefinite)
    ):
        # « Est-elle en congé de maladie ? »: a record about someone only a pronoun names.
        ids.append("other.pronoun")
    if identify:
        ids.append("identify")
    if aggregate:
        ids.append("aggregate")
    ids.extend(record)
    if _ENTITLEMENT_RE.search(part):
        ids.append("entitlement")
    if _PROCEDURAL_RE.search(part) or _CONTACT_ROLE_RE.search(part):
        ids.append("procedural")
    if _PROCEDURAL_ROLE_RE.search(part):
        ids.append("procedural.role")
    if _DISCLOSE_RE.search(part):
        ids.append("disclose")
    if _OWN_RE.search(part):
        ids.append("own")
    if _RESTRICTED_RE.search(part):
        ids.append("restricted")
    if _LEGAL_RE.search(part):
        ids.append("legal")
    return _Signals(ids)


@dataclass(frozen=True)
class _Whole:
    """Signals read on the whole question, for the case (c) exemptions."""

    procedural: bool
    involved: bool
    identify: bool
    named: bool
    disclose: bool
    # Some part asks for someone's contact details, pay or return date.
    asks_about: bool


def _eligibility(s: _Signals, part: str, lex: _Lexicon) -> bool:
    """A policy eligibility question (M13): « Quels employés ont droit au télétravail ? ».

    Every identification is « quel(le)s » + a plural person noun or « quel(le) » + a
    generic one, the part asks about an entitlement, and no record attribute, named
    person, small group, singling or pronoun target appears.
    """
    if "entitlement" not in s or s.record:
        return False
    if s.any("other.named", "other.small", "other.singling", "other.pronoun"):
        return False
    if _IDENTIFY_RE.search(part):
        return False
    found = list(lex.identify_person.finditer(part))
    return bool(found) and all(
        lex.identify_generic.fullmatch(part, m.start(), m.end()) for m in found
    )


def _person_targeted(s: _Signals, lex: _Lexicon, part: str, *, whole: _Whole) -> str | None:
    """The §5.5 case (a to d) that makes a part target a person, if any.

    ``whole`` holds the signals read on the whole question: whether it asks how to
    proceed, whether the asker is involved in any part, and whether any part asks to
    identify, names someone or asks to know something about a person.
    """
    if (
        "identify" in s
        # A role phrase is no person (« La personne désignée, c'est qui ? », review 2).
        and (
            s.record
            or lex.person_any.search(_PROCEDURAL_ROLE_RE.sub(" ", part))
            or "other.named" in s
        )
        and not _eligibility(s, part, lex)
    ):
        return "person_targeted.a"
    if "aggregate" in s and s.record:
        return "person_targeted.b"
    # The (c) exemption: the asker asks how to proceed, without asking to know something
    # about the person, their pay, contact details or return, and without an inverted or
    # « est-ce que » third-person question. The asker is involved in this very part, or (M13: « Mon
    # collègue s'est blessé hier. Que dois-je faire ? ») in another one, when no part
    # asks to identify someone, names someone, asks for their contact details, pay or
    # return, or asks to know something about them.
    involved = (
        "self.involved" in s
        or _CONTACT_ROLE_RE.search(part) is not None
        or (
            whole.involved
            and not (whole.identify or whole.named or whole.disclose or whole.asks_about)
        )
    )
    exempt = (
        involved
        and whole.procedural
        and "disclose" not in s
        and not s.any("rec.pay", "rec.return")
        and not ("rec.contact" in s and _others_contact(part))
        and _INVERTED_THIRD_RE.search(part) is None
        and _ESTCEQUE_THIRD_RE.search(part) is None
    )
    if s.any(*_TARGETS) and s.record and not exempt:
        return "person_targeted.c"
    if "other.named" in s and "entitlement" in s and "self.involved" not in s:
        return "person_targeted.d"
    return None


def _subject(ids: set[str]) -> Subject:
    if "other.small" in ids:
        return "small_group"
    if ids & {"other.named", "other.role", "other.singling", "other.pronoun", "identify"}:
        return "other"
    if "aggregate" in ids:
        return "group"
    if "self" in ids:
        return "self"
    return "none"


def _result(
    kind: Kind,
    subject: Subject,
    level: int,
    refusal: ScreenCode | None,
    ids: Iterable[str],
) -> Assessment:
    return Assessment(
        kind=kind,
        subject=subject,
        requested_level=level,
        refusal=refusal,
        rule_ids=tuple(dict.fromkeys(ids)),
    )


_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Zs", "Zl", "Zp"})


def _visible(text: str) -> bool:
    """True when ``text`` has a visible character: a zero-width space or a BOM alone is
    an empty question (G6, fail closed)."""
    return any(unicodedata.category(c) not in _INVISIBLE_CATEGORIES for c in text)


def _known_word(ctx: PrivacyContext, word: str) -> bool:
    """A normalised word that cannot be a password: corpus, function word, allowlist."""
    return word in ctx.vocabulary or word in FUNCTION_WORDS or word in ctx.allowlist


def _assess(redacted: str, ctx: PrivacyContext, *, raw: str) -> Assessment:
    """The §5.5 decision order; ``raw`` is the text the injection screen reads."""
    if not _visible(raw) or not _visible(redacted):
        return _result("empty", "none", 1, "empty_question", ["empty"])
    if is_injection(raw, credential_help_ok=True):
        return _result("injection", "none", 1, "prompt_injection", ["injection"])
    if credential_value(raw, functools.partial(_known_word, ctx)):
        # A password help request that seems to carry the password itself: the value is
        # no PI type, so redaction would leave it in the backend text (M13 exemption).
        return _result("injection", "none", 1, "prompt_injection", ["injection.secret"])
    text = _prepare(redacted)
    if _is_english(text):
        return _result("foreign", "none", 1, "unsupported_language", ["lang.en"])
    lex = _lexicon(ctx.roles)
    placeholders = _owned_placeholders(text)
    indefinite = lex.indefinite.search(text) is not None
    parts: list[tuple[str, _Signals]] = []
    prior_target = False
    for start, part in _parts(text):
        signals = _part_signals(
            part, start, placeholders, ctx, lex, prior_target=prior_target, indefinite=indefinite
        )
        prior_target = prior_target or signals.any(*_TARGETS)
        parts.append((part, signals))
    ids = [rule for _, s in parts for rule in s.ids]
    all_ids = set(ids)
    subject = _subject(all_ids)
    sensitive = _SENSITIVE_RE.search(text) is not None
    record_level = 5 if sensitive else 4
    procedural = "procedural" in all_ids
    whole = _Whole(
        procedural=procedural,
        involved="self.involved" in all_ids or _CONTACT_ROLE_RE.search(text) is not None,
        identify="identify" in all_ids,
        named="other.named" in all_ids,
        disclose="disclose" in all_ids,
        asks_about=any(
            s.any("rec.pay", "rec.return") or ("rec.contact" in s and _others_contact(p))
            for p, s in parts
        ),
    )
    for part, signals in parts:
        case = _person_targeted(signals, lex, part, whole=whole)
        if case is not None:
            kind: Kind = "aggregate" if case.endswith(".b") else "record"
            return _result(kind, subject, record_level, "person_targeted", [*ids, case])
    for _, signals in parts:
        if "self" in signals and "own" in signals and not procedural:
            return _result("record", "self", record_level, "own_record", [*ids, "own_record"])
    if "restricted" in all_ids:
        return _result("restricted", subject, 3, "restricted_info", [*ids, "restricted_info"])
    if "legal" in all_ids:
        return _result("legal", subject, 2, "legal_advice", [*ids, "legal_advice"])
    return _result("policy", subject, 2, None, [*ids, "allowed"])


def assess(redacted: str, spans: Sequence[PISpan], ctx: PrivacyContext) -> Assessment:
    """Classify an already redacted question (§5.4 signals, §5.5 decision order).

    ``spans`` are the redaction spans, kept in the signature for callers that hold them;
    the rules only read the placeholders they left in ``redacted``.
    """
    del spans
    return _assess(redacted, ctx, raw=redacted)


def screen(question: str, ctx: PrivacyContext) -> Screening:
    """Detect, redact and classify a raw question; pure, no I/O.

    The question is cleaned first (``clean_text``: NFKC, zero-width and control
    characters removed, look-alike letters folded; runs of spaces or tabs collapsed to one
    space, line breaks kept, so a pasted « 819  555  0142 » is still a phone number), and
    every rule, the redaction and the raw values read the cleaned text. The injection
    screen reads it unredacted (module-1 patterns, with the HR relaxation of M1): a
    capitalised « Instructions » would otherwise become [NOM]. The raw span values stay in
    memory only, out of ``repr`` and equality, for the output guard's echo check.
    """
    question = _HSPACES_RE.sub(" ", clean_text(question))
    spans = detect_pi(question, ctx, mode="question")
    redaction = redact(question, spans)
    return Screening(
        redacted=redaction.text,
        spans=redaction.spans,
        assessment=_assess(redaction.text, ctx, raw=question),
        raw_values=tuple(question[s.start : s.end] for s in redaction.spans),
    )
