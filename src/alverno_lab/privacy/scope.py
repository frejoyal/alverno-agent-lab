"""Account scope of a 3a text: does it name a value of another customer account?

ADR 0012 treats any value of another account in a draft or a request as a disclosure to
a third party (lab rule). :func:`build_scope_index` indexes, per account id, the values
that identify it (the I6 wiring feeds it ``connectors.sim.account_scope_values``: ids,
labels, e-mail domains, contact names, business e-mails, contact refs, order ids and
tracking refs). :func:`check_scope` then reads a text for the in-scope account.

- A value flags only when it belongs to some account other than the in-scope one **and
  not** to the in-scope one: a value shared with the in-scope account never flags.
- Ids (« CLI-004 », « CMD-2026-0403 », « TRK-881811 ») match exactly, as whole tokens,
  once every dash of the text is folded to ``-`` (a word processor's non-breaking
  hyphen or en dash hides nothing).
- E-mail addresses match exactly (case-insensitive, as addresses are); an account's
  e-mail domain flags inside any e-mail address of that domain or of a subdomain, and
  as a bare host name (« client03.example », « www.client03.example »).
- Every other value (labels, surnames, « given_name surname ») matches as whole words on
  ``normalize``d text: accents and case are ignored (« Hébert » flags « HEBERT »), and a
  line break counts as a space (a label wrapped over two lines still flags). Inside a
  value, a hyphen and a space are the same separator: a hyphenated label or compound
  written with a space (or a spaced one written with a hyphen) still flags.

The verdict says how many other accounts were touched, never which ones and never a
value. In the 3a reguard node (I5/I6, i2-spec §6), :func:`check_scope` runs **before**
the output guard (``check_output``): a hit yields ``cross_account``, and ``output_pi`` is
checked only when there is none.

Published known misses: a value written with a typo, an id without its prefix or in
lowercase, an e-mail address split by spaces or spelt out, and a contact named by given
name alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from alverno_lab.privacy.lexicon import clean_text, fold_separators, normalize

ScopeRule = Literal["other_account_value"]


class ScopeError(ValueError):
    """A scope call is invalid; the message is ``scope:<rule>``, never a value."""


@dataclass(frozen=True)
class ScopeVerdict:
    """``ok`` when no other account's value occurs; ``accounts`` counts the others."""

    ok: bool
    rule: ScopeRule | None
    accounts: int


# An identifier: capitals, a hyphen, then capitals or digits (« CLI-004 », « TRK-881811 »).
_ID_SHAPE_RE = re.compile(r"[A-Z]{2,4}-[A-Z0-9]+(?:-[A-Z0-9]+)*")
_DOMAIN_SHAPE_RE = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_EMAIL_RE = re.compile(r"(?<![\w.%+-])[\w.%+-]+@([\w-]+(?:\.[\w-]+)+)")
_WHITESPACE_RE = re.compile(r"\s+")
# Inside a word value, a hyphen and a space separate the same words (« Brume-Haute »).
_WORD_SEPARATOR_RE = re.compile(r"[\s-]+")
_WORD_JOIN = "[ -]"


@dataclass(frozen=True)
class ScopeIndex:
    """Account values by kind; built by :func:`build_scope_index`.

    Value-bearing fields stay out of ``repr`` (labels and contact names are personal
    information); only the account ids show.
    """

    accounts: frozenset[str]
    ids: Mapping[str, frozenset[str]] = field(repr=False)
    emails: Mapping[str, frozenset[str]] = field(repr=False)
    domains: Mapping[str, frozenset[str]] = field(repr=False)
    words: Mapping[str, frozenset[str]] = field(repr=False)
    _word_patterns: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = field(
        repr=False, compare=False
    )
    _id_patterns: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = field(
        repr=False, compare=False
    )
    _domain_patterns: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = field(
        repr=False, compare=False
    )


def _word_pattern(word: str) -> re.Pattern[str] | None:
    """A whole-word pattern of a ``normalize``d value; hyphens and spaces are equivalent.

    None when the value holds no word (a lone hyphen), which then never matches.
    """
    parts = [re.escape(part) for part in _WORD_SEPARATOR_RE.split(word) if part]
    if not parts:
        return None
    return re.compile(rf"(?<![\w-]){_WORD_JOIN.join(parts)}(?![\w-])")


def _frozen(table: Mapping[str, set[str]]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType({key: frozenset(owners) for key, owners in table.items()})


def build_scope_index(values_by_account: Mapping[str, Iterable[str]]) -> ScopeIndex:
    """Index ``values_by_account`` (account id -> values); the account id is a value too.

    Words are kept ``normalize``d, ids exact, e-mails and domains casefolded. Empty values
    are skipped.
    """
    ids: dict[str, set[str]] = {}
    emails: dict[str, set[str]] = {}
    domains: dict[str, set[str]] = {}
    words: dict[str, set[str]] = {}
    for account, values in values_by_account.items():
        for raw in (account, *values):
            value = clean_text(raw).strip()
            if not value:
                continue
            if "@" in value:
                emails.setdefault(value.casefold(), set()).add(account)
            elif _ID_SHAPE_RE.fullmatch(value):
                ids.setdefault(value, set()).add(account)
            elif _DOMAIN_SHAPE_RE.fullmatch(value.casefold()):
                domains.setdefault(value.casefold(), set()).add(account)
            else:
                norm = normalize(value).strip()
                if norm:
                    words.setdefault(norm, set()).add(account)
    frozen_ids, frozen_domains, frozen_words = _frozen(ids), _frozen(domains), _frozen(words)
    return ScopeIndex(
        accounts=frozenset(values_by_account),
        ids=frozen_ids,
        emails=_frozen(emails),
        domains=frozen_domains,
        words=frozen_words,
        _word_patterns=tuple(
            (pattern, owners)
            for w, owners in frozen_words.items()
            if (pattern := _word_pattern(w)) is not None
        ),
        _id_patterns=tuple(
            (re.compile(rf"(?<![\w-]){re.escape(i)}(?![\w-])"), owners)
            for i, owners in frozen_ids.items()
        ),
        _domain_patterns=tuple(
            (re.compile(rf"(?<![\w-]){re.escape(d)}(?![\w-])(?!\.[\w])"), owners)
            for d, owners in frozen_domains.items()
        ),
    )


def _email_owners(text: str, index: ScopeIndex) -> Iterable[frozenset[str]]:
    """Owners of every e-mail address of ``text``: by address, then by domain."""
    for match in _EMAIL_RE.finditer(text):
        owners = index.emails.get(match.group(0).casefold())
        if owners is not None:
            yield owners
        host = match.group(1).casefold()
        for domain, domain_owners in index.domains.items():
            if host == domain or host.endswith(f".{domain}"):
                yield domain_owners


def check_scope(text: str, in_scope_account: str, index: ScopeIndex) -> ScopeVerdict:
    """Whether ``text`` names a value of an account other than ``in_scope_account``.

    An unknown ``in_scope_account`` raises ``ScopeError("scope:unknown_account")``.
    """
    if in_scope_account not in index.accounts:
        raise ScopeError("scope:unknown_account") from None
    cleaned = fold_separators(clean_text(text))
    lowered = cleaned.casefold()
    norm = _WHITESPACE_RE.sub(" ", normalize(cleaned))
    hits: list[frozenset[str]] = list(_email_owners(cleaned, index))
    hits.extend(owners for pattern, owners in index._id_patterns if pattern.search(cleaned))
    hits.extend(owners for pattern, owners in index._domain_patterns if pattern.search(lowered))
    hits.extend(owners for pattern, owners in index._word_patterns if pattern.search(norm))
    others: set[str] = set()
    for owners in hits:
        if in_scope_account not in owners:
            others.update(owners)
    if others:
        return ScopeVerdict(ok=False, rule="other_account_value", accounts=len(others))
    return ScopeVerdict(ok=True, rule=None, accounts=0)
