"""Prompt-injection screening shared by every module (amendment M1).

The patterns are the module-1 ones, moved here verbatim: the Quality gate rebuilds its
list as ``[*INJECTION_PATTERNS, CREDENTIAL_PATTERN]`` in the original order, so its
behaviour is unchanged. The HR module adds one relaxation: asking for help with a
password (« Comment réinitialiser le mot de passe du portail RH ? », « Mon mot de passe
est bloqué, que faire ? ») is not an attack (M1, widened by M13).

The HR path (``credential_help_ok=True``) also reads the text with typographic
apostrophes folded and with accent-tolerant copies of the patterns, so that « jeton
d'accès » typed with U+2019 or « Oublie toutes les regles » are caught. An accent is
only made optional where the pattern has one: « j'ai oublié les règles » still does not
match (M11).

A help request must not carry the password itself: passwords are not a PI type, so
redaction would leave the value in the text sent to the embedder, BM25 and the LLM.
:func:`credential_value` flags a question that mentions a password and also holds a
word that looks like one (« Mon mot de passe qwerty est bloqué », « … c'est
soleil2026 ? »); the HR screen refuses it as ``prompt_injection``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from alverno_lab.privacy.lexicon import APOSTROPHE, normalize, strip_accents

# Instruction-override attempts (French and English). Questions are data, never orders.
INJECTION_PATTERNS: tuple[str, ...] = (
    r"\b(ignore[rz]?|oublie[rz]?|néglige[rz]?)\b.{0,40}\b(instructions?|consignes?|règles?"
    r"|directives?|prompt)\b",
    r"\bne tiens? (plus )?pas compte\b.{0,40}\b(instructions?|consignes?|règles?)\b",
    r"\bignore (all|any|the|your|previous|prior)\b.{0,30}\binstructions?\b",
    r"\b(prompt|message) syst[eè]me\b",
    r"\bsystem prompt\b",
    r"\b(tu es|vous êtes) maintenant\b",
    r"\byou are now\b",
    # "agis" alone is a normal verb ("comment agis-tu si une pièce est hors tolérance?").
    r"\b(agis|agissez)\s+(comme|en tant que|en qualité de)\b",
    r"\b(fais comme si|faites comme si|act as|pretend)\b",
    r"\b(mode (développeur|administrateur|admin|debug)|developer mode|admin mode|jailbreak)\b",
    r"\binvente[rz]?\b.{0,40}\b(tolérances?|valeurs?|réponses?|normes?|chiffres?|données)\b",
    r"\b(révèle|dévoile|affiche|montre)[rz]?\b.{0,30}\b(prompt|consignes?|instructions?)\b",
)
# Credential requests: module 1 refuses any mention; HR allows a password help request.
CREDENTIAL_PATTERN = r"\b(mot de passe|password|jeton d'accès|clé d'api|api key)\b"


def accent_tolerant(pattern: str) -> str:
    """``pattern`` where every accented letter also matches its unaccented form.

    « règles? » becomes « r[èe]gles? »; inside a character class the base letter is
    added to the class. Letters without an accent are left alone, so an accented word
    in the text never matches an unaccented letter of the pattern.
    """
    out: list[str] = []
    in_class = escaped = False
    for c in pattern:
        base = strip_accents(c)
        if escaped:
            out.append(c)
            escaped = False
        elif c == "\\":
            out.append(c)
            escaped = True
        elif c == "[" and not in_class:
            out.append(c)
            in_class = True
        elif c == "]" and in_class:
            out.append(c)
            in_class = False
        elif base != c and base.isalpha():
            out.append(f"{c}{base}" if in_class else f"[{c}{base}]")
        else:
            out.append(c)
    return "".join(out)


_INJECTION_RE = tuple(re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS)
_CREDENTIAL_RE = re.compile(CREDENTIAL_PATTERN, re.IGNORECASE)
_HR_INJECTION_RE = tuple(re.compile(accent_tolerant(p), re.IGNORECASE) for p in INJECTION_PATTERNS)
_HR_CREDENTIAL_RE = re.compile(accent_tolerant(CREDENTIAL_PATTERN), re.IGNORECASE)
# Only these two credentials can be the object of a help request; tokens and API keys
# are always blocked.
_HELPABLE = frozenset({"mot de passe", "password"})
_HELP_WINDOW = 40
# Help and reveal verbs are matched on unaccented text (« reinitialiser », « revele »).
_HELP_RE = re.compile(
    r"\b(reinitialiser|reinitialise|changer|change|modifier|oublie|perdu|recuperer|bloque"
    r"|verrouille|expire)\b",
    re.IGNORECASE,
)
_REVEAL_RE = re.compile(
    r"\b(donne|donnez|revele|devoile|affiche|montre|ecris|envoie|dis-moi|quel est|c'est quoi)\b",
    re.IGNORECASE,
)


def is_injection(text: str, *, credential_help_ok: bool = False) -> bool:
    """True when ``text`` tries to override the rules or asks for a credential.

    Without ``credential_help_ok``, this is module 1's screen on the raw text. With it
    (HR), the text is read with apostrophes folded and accent-tolerant patterns, and a
    password mention is allowed when a help word occurs in the 40 characters before or
    after it (M13) and no reveal verb occurs anywhere.
    """
    if not credential_help_ok:
        return any(p.search(text) for p in _INJECTION_RE) or bool(_CREDENTIAL_RE.search(text))
    plain = text.replace(APOSTROPHE, "'")
    if any(p.search(plain) for p in _HR_INJECTION_RE):
        return True
    matches = list(_HR_CREDENTIAL_RE.finditer(plain))
    if not matches:
        return False
    if _REVEAL_RE.search(strip_accents(plain)):
        return True
    for match in matches:
        if match.group(0).lower() not in _HELPABLE:
            return True
        before = plain[max(0, match.start() - _HELP_WINDOW) : match.start()]
        after = plain[match.end() : match.end() + _HELP_WINDOW]
        if not any(_HELP_RE.search(strip_accents(w)) for w in (before, after)):
            return True
    return False


# --------------------------------------------------------------------------- password values
# Words skipped after a password mention: they introduce a value, they are not one.
_VALUE_FILLERS = frozenset({"est", "etait", "actuel", "actuelle", "temporaire", "c'est"})
_VALUE_WINDOW = 3
# Quotes, written as code points where they look alike: the typographic apostrophe
# U+2019 is already folded to "'" (``plain``); U+2018 is its opening counterpart.
_LEFT_SINGLE = chr(0x2018)
_QUOTES = f"«»\"“”'{_LEFT_SINGLE}"
_OPENERS = f"(«\"“'{_LEFT_SINGLE}[{{"
_CLOSERS = ",.;:?!)»\"”']}"
_SECRET_CHARS = frozenset("#!@$%&*?=+^~|<>")
_PLAIN_SECRET_CHARS = _SECRET_CHARS - {"@"}
_ORDINAL_RE = re.compile(r"\d+(?:er|re|e|eme|ieme)", re.IGNORECASE)
_DOC_ID_RE = re.compile(r"[A-Z]{2,4}-\d{3}(?:#\d{2})?")
_HELPABLE_RE = re.compile(r"\b(mot de passe|password)\b", re.IGNORECASE)


def _bare(token: str) -> tuple[str, bool]:
    """``token`` without its surrounding punctuation, and whether it was quoted."""
    stripped = token.lstrip(_OPENERS).rstrip(_CLOSERS)
    quoted = (token[:1] in _QUOTES or token[-1:] in _QUOTES) and bool(stripped)
    return stripped, quoted


def _looks_like_secret(word: str, chars: frozenset[str]) -> bool:
    """A word mixing letters and digits, or holding a symbol, as passwords do."""
    if _ORDINAL_RE.fullmatch(word) or _DOC_ID_RE.fullmatch(word):
        return False
    has_digit = any(c.isdecimal() for c in word)
    has_letter = any(c.isalpha() for c in word)
    return (has_digit and has_letter) or any(c in chars for c in word)


def credential_value(text: str, known: Callable[[str], bool]) -> bool:
    """True when ``text`` mentions a password and seems to hold one (HR screen).

    Two checks, run only when « mot de passe » / « password » occurs:

    - anywhere in the text, a word that mixes letters and digits or holds a symbol
      (« soleil2026 », « Tigre#4471 »); ordinals (« 1er ») and document ids (« RH-001 »)
      are not values;
    - in the 3 words after each mention (fillers such as « est », « c'est », « : » or
      « = » skipped), a quoted word, a word with a digit or a symbol, or a word that
      ``known`` rejects (``known`` receives the normalised word: corpus vocabulary,
      function words, help words, allowlist).
    """
    plain = text.replace(APOSTROPHE, "'")
    mentions = list(_HELPABLE_RE.finditer(plain))
    if not mentions:
        return False
    for token in plain.split():
        word, _ = _bare(token)
        if word and _looks_like_secret(word, _PLAIN_SECRET_CHARS):
            return True
    for mention in mentions:
        seen = 0
        for token in plain[mention.end() :].split():
            word, quoted = _bare(token)
            norm = normalize(word)
            if not word or norm in _VALUE_FILLERS:
                continue
            if quoted or _looks_like_secret(word, _SECRET_CHARS):
                return True
            parts = [p for p in re.split(r"[-']", norm) if p]
            if not _ORDINAL_RE.fullmatch(word) and not all(
                known(p) or _HELP_RE.fullmatch(p) for p in parts
            ):
                return True
            seen += 1
            if seen == _VALUE_WINDOW:
                break
    return False
