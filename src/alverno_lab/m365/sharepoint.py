"""Simulated SharePoint library as the source of the Quality corpus (docs/adr/0025).

``data/m365/sharepoint/library.json`` describes the library « Documentation qualité »
in the shape of Microsoft Graph metadata: one item per document, with its version,
``eTag``, sensitivity label and the groups that may read it. It copies no text: each
item names a file of the content folder (``data/corpus/``) and pins its SHA-256.

:func:`sync` keeps only the items the agent's service group may read and whose label
is allowed, checks every hash, rewrites only the items whose ``eTag`` changed and moves
the items that left the export to ``<out>/.retired/`` (the corpus loader reads
``*.md`` at the top of the folder only). Nothing is deleted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SERVICE_GROUP = "Agent Qualité — lecture"
# Labels of loi25.md level 2 (« Général ») and of client specifications.
ALLOWED_LABELS = frozenset({"Général", "Restreint — client"})
MANIFEST = "sync-manifest.json"
RETIRED = ".retired"


class SyncError(ValueError):
    """The export is malformed, or a content file does not match its pinned hash."""


class LibraryItem(BaseModel):
    """One driveItem of the simulated library (Graph-like fields)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$")
    web_path: str
    version: str
    last_modified: str
    etag: str
    sensitivity_label: str
    readers: tuple[str, ...]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Library(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    site: str
    library: str
    items: tuple[LibraryItem, ...]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_library(path: Path) -> Library:
    try:
        library = Library.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise SyncError(f"{path}: invalid library export: {exc}") from exc
    names = [item.name for item in library.items]
    if len(names) != len(set(names)):
        raise SyncError(f"{path}: duplicate item names")
    return library


def readable(item: LibraryItem, group: str = SERVICE_GROUP) -> bool:
    return group in item.readers


def allowed(item: LibraryItem, labels: Iterable[str] = ALLOWED_LABELS) -> bool:
    return item.sensitivity_label in set(labels)


@dataclasses.dataclass
class SyncResult:
    written: list[str] = dataclasses.field(default_factory=list)
    unchanged: list[str] = dataclasses.field(default_factory=list)
    retired: list[str] = dataclasses.field(default_factory=list)
    skipped_group: list[str] = dataclasses.field(default_factory=list)
    skipped_label: list[str] = dataclasses.field(default_factory=list)

    @property
    def kept(self) -> list[str]:
        return sorted(self.written + self.unchanged)


def _read_manifest(out: Path) -> dict[str, dict[str, str]]:
    path = out / MANIFEST
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): dict(v) for k, v in data.items()}


def sync(library: Library, content: Path, out: Path) -> SyncResult:
    """Write the items the service group may read into ``out`` (see the module doc)."""
    result = SyncResult()
    previous = _read_manifest(out)
    manifest: dict[str, dict[str, str]] = {}
    chosen: list[LibraryItem] = []
    for item in library.items:
        if not readable(item):
            result.skipped_group.append(item.name)
        elif not allowed(item):
            result.skipped_label.append(item.name)
        else:
            chosen.append(item)
    # Every hash is checked before anything is written: a mismatch leaves ``out`` as is.
    for item in chosen:
        source = content / item.name
        if not source.is_file() or sha256_file(source) != item.sha256:
            raise SyncError(f"{item.name}: content does not match the export's SHA-256")
    out.mkdir(parents=True, exist_ok=True)
    for item in chosen:
        target = out / item.name
        before = previous.get(item.name)
        if before and before["etag"] == item.etag and target.is_file():
            result.unchanged.append(item.name)
        else:
            target.write_bytes((content / item.name).read_bytes())
            result.written.append(item.name)
        manifest[item.name] = {"etag": item.etag, "sha256": item.sha256, "id": item.id}
    for name in sorted(set(previous) - set(manifest)):
        stale = out / name
        if stale.is_file():
            retired = out / RETIRED
            retired.mkdir(exist_ok=True)
            stale.replace(retired / f"{name}.{previous[name]['sha256'][:12]}")
        result.retired.append(name)
    (out / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return result


def identical(left: Path, right: Path) -> bool:
    """True when both folders hold the same ``*.md`` files, byte for byte."""
    a = {p.name: p.read_bytes() for p in left.glob("*.md")}
    b = {p.name: p.read_bytes() for p in right.glob("*.md")}
    return a == b
