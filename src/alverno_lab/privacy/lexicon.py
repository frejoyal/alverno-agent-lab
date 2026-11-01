"""Text normalisation and the lexicons shipped as package data (``privacy/data/*.txt``).

Every classifier pattern and every lexicon lookup works on :func:`normalize` output, so
« Côté », « COTE » and « côté » compare equal. Offsets are always taken on the raw text:
normalisation changes lengths and is never used to locate a span.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Iterable
from importlib import resources

from alverno_lab.privacy.types import PrivacyConfigError
from alverno_lab.text import FRENCH_STOPWORDS

# Typographic apostrophe, written as a code point to stay unambiguous.
APOSTROPHE = chr(0x2019)
_SPACES_RE = re.compile(r"[^\S\n]+")
# A word: letters, with internal hyphens (« Jean-François », « Saint-Jean-Baptiste »).
# Apostrophes split words (« d'Alverno » -> « d », « Alverno »). Letters and digits glue
# to a word, an underscore does not: « _Julie Bérubé_ » (Markdown emphasis) holds words.
WORD_RE = re.compile(r"(?<![^\W_])(?<!-)[^\W\d_]+(?:-[^\W\d_]+)*(?![^\W_])(?!-)")

# Every space separator (Unicode category Zs, e.g. the no-break spaces U+00A0 and U+202F
# of French number typography) plus the tab, and every dash or minus sign. ``fold_separators``
# maps them one to one, so offsets stay valid; tests check the list against unicodedata.
SPACE_SEPARATORS: tuple[int, ...] = (
    0x09,
    0x20,
    0xA0,
    0x1680,
    *range(0x2000, 0x200B),
    0x202F,
    0x205F,
    0x3000,
)
DASHES: tuple[int, ...] = (*range(0x2010, 0x2016), 0x2212, 0xFE58, 0xFE63, 0xFF0D)
_FOLD = {cp: " " for cp in SPACE_SEPARATORS} | {cp: "-" for cp in DASHES}
# Separators of a digit run, as a character-class fragment: horizontal spaces, dots,
# parentheses, slashes and dashes (the hyphen, U+2010 to U+2015, the minus sign U+2212).
# Shared by the canary scan and the output guard, so both read « (819) 555-0142 » and
# « 819 - 555 - 0142 » as one run. Commas, pipes and line breaks end a run there.
DIGIT_SEPARATORS = r" \t.()/" + re.escape("-") + chr(0x2010) + "-" + chr(0x2015) + chr(0x2212)
DIGIT_RUN_RE = re.compile(rf"\d(?:[{DIGIT_SEPARATORS}]{{0,3}}\d)*")
# Look-alike letters (Cyrillic, Greek) folded to Latin ones in questions: « vacances »
# typed with a Cyrillic a (U+0430) would otherwise defeat every keyword rule. Written as
# code points, in the order of the Latin letters below.
_LOOKALIKES = (
    *(0x430, 0x435, 0x43E, 0x440, 0x441, 0x443, 0x445, 0x456, 0x458, 0x455),
    *(0x410, 0x412, 0x415, 0x41A, 0x41C, 0x41D, 0x41E, 0x420, 0x421, 0x422, 0x425),
    *(0x3BF, 0x3B1, 0x39F, 0x391),
)
_CONFUSABLES = str.maketrans("".join(map(chr, _LOOKALIKES)), "aeopcyxijsABEKMHOPCTXoaOA")
# Line breaks and tabs stay; every other control character is dropped.
_KEPT_CONTROLS = frozenset("\n\r\t")


def fold_separators(text: str) -> str:
    """Spaces of any kind become ``' '`` and dashes become ``'-'``; the length is kept."""
    return text.translate(_FOLD)


def clean_text(text: str) -> str:
    """The text every rule reads in a question (and, later, in an answer).

    NFKC (no-break spaces, the full-width at sign and digits, composed accents of a decomposed
    macOS paste), then format characters (zero-width spaces, BOM) and control characters
    are removed and look-alike letters are folded. Lengths change: offsets are taken on
    the cleaned text.
    """
    composed = unicodedata.normalize("NFKC", text)
    kept = "".join(
        c for c in composed if c in _KEPT_CONTROLS or unicodedata.category(c) not in {"Cf", "Cc"}
    )
    return unicodedata.normalize("NFC", kept.translate(_CONFUSABLES))


def strip_accents(text: str) -> str:
    """Remove combining marks after a canonical decomposition."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """NFKC, casefold, accent removal, typographic apostrophe, collapsed spaces (§5.1).

    Newlines are kept: a newline starts a sentence for the classifier.
    """
    folded = strip_accents(unicodedata.normalize("NFKC", text).casefold())
    return _SPACES_RE.sub(" ", folded.replace(APOSTROPHE, "'"))


def words(text: str) -> list[str]:
    """Normalised words of ``text`` (hyphenated compounds kept whole)."""
    return WORD_RE.findall(normalize(text))


def build_vocabulary(texts: Iterable[str]) -> frozenset[str]:
    """Normalised words of a corpus, with each part of a hyphenated compound added too."""
    vocabulary: set[str] = set()
    for text in texts:
        for word in words(text):
            vocabulary.add(word)
            vocabulary.update(word.split("-"))
    return frozenset(vocabulary)


# French function words that may be capitalised (a title, a quoted phrase) without being
# a name: they never count as an unknown proper noun (heuristics N3 and N4).
FUNCTION_WORDS: frozenset[str] = frozenset(
    {strip_accents(w) for w in FRENCH_STOPWORDS}
    | set(
        """
        apres avant aujourd'hui aupres autant bonjour bref cependant certains certaines
        chacun chacune combien comment depuis dois doit enfin ensuite est-ce hier ici
        lequel laquelle lesquels lesquelles mais merci neanmoins oui non or
        parce pendant peut-etre peut-on pourquoi puis-je puisque quand quel quelle quels
        quelles quelqu quelqu'un quoi salut sinon soit svp toutefois voici voila
        madame mademoiselle mme mlle monsieur
        """.split()  # noqa: SIM905 - a word block is easier to review than a list
    )
)


def _read_lines(name: str) -> list[str]:
    """Non-empty, non-comment lines of a package data file."""
    try:
        raw = resources.files("alverno_lab.privacy").joinpath("data", name).read_text("utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        raise PrivacyConfigError(f"privacy lexicon not readable: data/{name}") from exc
    lines = [line.strip() for line in raw.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


@functools.cache
def given_names() -> frozenset[str]:
    """Normalised common Québec given names (``data/prenoms.txt``), for rule N2."""
    names = frozenset(normalize(line) for line in _read_lines("prenoms.txt"))
    if not names:
        raise PrivacyConfigError("privacy lexicon is empty: data/prenoms.txt")
    return names


@functools.cache
def allowlist_terms() -> frozenset[str]:
    """Normalised capitalised terms that are not people (``data/allowlist.txt``).

    A multi-word entry (« Fête nationale ») is kept whole and each of its words is added
    too, so that the token heuristics skip « Fête » in « la Fête nationale ».
    """
    terms: set[str] = set()
    for line in _read_lines("allowlist.txt"):
        entry = normalize(line)
        terms.add(entry)
        terms.update(w for w in WORD_RE.findall(entry) if len(w) > 2)
    if not terms:
        raise PrivacyConfigError("privacy lexicon is empty: data/allowlist.txt")
    return frozenset(terms)
