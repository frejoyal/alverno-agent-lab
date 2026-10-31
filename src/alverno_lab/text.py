"""French text normalisation shared by BM25, the hash embedder and the evaluations."""

from __future__ import annotations

import re
import unicodedata

# Short French stopword list: articles, pronouns, prepositions, auxiliaries and
# interrogatives. Domain words (``qualité``, ``soudure``...) are never stopwords.
FRENCH_STOPWORDS: frozenset[str] = frozenset(
    """
    a à afin ai aie ainsi alors au aucun aucune aussi autre aux avec avoir c ça car ce ceci
    cela celle celles celui ces cet cette ceux chaque chez ci combien comme comment d dans de
    des doit doivent donc dont du elle elles en encore entre est et été être eu faut il ils j
    je l la le les leur leurs lui m ma mais me même mes moi mon n ne ni nos notre nous on ont
    ou où par pas peut peuvent plus pour pourquoi qu quand que quel quelle quelles quels qui
    quoi s sa sans se selon ses si son sont sous sur t ta te tes toi ton tous tout toute
    toutes très tu un une vos votre vous y
    """.split()  # noqa: SIM905 - a word block is easier to review than a list
)

_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[a-zà-öø-ÿœæ]+")
_SPACES_RE = re.compile(r"\s+")
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d)")
# Typographic apostrophe and dashes -> ASCII (written as escapes to stay unambiguous).
_PUNCT_MAP = str.maketrans({0x2019: "'", 0x2011: "-", 0x2013: "-", 0x2014: "-"})


def _light_stem(token: str) -> str:
    """Strip a plural ``s``/``x`` so that ``pièces`` and ``pièce`` match."""
    if len(token) > 3 and token[-1] in "sx" and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Tokenise French text: lowercase, accents kept, short stopwords removed.

    Decimal numbers are kept whole with a dot separator (``0,25`` -> ``0.25``).
    """
    lowered = unicodedata.normalize("NFC", text).lower()
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(lowered):
        if raw[0].isdigit():
            tokens.append(raw.replace(",", "."))
            continue
        if not keep_stopwords and raw in FRENCH_STOPWORDS:
            continue
        tokens.append(_light_stem(raw))
    return tokens


def normalize_for_match(text: str) -> str:
    """Normalise text for substring fact matching (case, spaces, decimal commas)."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = normalized.translate(_PUNCT_MAP)
    normalized = _DECIMAL_COMMA_RE.sub(".", normalized)
    return _SPACES_RE.sub(" ", normalized).strip()
