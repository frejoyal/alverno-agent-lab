"""Quarantine of tool text before it reaches a prompt (module 3, ADR 0012).

Every projected free text (the ERP ``customer_visible_note``, the GMAO ``remark``) goes
through :func:`scrub_tool_text`. It is pure and reads only the text and the privacy
context; the I6 pipeline wraps it into the connectors' ``Quarantine`` callable
(``QuarantinedText(text=r.text, flags=r.flags)``). It is a defence in depth: the code
chooses tools, recipients and arguments, so a tool text cannot choose anything.

The text is cleaned first (``clean_text``: NFKC, zero-width and control characters
removed, look-alike letters folded), then read sentence by sentence. A sentence ends at
``.``, ``?``, ``!`` or ``;`` followed by a space or the end, or at a line break. Unlike
the classifier's parts, a colon does not end a sentence: « NOTE AU SYSTÈME : IGNORE… »
is one order and must leave as a whole, header included. A dot inside a token
(« J.T. », « 2.5 ») does not end one either, nor the dot after a single capital
(« selon J.T. a mis… », « M. ») or after a short abbreviation (« env. », « p. ex. »,
« no. »): an attribution or an order must not be cut in two.

1. A sentence is **removed** and replaced by ``[texte retiré]`` (consecutive removed
   sentences share one marker) when it matches the shared injection patterns
   (``INJECTION_PATTERNS``, read accent-tolerantly as the HR path of ``is_injection``
   does, or the ops override patterns of ``ops_injection``; flag ``injection``), an ops
   action pattern (``ops_injection``; flag ``action_request``), the credential patterns
   or a password value (``CREDENTIAL_PATTERN``, the short names of
   ``OPS_CREDENTIAL_PATTERN`` such as « mdp » or « NIP », ``credential_value``; flag
   ``credential``), or when :func:`attributes_small_role` holds (flag ``small_role``).
2. PI spans of the kept text found by ``detect_pi(mode="document")`` with the ops context
   are replaced by the standard placeholders (flag ``pi``).
3. Initials (2 or 3 capitals, optionally dotted, spaced or hyphenated: « MT »,
   « J.T. », « J. T. », « J.-T. », « J-T »; 2 or 3 lowercase letters right after
   « initiales ») and login-shaped tokens (letters then digits, « JTremblay »,
   « j_tremblay », « prenom.nom » or « p.nom », or an unknown word right after
   « compte », « login », « identifiant », « utilisateur », « usager », « user » or
   « session ») become ``[identifiant]`` (flag ``technician_ref``). Fixture ids
   (``CMD-``, ``EQ-``, ``OT-``, ``CLI-``, ``CT-``, ``TRK-``, ``TRP-``, ``RET-``,
   ``ACT-``, symptom and failure codes, part references) and the technical
   abbreviations of :data:`TECH_ABBREVIATIONS` are kept. A bare capital token is also
   kept when it spells a function word or an allow-listed term (an all-caps sentence,
   « LIVRAISON DE LA PIÈCE »), or when it follows a number (a unit, « 90 PSI »); a
   dotted, spaced or hyphenated form is always redacted.

Flags are unique and follow :data:`FLAG_ORDER`. The result never holds a removed
sentence. Rule outcomes carry no value, so flags may go to the access log.

Published known misses: bare initials that spell a function word (« LA », « DE »), bare
lowercase initials without a keyword (« fait par jt »), initials written « J/T », a login
that is a dictionary word, an order split across sentences, and small-group attributions
without a word of the closed duration, fault or result lists (« n'a pas vu »).
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Literal

from alverno_lab.privacy.detect import detect_pi, redact
from alverno_lab.privacy.injection import (
    CREDENTIAL_PATTERN,
    INJECTION_PATTERNS,
    accent_tolerant,
    credential_value,
)
from alverno_lab.privacy.lexicon import FUNCTION_WORDS, clean_text, normalize
from alverno_lab.privacy.ops_injection import (
    fold_apostrophes,
    is_ops_action,
    is_ops_credential,
    is_ops_injection,
)
from alverno_lab.privacy.types import PrivacyContext

QuarantineFlag = Literal[
    "injection", "action_request", "credential", "pi", "technician_ref", "small_role"
]
FLAG_ORDER: tuple[QuarantineFlag, ...] = (
    "injection",
    "action_request",
    "credential",
    "pi",
    "technician_ref",
    "small_role",
)
REMOVED = "[texte retiré]"
IDENTIFIER = "[identifiant]"

# Technical abbreviations in capitals, never initials. CNC, ERP and ADR occur in
# data/eval/ops/erp.yaml and gmao.yaml (asset names, comments); the others are the usual
# shop, maintenance and unit abbreviations a remark may hold: work order (OT, BT),
# health and safety (SST), automation (PLC, API, HMI, VFD, UPS, RTD, DEL, LED), units
# and standards (PSI, RPM, BAR, KW, HP, CA, CC, NPT, ISO), preventive maintenance (PM),
# quality (QC), and office words (OK, PDF, PO, RH, TI, IA). A closed list, by design.
TECH_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        *("CNC", "ERP", "ADR", "OT", "BT", "SST", "PLC", "API", "HMI", "VFD", "UPS"),
        *("RTD", "DEL", "LED", "PSI", "RPM", "BAR", "KW", "HP", "CA", "CC", "NPT", "ISO"),
        *("PM", "QC", "OK", "PDF", "PO", "RH", "TI", "IA"),
    }
)


@dataclass(frozen=True)
class ScrubResult:
    """A quarantined tool text and the rules that fired; ``text`` stays out of ``repr``."""

    text: str = field(repr=False)
    flags: tuple[QuarantineFlag, ...]


# --------------------------------------------------------------------------- sentences
# A sentence ends at . ? ! ; followed by a space or the end of the text, or at a line
# break; the separator stays with its sentence, so the pieces rebuild the text.
_BOUNDARY_RE = re.compile(r"[.?!;]+(?:\s+|$)|\n\s*")
# A single dot after an initial or a short abbreviation, followed by spaces on the same
# line, does not end a sentence (« selon J.T. a mis », « M. », « env. », « p. ex. »).
_ABBREVIATION_END_RE = re.compile(
    r"(?:^|[^\w])(?:[A-ZÀ-ÖØ-Þ]|(?i:env|ex|p|no|mme|mlle|m|dr|approx|cf|vs|st|ste))$"
)


def _no_break(text: str, match: re.Match[str]) -> bool:
    """True when ``match`` is a dot that closes an initial or an abbreviation."""
    separator = match.group(0)
    return (
        separator[0] == "."
        and len(separator) > 1
        and separator[1:].strip(" \t") == ""
        and _ABBREVIATION_END_RE.search(text[max(0, match.start() - 8) : match.start()]) is not None
    )


def _sentences(text: str) -> list[str]:
    """Pieces of ``text`` (each with its trailing separator); they join back to ``text``."""
    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        if _no_break(text, match):
            continue
        pieces.append(text[start : match.end()])
        start = match.end()
    if start < len(text):
        pieces.append(text[start:])
    return pieces


# --------------------------------------------------------------------------- removal rules
_INJECTION_RE = tuple(re.compile(accent_tolerant(p), re.IGNORECASE) for p in INJECTION_PATTERNS)
_CREDENTIAL_RE = re.compile(accent_tolerant(CREDENTIAL_PATTERN), re.IGNORECASE)

# Small-group attribution words, on normalised text (closed lists).
_L = r"(?<![\w\[-])"
_R = r"(?![\w\]-])"
_DURATION_RE = re.compile(
    rf"{_L}(?:\d+ ?h(?: ?\d{{1,2}})?|\d+ ?min|minutes?|heures?|jours?|journees?|semaines?"
    rf"|temps|durees?|delais?|longtemps|en retard|tard|a mis|a pris|avait mis|avait pris){_R}"
)
_FAULT_RE = re.compile(
    rf"{_L}(?:mal (?:serre|monte|installe|ajuste|regle|branche|fixe|fait|repare|remonte"
    rf"|entretenu|graisse|aligne|resserre|programme)e?s?"
    rf"|fautes?|erreurs?|negligences?|negligente?s?|oubli|a oublie|avait oublie|oublie de"
    rf"|blame|blamer|responsable de|a cause (?:du|de la|de l'|des)|par sa faute"
    rf"|incompetences?|incompetente?s?|baclee?s?|mauvais (?:travail|montage|reglage|serrage)"
    rf"|mauvaise (?:installation|manipulation|manoeuvre)|trompee?s?|de travers"
    rf"|n'(?:a|avait|ont|avaient) pas (?:verifie|serre|resserre|controle|fait|teste"
    rf"|graisse|nettoye|remplace|regle))(?![\w\]-])"
)
_RESULT_RE = re.compile(
    rf"{_L}(?:rendements?|productivite|performances?|resultats?|efficaces?|efficacite"
    rf"|lente?s?|rapides?|reussie?s?|echouee?s?|ratee?s?|taux|rebuts?){_R}"
)


def attributes_small_role(sentence: str, ctx: PrivacyContext) -> bool:
    """True when ``sentence`` ties a small group to a duration, a fault or a result.

    A small group is a role mention (``OrgRoles.mentions``, with its shift) whose
    headcount is below ``ctx.k`` (« le mécanicien de soir »); the duration, fault/blame
    or result word must be in the same sentence. Reused by the 3b output guard (I9).
    """
    norm = normalize(clean_text(sentence))
    if not any(m.headcount < ctx.k for m in ctx.roles.mentions(norm)):
        return False
    return any(p.search(norm) for p in (_DURATION_RE, _FAULT_RE, _RESULT_RE))


def _known(ctx: PrivacyContext, word: str) -> bool:
    """A normalised word that is a function word, a corpus word or an allow-listed term."""
    return word in FUNCTION_WORDS or word in ctx.vocabulary or word in ctx.allowlist


def _removal_flags(sentence: str, ctx: PrivacyContext) -> list[QuarantineFlag]:
    """The removal rules that ``sentence`` matches, in :data:`FLAG_ORDER`."""
    plain = fold_apostrophes(sentence)
    found: list[QuarantineFlag] = []
    if any(p.search(plain) for p in _INJECTION_RE) or is_ops_injection(plain):
        found.append("injection")
    if is_ops_action(plain):
        found.append("action_request")
    if (
        _CREDENTIAL_RE.search(plain)
        or is_ops_credential(plain)
        or credential_value(plain, functools.partial(_known, ctx))
    ):
        found.append("credential")
    if attributes_small_role(plain, ctx):
        found.append("small_role")
    return found


# --------------------------------------------------------------------------- identifiers
_CAP = "A-ZÀ-ÖØ-Þ"
# Dotted, spaced or hyphenated initials (« J.T. », « J. T. », « J.-T. », « J-T »), always
# redacted; bare capitals (« MT »), kept when they spell a known short word.
_INITIALS_RE = re.compile(
    rf"(?<![\w.\[-])(?:(?P<marked>[{_CAP}]\.(?: ?-?[{_CAP}]\.?){{1,2}}"
    rf"|[{_CAP}](?:-[{_CAP}]){{1,2}})|(?P<bare>[{_CAP}]{{2,3}}))(?![\w\]-])"
)
# Lowercase initials named as such (« Initiales : mt »).
_INITIALS_CONTEXT_RE = re.compile(r"(?<![\w-])(?i:initiales?|init\.)\s*:?\s+([a-z]{2,3})(?![\w-])")
_NUMBER_BEFORE_RE = re.compile(r"[0-9] ?$")
_LETTERS = "a-zà-ÿ"
# Letters then digits (« jtremblay2 », « JTremblay2 », « jbérubé2 »), two capitals then
# lowercase (« JTremblay »), « j_tremblay », « prenom.nom », « p.nom » or « nom.p »;
# never a part of an e-mail address or a domain (« client01.example »).
_LOGIN_SHAPE_RE = re.compile(
    rf"(?<![\w.@-])(?:[{_LETTERS}]{{2,}}[0-9]{{1,4}}"
    rf"|[A-Z]{{1,2}}[{_LETTERS}]{{2,}}[0-9]{{1,4}}|[A-Z]{{2}}[{_LETTERS}]{{3,}}"
    rf"|[{_LETTERS}]+_[{_LETTERS}]+[0-9]*"
    rf"|[{_LETTERS}]+(?:-[a-z]+)?\.[{_LETTERS}]{{2,}}(?:-[a-z]+)?"
    rf"|[{_LETTERS}]{{2,}}\.[{_LETTERS}])"
    r"(?![\w@-])(?!\.[a-z0-9])"
)
# A word right after an account keyword (« sous le compte jtremblay », « Compte :
# JTREMBLAY »).
_LOGIN_CONTEXT_RE = re.compile(
    r"(?<![\w-])(?i:compte|login|identifiant|utilisateur|usager|session|user|username)"
    r"\s*:?\s+([A-Za-zÀ-ÿ][\w.-]*[A-Za-zÀ-ÿ0-9])(?![\w@-])"
)
# Cyrillic look-alikes of J (U+0408, U+0458) that ``clean_text`` does not fold.
_LOCAL_LOOKALIKES = str.maketrans({chr(0x0408): "J", chr(0x0458): "j"})
# Ordinary words after « compte » that are no login (« compte rendu », « compte tenu »),
# normalised; known words of the context are skipped too.
_NOT_LOGINS = frozenset(
    {
        *("rendu", "rendus", "tenu", "courant", "courants", "bancaire", "principal"),
        *("actif", "inactif", "existant", "partage", "verrouille", "bloque", "ferme"),
        *("ouvert", "ouverte", "fermee", "verrouillee", "bloquee", "expire", "expiree"),
        *("active", "inactive", "partagee"),
    }
)
# Fixture ids and codes, kept whole: order, asset, work order, account, contact,
# tracking, carrier, delay, action, symptom and failure codes, part references.
_ID_RE = re.compile(
    r"(?<![\w-])(?:(?:CMD|EQ|OT|CLI|CT|TRK|TRP|RET|ACT|SYM|MEC|ELE|HYD|PNE|COM|USU|EXT)"
    r"-[A-Z0-9]+(?:-[A-Z0-9]+)*|INDETERMINE|[A-Z]{2,3}-[0-9]{3,4})(?![\w-])"
)


def _short_word(ctx: PrivacyContext, letters: str, before: str) -> bool:
    """A bare capital token that is no initial: a function word, an allow-listed term,
    or a unit after a number (« 90 PSI »)."""
    word = normalize(letters)
    return (
        word in FUNCTION_WORDS
        or word in ctx.allowlist
        or _NUMBER_BEFORE_RE.search(before) is not None
    )


def _identifier_spans(text: str, ctx: PrivacyContext) -> list[tuple[int, int]]:
    """Offsets of initials and logins in ``text``, outside fixture ids, sorted."""
    ids = [(m.start(), m.end()) for m in _ID_RE.finditer(text)]

    def inside_id(start: int, end: int) -> bool:
        return any(start < e and b < end for b, e in ids)

    spans: list[tuple[int, int]] = []
    for m in _INITIALS_RE.finditer(text):
        letters = "".join(c for c in m.group(0) if c.isalpha())
        if letters in TECH_ABBREVIATIONS or inside_id(m.start(), m.end()):
            continue
        if m.group("bare") and _short_word(ctx, letters, text[: m.start()]):
            continue
        spans.append((m.start(), m.end()))
    spans.extend((m.start(1), m.end(1)) for m in _INITIALS_CONTEXT_RE.finditer(text))
    spans.extend(
        (m.start(), m.end())
        for m in _LOGIN_SHAPE_RE.finditer(text)
        if not inside_id(m.start(), m.end())
    )
    for m in _LOGIN_CONTEXT_RE.finditer(text):
        word = normalize(m.group(1))
        if word not in _NOT_LOGINS and not _known(ctx, word) and not inside_id(*m.span(1)):
            spans.append((m.start(1), m.end(1)))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _replace(text: str, spans: list[tuple[int, int]], placeholder: str) -> str:
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(placeholder)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


# --------------------------------------------------------------------------- entry point
def scrub_tool_text(text: str, ctx: PrivacyContext) -> ScrubResult:
    """Quarantine one tool text (see the module docstring); pure, no I/O."""
    if not text:
        return ScrubResult("", ())
    flags: set[QuarantineFlag] = set()
    out: list[str] = []
    previous_removed = False
    for piece in _sentences(clean_text(text).translate(_LOCAL_LOOKALIKES)):
        body = piece.rstrip()
        trail = piece[len(body) :]
        found = _removal_flags(body, ctx) if body else []
        if found:
            flags.update(found)
            if previous_removed:
                out[-1] = REMOVED + trail
            else:
                out.append(REMOVED + trail)
            previous_removed = True
        else:
            out.append(piece)
            previous_removed = False
    kept = "".join(out)
    redaction = redact(kept, detect_pi(kept, ctx, mode="document"))
    if redaction.spans:
        flags.add("pi")
    identifiers = _identifier_spans(redaction.text, ctx)
    if identifiers:
        flags.add("technician_ref")
    scrubbed = _replace(redaction.text, identifiers, IDENTIFIER)
    return ScrubResult(scrubbed, tuple(f for f in FLAG_ORDER if f in flags))
