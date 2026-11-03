"""Schema-agnostic canary loader for ``alverno privacy scan`` (ADR 0015, spec I4 decision 8).

:func:`load_canaries` reads the canaries of any gold or holdout set, HR or module 3,
without validating the rest of the item: of each JSON line it reads ``id`` and
``canaries`` only. Every other key, ``contact_canaries`` included (a contact of the order
is allowed in the served draft, ADR 0015), is ignored, never validated and never kept.

A canary is either an HR object ``{"type", "value"}``, validated as
:class:`~alverno_lab.privacy.canary.Canary`, or a module 3 string. A string gets a type
by strict full-match inference, in this order:

1. ``courriel``: an email address;
2. ``matricule``: ``ALV-`` and 5 digits;
3. ``telephone``: at least 10 digits, with only digits and phone separators;
4. ``code_postal``: a Canadian postal code;
5. ``nom``: 2 or 3 capitalised words whose first word is a common given name;
6. otherwise the value is untyped (a price, a note, a login): it is matched as a
   substring, which is the rule of ``canary_in`` for the ``adresse`` type, so it is
   carried as an ``adresse`` canary **internally** and labelled ``texte``.

:class:`LoadedCanary` pairs the canary with that label, which is what the scan prints
(``path: label``), so an untyped value is never reported under a misleading type.

An untyped value made only of digits and number separators (a margin such as ``12.5`` or
a price such as ``1 245,80 $``) is not counted inside a longer number:
:func:`untyped_hits` keeps a file only when the value stands apart from other digits
(:func:`numeric_value_in`). A measured latency such as ``312.58`` in a report would
otherwise hit a two-digit margin by chance, run after run (found at step I7). In a JSON
or JSONL file a numeric value is looked for in the strings only, never in the JSON
numbers, which are measurements: a count of ``23.0`` once equalled a margin (step I8).

Errors are :class:`~alverno_lab.evals.dataset.DatasetError` with the message
``<path>:<line>:<rule>`` (line 0 for a rule on the whole file): ``file.missing``,
``file.unreadable``, ``file.empty``, ``json.invalid``, ``id.missing``,
``canaries.not_a_list`` and ``canary.invalid``. A message never holds a value, and every
error is raised ``from None`` outside any ``except`` block, so no exception chain keeps a
JSON line or a pydantic error that would echo one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.evals.dataset import DatasetError
from alverno_lab.privacy.canary import Canary, decode_artifact, scan_paths
from alverno_lab.privacy.lexicon import given_names, normalize
from alverno_lab.privacy.types import PIType

__all__ = [
    "UNTYPED_LABEL",
    "LoadedCanary",
    "infer_canary",
    "is_numeric_value",
    "load_canaries",
    "numeric_value_in",
    "untyped_hits",
]

# Label of a string canary whose type cannot be inferred (matched as a substring).
UNTYPED_LABEL = "texte"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_MATRICULE_RE = re.compile(r"ALV-[0-9]{5}")
# Digits and phone separators only: spaces, dots, dashes, parentheses and a leading plus.
_PHONE_RE = re.compile(r"\+?[0-9 .()-]+")
_PHONE_MIN_DIGITS = 10
_POSTAL_RE = re.compile(r"[A-Za-z][0-9][A-Za-z] ?[0-9][A-Za-z][0-9]")
# A capitalised word: an upper-case letter, then letters, with inner hyphens or apostrophes.
_CAPITALISED_RE = re.compile(r"[^\W\d_][^\W\d_]*(?:['-][^\W\d_]+)*")
# A number: digits with spaces, dots or commas between them, then an optional % or $.
_NUMERIC_RE = re.compile(r"[0-9](?:[0-9 .,]*[0-9])? ?[%$]?")
_SPACES_RE = re.compile(r"\s+")


class LoadedCanary(BaseModel):
    """A canary and the label a scan prints for it (its type, or ``texte``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    canary: Canary
    label: str = Field(pattern=r"^[a-z_]+$")


def _is_name(value: str) -> bool:
    parts = value.split(" ")
    if not 2 <= len(parts) <= 3:
        return False
    if not all(_CAPITALISED_RE.fullmatch(p) and p[0].isupper() for p in parts):
        return False
    return normalize(parts[0]) in given_names()


def _string_type(value: str) -> PIType | None:
    """The PI type of a string canary by strict full match, or None when untyped."""
    if _EMAIL_RE.fullmatch(value):
        return PIType.COURRIEL
    if _MATRICULE_RE.fullmatch(value):
        return PIType.MATRICULE
    if _PHONE_RE.fullmatch(value) and sum(c.isdigit() for c in value) >= _PHONE_MIN_DIGITS:
        return PIType.TELEPHONE
    if _POSTAL_RE.fullmatch(value):
        return PIType.CODE_POSTAL
    if _is_name(value):
        return PIType.NOM
    return None


def infer_canary(value: str) -> LoadedCanary:
    """The canary of a module 3 string, typed by inference or labelled ``texte``.

    ``value`` must be a non-empty string; the caller checks it.
    """
    pi_type = _string_type(value)
    if pi_type is None:
        # Untyped: the substring rule of canary_in, which the adresse type uses.
        return LoadedCanary(canary=Canary(type=PIType.ADRESSE, value=value), label=UNTYPED_LABEL)
    return LoadedCanary(canary=Canary(type=pi_type, value=value), label=pi_type.value)


def _flat(text: str) -> str:
    # The folding of canary_in's substring rule: NFKC, normalize, runs of spaces as one.
    return _SPACES_RE.sub(" ", normalize(unicodedata.normalize("NFKC", text)))


def is_numeric_value(value: str) -> bool:
    """Whether an untyped value is a number: digits, spaces, dots, commas, then % or $."""
    return _NUMERIC_RE.fullmatch(_flat(value).strip()) is not None


def numeric_value_in(value: str, text: str) -> bool:
    """Whether the number ``value`` occurs in ``text`` apart from other digits.

    A neighbouring digit, or a dot or comma followed by a digit, makes it part of a
    longer number: ``12.5`` is in « marge 12.5 % » but not in « 312.58 » or « 12.55 ».
    """
    target = re.escape(_flat(value).strip())
    pattern = rf"(?<![0-9])(?<![0-9][.,]){target}(?![0-9])(?![.,][0-9])"
    return re.search(pattern, _flat(text)) is not None


def untyped_hits(paths: Sequence[Path], canaries: Sequence[Canary]) -> list[tuple[Path, Canary]]:
    """Every (file, canary) hit of untyped canaries, files in the order they are walked.

    A canary is matched as a substring, as ``canary_in`` does for ``adresse``; a numeric
    value must also stand apart from other digits in the decoded file.
    """
    hits: list[tuple[Path, Canary]] = []
    for canary in canaries:
        numeric = is_numeric_value(canary.value)
        for path, _ in scan_paths(paths, [canary]):
            if numeric and not numeric_value_in(canary.value, _numeric_scope(path)):
                continue
            hits.append((path, canary))
    return hits


def _json_strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for key, value in node.items() for s in (str(key), *_json_strings(value))]
    if isinstance(node, list):
        return [s for value in node for s in _json_strings(value)]
    return []


def _numeric_scope(path: Path) -> str:
    """The text in which a numeric canary is looked for.

    In a JSON or JSONL file, the string values and keys only: a JSON number is a
    measurement the evaluation wrote (a count, a rate, a latency), never fixture text, and
    a count such as ``23.0`` once equalled a fixture margin by chance (step I8). A line
    that does not parse is kept whole, so nothing is skipped silently. Any other file is
    decoded whole.
    """
    text = decode_artifact(path.read_bytes())
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return text
    blocks = [text] if path.suffix.lower() == ".json" else text.splitlines()
    kept: list[str] = []
    for block in blocks:
        try:
            kept.extend(_json_strings(json.loads(block)))
        except ValueError:
            kept.append(block)
    return "\n".join(kept)


def _canary_of(node: object) -> LoadedCanary | None:
    """The canary of one element of a ``canaries`` list, or None when it is invalid."""
    if isinstance(node, str):
        return infer_canary(node) if node.strip() else None
    if not isinstance(node, dict):
        return None
    try:
        canary = Canary.model_validate(node)
    except ValidationError:
        return None
    return LoadedCanary(canary=canary, label=canary.type.value)


def _line_canaries(line: str) -> list[LoadedCanary] | str:
    """The canaries of one JSON line, or the broken rule. Exceptions are dropped here."""
    try:
        doc = json.loads(line)
    # json.loads raises a bare ValueError for an integer over the int-to-str digit limit,
    # and RecursionError for arrays nested too deep; JSONDecodeError is a ValueError.
    except (ValueError, RecursionError):
        return "json.invalid"
    if not isinstance(doc, dict):
        return "json.invalid"
    item_id = doc.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        return "id.missing"
    nodes = doc.get("canaries", [])
    if not isinstance(nodes, list):
        return "canaries.not_a_list"
    found: list[LoadedCanary] = []
    for node in nodes:
        loaded = _canary_of(node)
        if loaded is None:
            return "canary.invalid"
        found.append(loaded)
    return found


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def load_canaries(path: Path) -> list[LoadedCanary]:
    """Unique canaries of a JSONL set, in order of first appearance.

    On an HR set, the canaries equal ``gold_canaries(load_hr_gold(path))``, in the same
    order. A set with no canary gives an empty list; the caller decides what that means.
    """
    if not path.is_file():
        raise DatasetError(f"{path}:0:file.missing") from None
    text = _read_text(path)
    if text is None:
        raise DatasetError(f"{path}:0:file.unreadable") from None
    loaded: dict[LoadedCanary, None] = {}
    seen_line = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        seen_line = True
        found = _line_canaries(line)
        if isinstance(found, str):
            raise DatasetError(f"{path}:{lineno}:{found}") from None
        loaded.update(dict.fromkeys(found))
    if not seen_line:
        raise DatasetError(f"{path}:0:file.empty") from None
    return list(loaded)
