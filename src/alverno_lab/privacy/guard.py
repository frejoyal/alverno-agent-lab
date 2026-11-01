"""Output guard: withhold an answer that may disclose personal information (spec §5.6, D8).

The guard reads the model answer before the footer and the note are added. The first of
four rules that fires withholds the whole answer (refusal ``output_pi``):

1. ``detected``: the detector, in document mode, finds a span that is neither in the
   cited extracts (as a whole token: « Jean » is not in « Saint-Jean-Baptiste ») nor
   allow-listed;
2. ``placeholder``: the answer contains a name marker ([NOM], « [ NOM ] », <NOM>,
   [PRÉNOM], [Nom de famille]...), or a placeholder absent from the redacted question;
3. ``echo``: a raw value detected in the question appears in the answer: as a whole word
   after normalisation; for a name, any of its words that the corpus does not know (the
   surname alone, « S. Gagnon »); a postal code with or without its space or a hyphen;
   or, for a value with 5 digits or more (SIN, phone, account, matricule...), as digits
   inside a digit run, separators such as spaces, line breaks, commas or underscores
   removed;
4. ``proper_noun``: a capitalised word in the middle of a sentence (3 letters or more,
   not all caps) is absent from the cited extracts, the corpus vocabulary and the
   allowlist. On output a sentence starts only at the start of the text, after a line
   break, or after « . », « ? » or « ! » followed by a space, and never after the dot of
   an initial (« J. ») or an abbreviation (« M. », « Mr. », « art. »): a colon, a semicolon or an
   opening guillemet does not start one. This is narrower than the classifier's rule of
   §5.1 on purpose: there, a wider rule fails closed; here, it would fail open. A word
   that opens a sentence is not examined: a lone surname there (« Gagnon est encore
   malade. ») passes (published known miss, ADR 0006).

The answer is cleaned first (``lexicon.clean_text``), like the question, so zero-width
characters or look-alike letters cannot hide a value. The verdict names a rule, never a
value.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from alverno_lab.privacy.detect import detect_pi
from alverno_lab.privacy.lexicon import (
    APOSTROPHE,
    DIGIT_RUN_RE,
    DIGIT_SEPARATORS,
    FUNCTION_WORDS,
    WORD_RE,
    build_vocabulary,
    clean_text,
    fold_separators,
    normalize,
)
from alverno_lab.privacy.types import GuardVerdict, PrivacyContext

# Placeholders on normalised text (« [TÉLÉPHONE] » reads « [telephone] »), whatever the
# case, the inner spaces or the bracket the model wrote them with (« [ NOM ] », « <NOM> »,
# « {nas} », « [Nom de famille] »). A name marker is always refused.
_PLACEHOLDER_RE = re.compile(
    r"[\[<{]\s*(nom de famille|nom|prenom|nas|nam|courriel|telephone|matricule|code_postal"
    r"|adresse|date_naissance|compte_bancaire|numero)\b[^\]>}\n]{0,20}[\]>}]"
)
_NAME_MARKERS = frozenset({"nom", "prenom", "nom de famille"})
# Echo of a long number: the digit runs of the answer with wider separators than a canary
# run, so « 819\n555\n0142 », « 819_555_0142 » or « 819, 555, 0142 » stay one run.
_ECHO_RUN_RE = re.compile(rf"\d(?:[\s,_{DIGIT_SEPARATORS}]{{0,3}}\d)*")
_MIN_ECHO_DIGITS = 5
_MIN_ECHO_WORD = 3
_LETTERS_RE = re.compile(r"[^\W\d_]+")
_POSTAL_RE = re.compile(r"[a-z]\d[a-z]\d[a-z]\d")
_POSTAL_SEPARATORS = r"[\s" + DIGIT_SEPARATORS + r"]*"
# Characters skipped before a word when looking for the start of its sentence: spaces,
# list bullets, opening brackets and quotes.
_LEADING = frozenset(' \t([*_-«"“')
_SENTENCE_END = frozenset(".?!")
# A dot after one of these words (or after a single capital letter) ends no sentence.
_ABBREVIATIONS = frozenset({"m", "mme", "mlle", "dr", "st", "p", "ex", "art", "mr", "mrs", "ms"})
_WORD_BEFORE_RE = re.compile(r"([^\W\d_]+)$")


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdecimal())


def _at_sentence_start(text: str, start: int) -> bool:
    """True when the word at ``start`` opens a sentence (output rule, see the module doc)."""
    i = start - 1
    spaced = False
    while i >= 0 and text[i] in _LEADING:
        spaced = spaced or text[i] in " \t"
        i -= 1
    if i < 0 or text[i] == "\n":
        return True
    if text[i] not in _SENTENCE_END or not spaced:
        return False
    if text[i] != ".":
        return True
    before = _WORD_BEFORE_RE.search(text[max(0, i - 8) : i])
    if before is None:
        return True
    word = before.group(1)
    return not (len(word) == 1 or word.casefold() in _ABBREVIATIONS)


def _in_text(value: str, text: str) -> bool:
    """``value`` in ``text`` as a whole token: a hyphen belongs to the word around it."""
    return re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text) is not None


def _detected(answer: str, extracts: str, ctx: PrivacyContext) -> bool:
    for span in detect_pi(answer, ctx, mode="document"):
        value = normalize(answer[span.start : span.end]).strip()
        if value in ctx.allowlist or _in_text(value, extracts):
            continue
        digits = _digits(value)
        if digits:
            runs = _digits_runs(extracts)
            # A number without letters may be a cut copy of an extract number (« 2023-06-0 »
            # of « 2023-06-01 »): its digits inside one run of the extracts. A value with
            # letters (matricule, postal code) must match a whole run.
            numeric = not any(c.isalpha() for c in value)
            if digits in runs or (numeric and any(digits in run for run in runs)):
                continue
        return True
    return False


def _digits_runs(text: str) -> set[str]:
    return {_digits(m.group(0)) for m in DIGIT_RUN_RE.finditer(text)}


def _placeholder(answer: str, redacted_question: str) -> bool:
    found = set(_PLACEHOLDER_RE.findall(normalize(answer)))
    if not found:
        return False
    allowed = set(_PLACEHOLDER_RE.findall(normalize(redacted_question)))
    return bool(found & _NAME_MARKERS) or not found <= allowed


def _unknown_words(value: str, ctx: PrivacyContext) -> list[str]:
    """Words of a name-like value that the corpus does not know (given name, surname)."""
    return [
        w
        for w in _LETTERS_RE.findall(value)
        if len(w) >= _MIN_ECHO_WORD
        and w not in FUNCTION_WORDS
        and w not in ctx.vocabulary
        and w not in ctx.allowlist
    ]


def _echo(answer: str, raw_values: Iterable[str], ctx: PrivacyContext) -> bool:
    flat = normalize(answer)
    answer_words: set[str] | None = None
    runs: list[str] | None = None
    for raw in raw_values:
        value = normalize(clean_text(raw)).strip()
        if not value:
            continue
        if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", flat):
            return True
        compact = re.sub(r"[\s-]", "", value)
        if _POSTAL_RE.fullmatch(compact):
            pattern = _POSTAL_SEPARATORS.join(re.escape(c) for c in compact)
            if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", flat):
                return True
        digits = _digits(value)
        if not digits and "@" not in value:
            # A name: each word the corpus does not know is checked on its own.
            if answer_words is None:
                answer_words = set(_LETTERS_RE.findall(flat))
            if any(w in answer_words for w in _unknown_words(value, ctx)):
                return True
        if len(digits) >= _MIN_ECHO_DIGITS:
            if runs is None:
                runs = [_digits(m.group(0)) for m in _ECHO_RUN_RE.finditer(flat)]
            if any(digits in run for run in runs):
                return True
    return False


def _proper_noun(answer: str, extract_words: frozenset[str], ctx: PrivacyContext) -> bool:
    text = fold_separators(answer.replace(APOSTROPHE, "'"))
    for match in WORD_RE.finditer(text):
        token = match.group(0)
        if not token[0].isupper() or token.isupper() or sum(c.isalpha() for c in token) < 3:
            continue
        if _at_sentence_start(text, match.start()):
            continue
        norm = normalize(token)
        if _known(norm, extract_words, ctx) or (
            "-" in norm and all(_known(p, extract_words, ctx) for p in norm.split("-"))
        ):
            continue
        return True
    return False


def _known(word: str, extract_words: frozenset[str], ctx: PrivacyContext) -> bool:
    return (
        word in extract_words
        or word in ctx.vocabulary
        or word in ctx.allowlist
        or word in FUNCTION_WORDS
    )


def check_output(
    answer: str,
    *,
    redacted_question: str,
    raw_values: Sequence[str],
    cited_extracts: Sequence[str],
    ctx: PrivacyContext,
) -> GuardVerdict:
    """Check a model answer against the four rules of §5.6; ``ok`` is False when one fires.

    ``raw_values`` are the values detected in the cleaned question (memory only);
    ``cited_extracts`` are the extracts the answer cites, as the model saw them.
    """
    text = clean_text(answer)
    extracts = normalize("\n".join(cited_extracts))
    if _detected(text, extracts, ctx):
        return GuardVerdict(ok=False, rule="detected")
    if _placeholder(text, redacted_question):
        return GuardVerdict(ok=False, rule="placeholder")
    if _echo(text, raw_values, ctx):
        return GuardVerdict(ok=False, rule="echo")
    extract_words = build_vocabulary(cited_extracts)
    if _proper_noun(text, extract_words, ctx):
        return GuardVerdict(ok=False, rule="proper_noun")
    return GuardVerdict(ok=True)
