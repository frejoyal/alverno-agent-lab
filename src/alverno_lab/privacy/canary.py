"""Canaries: synthetic PI values planted in evaluation questions, searched for in artifacts.

Matching is by type (amendment M12) so that a canary is found whatever its separators or
case, while report floats and short numbers never match:

- nas, nam, telephone, compte_bancaire, numero: digits only, inside a digit run whose
  separators (spaces, dots, dashes, slashes, parentheses; at most three in a row, as in
  « 819 - 555 - 0142 ») are removed;
- matricule: « ALV » + the 5 digits, any case, optional dash or space;
- adresse, courriel, date_naissance: normalised string match (§5.1, spaces collapsed);
- code_postal: normalised, with an optional space between its two halves;
- nom: the surname as a whole normalised word.

Every text is read after NFKC, so no-break spaces (U+00A0, U+202F) and full-width digits
fold first. A hit is reported as a path and a type: the value itself is never printed.
Files named ``.env`` or ``.env.*`` are never read: they hold local settings, not reports.
"""

from __future__ import annotations

import codecs
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alverno_lab.privacy.lexicon import DIGIT_RUN_RE, normalize
from alverno_lab.privacy.types import NUMERIC_TYPES, PIType

# Digits joined by at most three separators among horizontal spaces, dots, dashes (the
# hyphen, U+2010 to U+2015, the minus sign), slashes and parentheses, so that
# « (819) 555-0142 » and « 819 - 555 - 0142 » are one run. Commas, pipes and line breaks
# end a run: JSON arrays, table cells and list lines never merge. The run is the one of
# ``lexicon``, shared with the output guard.
# A character-class fragment: the hyphen, U+2010 to U+2015 and the minus sign U+2212.
_DASHES = re.escape("-") + chr(0x2010) + "-" + chr(0x2015) + chr(0x2212)
_DIGIT_RUN_RE = DIGIT_RUN_RE
_SPACES_RE = re.compile(r"\s+")
_JSON_SUFFIXES = frozenset({".json", ".jsonl"})


class Canary(BaseModel):
    """A synthetic value whose presence in an artifact is a leak. The value stays out of
    ``repr`` so that a failing assertion or a log line never prints it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: PIType
    value: str = Field(min_length=1, repr=False)


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _digit_runs(text: str) -> Iterator[str]:
    for match in _DIGIT_RUN_RE.finditer(text):
        yield _digits(match.group(0))


def _flat(text: str) -> str:
    return _SPACES_RE.sub(" ", normalize(text))


def canary_in(canary: Canary, text: str) -> bool:
    """True when ``canary`` occurs in ``text`` under the matching rule of its type."""
    value = canary.value
    text = unicodedata.normalize("NFKC", text)
    if canary.type in NUMERIC_TYPES:
        target = _digits(value)
        return bool(target) and any(target in run for run in _digit_runs(text))
    if canary.type is PIType.MATRICULE:
        target = _digits(value)
        pattern = rf"(?<![a-z0-9])alv[ {_DASHES}]?{re.escape(target)}(?!\d)"
        return re.search(pattern, text, re.IGNORECASE) is not None
    if canary.type is PIType.CODE_POSTAL:
        compact = _flat(value).replace(" ", "")
        pattern = rf"(?<![\w]){re.escape(compact[:3])} ?{re.escape(compact[3:])}(?![\w])"
        return re.search(pattern, _flat(text)) is not None
    if canary.type is PIType.NOM:
        surname = _flat(value).split(" ")[-1]
        return re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", _flat(text)) is not None
    return _flat(value) in _flat(text)


def _json_strings(node: Any) -> Iterator[str]:  # Any: arbitrary decoded JSON
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _json_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _json_strings(value)


def _variants(path: Path, raw: str) -> list[str]:
    """The raw text, plus the decoded strings of a JSON or JSONL file (escapes undone)."""
    variants = [raw]
    if path.suffix.lower() not in _JSON_SUFFIXES:
        return variants
    docs: list[Any] = []  # Any: arbitrary decoded JSON
    try:
        docs.append(json.loads(raw))
    except json.JSONDecodeError:
        for line in raw.splitlines():
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    variants.extend("\n".join(_json_strings(doc)) for doc in docs)
    return variants


def _is_env_file(path: Path) -> bool:
    """A local settings file (``.env``, ``.env.local``...): never read by a scan."""
    return path.name == ".env" or path.name.startswith(".env.")


def _files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file() and not _is_env_file(p))
        elif path.is_file() and not _is_env_file(path):
            yield path


def decode_artifact(data: bytes) -> str:
    """Text of an artifact file, whatever its encoding among UTF-8 and UTF-16.

    Windows PowerShell 5.1 writes UTF-16 with ``>`` and ``Out-File``: read as UTF-8, it
    would hide every canary behind NUL bytes. A BOM decides; otherwise a NUL byte in the
    first 4 KB means UTF-16 LE; otherwise UTF-8 (BOM removed). Undecodable bytes are
    dropped, never fatal.
    """
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="ignore")
    if b"\x00" in data[:4096]:
        return data.decode("utf-16-le", errors="ignore")
    return data.decode("utf-8-sig", errors="ignore")


def scan_paths(paths: Iterable[Path], canaries: Sequence[Canary]) -> list[tuple[Path, PIType]]:
    """Every (file, canary type) pair with a match, files of directories included."""
    hits: list[tuple[Path, PIType]] = []
    for path in _files(paths):
        raw = decode_artifact(path.read_bytes())
        texts = _variants(path, raw)
        found = {c.type for c in canaries if any(canary_in(c, t) for t in texts)}
        hits.extend((path, t) for t in sorted(found, key=lambda t: t.value))
    return hits
