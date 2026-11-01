"""Deterministic personal-information detectors and redaction (spec §5.2 and §5.3).

Every detector is a regular expression or a lexicon lookup: the RPRP can audit them and
CI tests them. Spans carry a type, offsets, a basis and a rule id, never the value.
Question mode runs every rule; document mode skips the name heuristics N3 and N4.

Separators are folded first, one character for one: every space separator (no-break
spaces included) reads as a space and every dash as a hyphen, so « 046 454 286 » with
U+202F is a SIN. Callers clean a question first (``lexicon.clean_text``: NFKC, zero-width
characters removed); offsets refer to the text passed in.

Published known misses: names written in lowercase, a lone surname that is also a common
word (« Côté »), nicknames, initials, a single given name written in capitals
(« JULIE a-t-elle... »; two capitalised words are caught), a lone surname that follows
« Saint-/Sainte- » (« Saint-Pierre » alone), lowercase street names, digits written in
words, obfuscated e-mail addresses (« julie (at) alverno »), and foreign formats beyond
the ``numero`` fallback.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator, Sequence
from typing import Literal

from alverno_lab.privacy.lexicon import (
    FUNCTION_WORDS,
    WORD_RE,
    fold_separators,
    given_names,
    normalize,
)
from alverno_lab.privacy.types import BASIS_RANK, PISpan, PIType, PrivacyContext, Redaction

Mode = Literal["question", "document"]

PLACEHOLDERS: dict[PIType, str] = {
    PIType.NAS: "[NAS]",
    PIType.NAM: "[NAM]",
    PIType.COURRIEL: "[COURRIEL]",
    PIType.TELEPHONE: "[TÉLÉPHONE]",
    PIType.MATRICULE: "[MATRICULE]",
    PIType.CODE_POSTAL: "[CODE_POSTAL]",
    PIType.ADRESSE: "[ADRESSE]",
    PIType.DATE_NAISSANCE: "[DATE_NAISSANCE]",
    PIType.COMPTE_BANCAIRE: "[COMPTE_BANCAIRE]",
    PIType.NOM: "[NOM]",
    PIType.NUMERO: "[NUMÉRO]",
}
PLACEHOLDER_RE = re.compile("|".join(re.escape(p) for p in PLACEHOLDERS.values()))

# Characters before which a word starts a sentence (§5.1): text start, . ? ! : ; « " or
# a newline, with optional spaces and opening brackets in between.
_SENTENCE_END = frozenset('.?!:;«"\n')
_CONTEXT_WINDOW = 30
# Spaces and opening brackets skipped when looking back for a sentence end.
_LEADING_PUNCT = frozenset(" \t([*_" + chr(0xA0))


def luhn_valid(digits: str) -> bool:
    """Luhn check of a digit string (separators are ignored); False without digits.

    Only decimal digits count: « ² » is a digit for ``str.isdigit`` but not a number.
    """
    numbers = [int(c) for c in digits if c.isdecimal()]
    if not numbers:
        return False
    total = 0
    for i, n in enumerate(reversed(numbers)):
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


# --------------------------------------------------------------------------- structured
_NAS_RE = re.compile(r"(?<![\w.])(?<!\d[ .-])\d{3}[ .-]?\d{3}[ .-]?\d{3}(?![ .-]?\d)(?!\w)")
_NAS_CONTEXT_RE = re.compile(r"\b(nas|assurance sociale|sin)\b")
_NAM_RE = re.compile(r"(?<![A-Za-z\d])[A-Z]{4} ?(\d{4}) ?(\d{4})(?![ .-]?\d)(?!\w)")
_EMAIL_RE = re.compile(r"(?<![\w.%+-])[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(
    r"(?<![\w+])(?<!\d[ .-])(?:\+?1[ .-]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[ .-]?[2-9]\d{2}"
    r"[ .-]?\d{4}(?:,?\s*(?:poste|p\.)\s*\d{1,5})?(?![\w])",
    re.IGNORECASE,
)
# « ALV » + 5 digits, with an optional hyphen or space: the canary rule of M12.
_MATRICULE_RE = re.compile(r"(?<![\w-])ALV[- ]?\d{5}(?!\d)", re.IGNORECASE)
_MATRICULE_CONTEXT_RE = re.compile(
    r"\b(?:matricule|(?:numéro|numero|no|n°) d'employée?)(?:\W+[^\W\d]\w*){0,2}?\W+"
    r"(\d{4,6})(?![ .-]?\d)",
    re.IGNORECASE,
)
_POSTAL_RE = re.compile(
    r"(?<![\w-])[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d(?![\w-])",
    re.IGNORECASE,
)
_STREET_RE = re.compile(
    r"(?<![\w-])\d{1,5}[A-Za-z]?,?\s+(?:rue|boul\.|boulevard|avenue|av\.|chemin|ch\.|rang"
    r"|route|place|montée|croissant)(?=\s)",
    re.IGNORECASE,
)
_CAP_WORD = r"[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:-[^\W\d_]+)*"
# After the street type: optional particles, then 1 to 3 capitalised name words.
_STREET_NAME_RE = re.compile(
    r"(?:\s+(?i:de|du|des|la|le|les|aux?)(?=\s)|\s+(?i:[dl])'(?=[^\W\d_]))*"
    rf"(?:\s+|(?<=')){_CAP_WORD}(?:\s+{_CAP_WORD}){{0,2}}"
)
_MONTHS = (
    r"janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre"
    r"|novembre|décembre|decembre"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}"
    rf"|(?:1er|\d{{1,2}})\s+(?:{_MONTHS})\s+\d{{4}})(?!\d)",
    re.IGNORECASE,
)
# « né(e) le » (also typed « nee le »), never the negation « ne le ».
_BIRTH_CONTEXT_RE = re.compile(
    r"\b(?:n(?:é|ée|ee)\s+le|date\s+de\s+naissance|naissance)\b", re.IGNORECASE
)
_BANK_FULL_RE = re.compile(r"(?<![\w.,-])\d{5}[ -]?\d{3}[ -]?\d{7,12}(?![ .,-]?\d)(?!\w)")
_BANK_CONTEXT_RE = re.compile(
    r"\b(?:compte|transit|institution|d[ée]p[ôo]t direct|bancaires?)\b", re.IGNORECASE
)
_BANK_SHORT_RE = re.compile(r"(?<![\w.,-])\d{7,12}(?![ .,-]?\d)(?!\w)")
# Digits joined by an optional single separator, a slash, or a hyphen or slash with a
# space on either side (« 046 - 454 - 286 », « 819/555/0142 »). A bare hyphen is only in
# the second alternative: if both could match it, « 1-1-1-…-1x » backtracks
# exponentially (review 2, ReDoS).
_NUMERO_RE = re.compile(r"(?<![\w-])\d(?:(?:[ .]| ?[/-] ?)?\d){6,}(?![\w-])")
_NUMERO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")

# --------------------------------------------------------------------------- names
_TITLE_RE = re.compile(
    r"(?:(?<![\w.])M\.|(?<!\w)(?i:mme|mlle)\.?|(?<!\w)(?i:monsieur|madame|mademoiselle))\s+"
)
_INVERTED_VERB_RE = re.compile(r"\s+[^\W\d_]+(?:-t)?-(?:il|elle)(?![\w-])")
_SAINT_PREFIXES = ("saint-", "sainte-", "st-", "ste-")
_MAX_NAME_WORDS = 3


def _context_before(text: str, start: int, pattern: re.Pattern[str]) -> bool:
    window = normalize(text[max(0, start - _CONTEXT_WINDOW) : start])
    return pattern.search(window) is not None


def _structured(text: str, ctx: PrivacyContext) -> Iterator[PISpan]:
    """Spans of every structured type except the ``numero`` fallback."""
    for m in _NAS_RE.finditer(text):
        if luhn_valid(m.group(0)):
            yield PISpan(PIType.NAS, m.start(), m.end(), "validated", "nas.luhn")
        elif _context_before(text, m.start(), _NAS_CONTEXT_RE):
            yield PISpan(PIType.NAS, m.start(), m.end(), "context", "nas.context")
        # Otherwise the digits fall to the numero fallback.
    for m in _NAM_RE.finditer(text):
        digits = m.group(1) + m.group(2)
        month, day = int(digits[2:4]), int(digits[4:6])
        if (1 <= month <= 12 or 51 <= month <= 62) and 1 <= day <= 31:
            yield PISpan(PIType.NAM, m.start(), m.end(), "validated", "nam")
    for m in _EMAIL_RE.finditer(text):
        if m.group(0).casefold() not in ctx.role_mailboxes:
            yield PISpan(PIType.COURRIEL, m.start(), m.end(), "pattern", "courriel")
    for m in _PHONE_RE.finditer(text):
        yield PISpan(PIType.TELEPHONE, m.start(), m.end(), "pattern", "telephone")
    for m in _MATRICULE_RE.finditer(text):
        yield PISpan(PIType.MATRICULE, m.start(), m.end(), "pattern", "matricule")
    for m in _MATRICULE_CONTEXT_RE.finditer(text):
        yield PISpan(PIType.MATRICULE, m.start(1), m.end(1), "context", "matricule.context")
    for m in _POSTAL_RE.finditer(text):
        yield PISpan(PIType.CODE_POSTAL, m.start(), m.end(), "pattern", "code_postal")
    for m in _STREET_RE.finditer(text):
        name = _STREET_NAME_RE.match(text, m.end())
        if name:
            yield PISpan(PIType.ADRESSE, m.start(), name.end(), "pattern", "adresse")
    for kw in _BIRTH_CONTEXT_RE.finditer(text):
        date = _DATE_RE.search(text, kw.end(), kw.end() + _CONTEXT_WINDOW + 20)
        if date and date.start() - kw.end() <= _CONTEXT_WINDOW:
            yield PISpan(PIType.DATE_NAISSANCE, date.start(), date.end(), "context", "date")
    for m in _BANK_FULL_RE.finditer(text):
        yield PISpan(PIType.COMPTE_BANCAIRE, m.start(), m.end(), "pattern", "compte.full")
    for kw in _BANK_CONTEXT_RE.finditer(text):
        num = _BANK_SHORT_RE.search(text, kw.end(), kw.end() + _CONTEXT_WINDOW + 12)
        if num and num.start() - kw.end() <= _CONTEXT_WINDOW:
            yield PISpan(PIType.COMPTE_BANCAIRE, num.start(), num.end(), "context", "compte")


def _numero(text: str) -> Iterator[PISpan]:
    for m in _NUMERO_RE.finditer(text):
        if not _NUMERO_DATE_RE.fullmatch(m.group(0)):
            yield PISpan(PIType.NUMERO, m.start(), m.end(), "pattern", "numero")


def _is_capitalised(word: str) -> bool:
    """« Julie », not « JULIE »: the N3 and N4 heuristics skip all-caps acronyms."""
    return word[0].isupper() and not word.isupper()


def _caps_name_word(word: str, ctx: PrivacyContext) -> bool:
    """An all-caps word that can be part of a name written in capitals (« BÉRUBÉ »):
    two letters or more, not a known word (« RH », « CNESST ») and not an inverted verb
    (« A-T-ELLE »)."""
    norm = normalize(word)
    return (
        word.isupper()
        and sum(c.isalpha() for c in word) >= 2
        and not _known(norm, ctx)
        and not norm.endswith(("-il", "-elle", "-ils", "-elles", "-on"))
    )


def _at_sentence_start(text: str, start: int) -> bool:
    i = start - 1
    while i >= 0 and text[i] in _LEADING_PUNCT:
        i -= 1
    return i < 0 or text[i] in _SENTENCE_END


def _following_words(
    text: str, end: int, limit: int, ctx: PrivacyContext | None = None
) -> Iterator[re.Match[str]]:
    """Capitalised words that follow ``end``, separated by single spaces.

    With ``ctx`` (surnames after a title or a given name), a surname written in capitals
    counts too (« Julie BÉRUBÉ »).
    """
    for _ in range(limit):
        if not text.startswith(" ", end):
            return
        nxt = WORD_RE.match(text, end + 1)
        if nxt is None:
            return
        word = nxt.group(0)
        if not (_is_capitalised(word) or (ctx is not None and _caps_name_word(word, ctx))):
            return
        yield nxt
        end = nxt.end()


def _known(word: str, ctx: PrivacyContext) -> bool:
    """A normalised word that is a corpus word, an allow-listed term or a function word."""
    return word in ctx.vocabulary or word in ctx.allowlist or word in FUNCTION_WORDS


def _is_given_name(norm: str) -> bool:
    if norm.startswith(_SAINT_PREFIXES):
        return False
    names = given_names()
    return norm in names or all(part in names for part in norm.split("-"))


def _names(text: str, ctx: PrivacyContext, mode: Mode) -> Iterator[PISpan]:
    # N1: a civility title followed by 1 to 3 capitalised words (the title is kept).
    for m in _TITLE_RE.finditer(text):
        first = WORD_RE.match(text, m.end())
        if first is None or not (
            _is_capitalised(first.group(0)) or _caps_name_word(first.group(0), ctx)
        ):
            continue
        rest = list(_following_words(text, first.end(), _MAX_NAME_WORDS - 1, ctx))
        end = rest[-1].end() if rest else first.end()
        yield PISpan(PIType.NOM, first.start(), end, "pattern", "N1")
    for word in WORD_RE.finditer(text):
        token = word.group(0)
        norm = normalize(token)
        if token.isupper() and _is_given_name(norm) and sum(c.isalpha() for c in token) >= 3:
            # N2 in capitals: « JULIE BÉRUBÉ ». A lone all-caps given name is not taken
            # (« CLAUDE.md »); the surname must follow.
            rest = list(
                itertools.takewhile(
                    lambda nxt: _caps_name_word(nxt.group(0), ctx),
                    _following_words(text, word.end(), 2, ctx),
                )
            )
            if rest:
                yield PISpan(PIType.NOM, word.start(), rest[-1].end(), "pattern", "N2")
            continue
        if not _is_capitalised(token):
            continue
        # N2: a given name from the lexicon, then optional capitalised surname words.
        if _is_given_name(norm):
            end = word.end()
            for nxt in _following_words(text, word.end(), 2, ctx):
                if normalize(nxt.group(0)) in ctx.allowlist:
                    break
                end = nxt.end()
            yield PISpan(PIType.NOM, word.start(), end, "pattern", "N2")
            continue
        if mode == "document" or sum(c.isalpha() for c in token) < 3 or _known(norm, ctx):
            continue
        if norm.startswith(_SAINT_PREFIXES):
            # « Saint-Jean », « Sainte-Julie », « Saint-Hyacinthe »: holidays and places.
            continue
        if not _at_sentence_start(text, word.start()):
            # N3: an unknown capitalised word inside a sentence.
            end = word.end()
            for nxt in _following_words(text, word.end(), _MAX_NAME_WORDS - 1):
                if _known(normalize(nxt.group(0)), ctx):
                    break
                end = nxt.end()
            yield PISpan(PIType.NOM, word.start(), end, "heuristic", "N3")
        elif _INVERTED_VERB_RE.match(text, word.end()):
            # N4: an unknown word opening a sentence, followed by « a-t-elle », « peut-il ».
            yield PISpan(PIType.NOM, word.start(), word.end(), "heuristic", "N4")


def _resolve(candidates: Sequence[PISpan], fallback: Sequence[PISpan]) -> list[PISpan]:
    """Pick non-overlapping spans: basis, then length, then leftmost (§5.2).

    ``numero`` only claims digit runs that no specific detector covers (M12). A run that a
    specific span covers only in part becomes one ``numero`` span over both, so that no
    digit is left visible.
    """
    chosen: list[PISpan] = []
    ordered = sorted(candidates, key=lambda s: (BASIS_RANK[s.basis], -s.length, s.start))
    for span in ordered:
        if not any(span.overlaps(c) for c in chosen):
            chosen.append(span)
    for run in fallback:
        hit = [c for c in chosen if run.overlaps(c)]
        if not hit:
            chosen.append(run)
        elif not any(c.start <= run.start and run.end <= c.end for c in hit):
            start = min(run.start, *(c.start for c in hit))
            end = max(run.end, *(c.end for c in hit))
            chosen = [c for c in chosen if c not in hit]
            chosen.append(PISpan(PIType.NUMERO, start, end, "pattern", "numero"))
    return sorted(chosen, key=lambda s: s.start)


def detect_pi(text: str, ctx: PrivacyContext, *, mode: Mode = "question") -> list[PISpan]:
    """Detect personal information in ``text``; spans are sorted and never overlap.

    Placeholders left by an earlier redaction are never detected again, so that
    redaction is idempotent.
    """
    shielded = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
    # Typographic apostrophes, space separators and dashes become ASCII ones: one
    # character for one, so offsets stay valid.
    text = fold_separators(text.replace(chr(0x2019), "'"))
    candidates: list[PISpan] = []
    for span in [*_structured(text, ctx), *_names(text, ctx, mode)]:
        if span.type is PIType.NOM and normalize(text[span.start : span.end]) in ctx.allowlist:
            continue
        candidates.append(span)
    found = _resolve(candidates, list(_numero(text)))
    return [s for s in found if not any(s.start < e and b < s.end for b, e in shielded)]


def redact(text: str, spans: Sequence[PISpan]) -> Redaction:
    """Replace each span by its typed placeholder; no value-to-placeholder map is kept.

    Overlapping spans and spans inside an existing placeholder are skipped, so that
    ``redact(redact(x).text, detect_pi(redact(x).text))`` leaves the text unchanged.
    """
    shielded = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
    applied: list[PISpan] = []
    parts: list[str] = []
    cursor = 0
    for span in sorted(spans, key=lambda s: (s.start, -s.length)):
        if span.start < cursor or span.end > len(text) or span.start >= span.end:
            continue
        if any(span.start < e and b < span.end for b, e in shielded):
            continue
        parts.append(text[cursor : span.start])
        parts.append(PLACEHOLDERS[span.type])
        applied.append(span)
        cursor = span.end
    parts.append(text[cursor:])
    return Redaction(text="".join(parts), spans=tuple(applied))
