"""Load the Markdown quality corpus and split it into stable, citable chunks."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+")


class CorpusError(ValueError):
    """Raised when a corpus file is malformed or violates a corpus rule."""


class Document(BaseModel):
    """One controlled quality document with its YAML front matter."""

    model_config = ConfigDict(frozen=True)

    doc_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    doc_type: str
    department: str
    version: str
    effective_date: dt.date
    path: Path
    body: str
    # Required (fail closed): a document without a declared level is rejected.
    sensitivity: int = Field(ge=1, le=5)
    # Front matter values are arbitrary YAML scalars; kept for display only.
    metadata: dict[str, Any] = Field(default_factory=dict)
    superseded_by: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: object) -> str:
        return str(value)


class Chunk(BaseModel):
    """A citable passage. ``chunk_id`` is ``f"{doc_id}#{n:02d}"`` and stable."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    version: str = ""
    effective_date: str = ""
    superseded_by: str = ""

    @property
    def header(self) -> str:
        """Light contextual header prepended to the indexed text."""
        return f"{self.title} — {self.section}"

    @property
    def indexed_text(self) -> str:
        """Text given to the embedder and the reranker: header plus body."""
        return f"{self.header}\n\n{self.text}"

    @property
    def lexical_text(self) -> str:
        """Text given to BM25: section heading plus body, without the document title.

        The title is repeated in every chunk of a document. Under BM25 length
        normalisation, a question that names the document then favours its shortest
        chunks over the one holding the answer (measured: ``RAL 7035`` cut from the
        context of q006). The dense side keeps the full header.
        """
        return f"{self.section}\n\n{self.text}"


_KNOWN_KEYS = {
    "doc_id",
    "title",
    "doc_type",
    "department",
    "version",
    "effective_date",
    "sensitivity",
}


def parse_document(path: Path) -> Document:
    """Parse one Markdown file with a ``---`` delimited YAML front matter."""
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise CorpusError(f"{path}: missing YAML front matter")
    end = raw.find("\n---", 4)
    if end == -1:
        raise CorpusError(f"{path}: unterminated YAML front matter")
    try:
        front = yaml.safe_load(raw[4:end])
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path}: invalid YAML front matter: {exc}") from exc
    if not isinstance(front, dict):
        raise CorpusError(f"{path}: front matter must be a mapping")
    body = raw[end + 4 :].lstrip("-").strip("\n")
    extra = {str(k): v for k, v in front.items() if k not in _KNOWN_KEYS}
    if front.get("sensitivity") is None:
        raise CorpusError(f"{path}: missing sensitivity (Law 25 lab scale, 1 to 5)")
    try:
        return Document(
            doc_id=front.get("doc_id"),
            title=front.get("title"),
            doc_type=front.get("doc_type"),
            department=front.get("department"),
            version=front.get("version"),
            effective_date=front.get("effective_date"),
            sensitivity=front["sensitivity"],
            path=path,
            body=body,
            metadata=extra,
        )
    except ValidationError as exc:
        raise CorpusError(f"{path}: invalid front matter: {exc}") from exc


def load_corpus(directory: Path, *, max_sensitivity: int | None = 2) -> list[Document]:
    """Load every ``*.md`` file of ``directory``, sorted by file name.

    Documents above ``max_sensitivity`` (Law 25 lab scale) are rejected: the
    Quality module is classified 2/5.
    """
    if not directory.is_dir():
        raise CorpusError(f"corpus directory not found: {directory}")
    documents: list[Document] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.md")):
        doc = parse_document(path)
        if doc.doc_id in seen:
            raise CorpusError(f"duplicate doc_id {doc.doc_id} in {path} and {seen[doc.doc_id]}")
        if max_sensitivity is not None and doc.sensitivity > max_sensitivity:
            raise CorpusError(
                f"{path}: sensitivity {doc.sensitivity} exceeds the module limit {max_sensitivity}"
            )
        seen[doc.doc_id] = path
        documents.append(doc)
    if not documents:
        raise CorpusError(f"no Markdown documents in {directory}")
    return resolve_versions(documents)


_ARCHIVED_ID_RE = re.compile(r"^(?P<base>.+)-V\d+$")
_SUPERSEDED_STATUSES = {"superseded", "obsolete", "archived", "remplacé", "périmé", "perime"}


def resolve_versions(documents: list[Document]) -> list[Document]:
    """Mark superseded documents.

    A document is superseded when its front matter says so (``superseded_by`` or a
    ``status`` such as ``superseded``), or when its id is ``<BASE>-V<n>`` and
    ``<BASE>`` exists with a later effective date (archived version convention).
    """
    by_id = {d.doc_id: d for d in documents}
    resolved: list[Document] = []
    for doc in documents:
        target = doc.metadata.get("superseded_by")
        status = str(doc.metadata.get("status", "")).strip().lower()
        match = _ARCHIVED_ID_RE.match(doc.doc_id)
        if not target and match and match.group("base") in by_id:
            current = by_id[match.group("base")]
            if current.effective_date > doc.effective_date:
                target = current.doc_id
        if not target and status in _SUPERSEDED_STATUSES:
            target = "?"
        resolved.append(doc.model_copy(update={"superseded_by": str(target)}) if target else doc)
    return resolved


def _split_sections(body: str, default_section: str) -> list[tuple[str, str]]:
    """Split a body on ``##`` headings; text before the first heading keeps the title."""
    sections: list[tuple[str, list[str]]] = [(default_section, [])]
    in_code = False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
        match = None if in_code else _HEADING_RE.match(line)
        if match:
            sections.append((match.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    result = [(name, "\n".join(lines).strip()) for name, lines in sections]
    return [(name, text) for name, text in result if text]


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries, then hard-cut."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT_RE.split(paragraph):
        while len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return pieces


def _pack_paragraphs(text: str, max_chars: int) -> list[str]:
    """Greedily pack blank-line separated paragraphs into pieces of at most ``max_chars``."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        parts = [paragraph] if len(paragraph) <= max_chars else _split_long(paragraph, max_chars)
        for part in parts:
            candidate = f"{current}\n\n{part}" if current else part
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    pieces.append(current)
                current = part
    if current:
        pieces.append(current)
    return pieces


def chunk_document(doc: Document, max_chars: int = 1200) -> list[Chunk]:
    """Chunk by ``##`` headings, then by paragraphs; ids are ``{doc_id}#{n:02d}`` from 01."""
    if max_chars < 50:
        raise ValueError("max_chars must be 50 or more")
    chunks: list[Chunk] = []
    for section, text in _split_sections(doc.body, doc.title):
        for piece in _pack_paragraphs(text, max_chars):
            n = len(chunks) + 1
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{n:02d}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    section=section,
                    text=piece,
                    version=doc.version,
                    effective_date=doc.effective_date.isoformat(),
                    superseded_by=doc.superseded_by or "",
                )
            )
    return chunks


def chunk_corpus(documents: list[Document], max_chars: int = 1200) -> list[Chunk]:
    """Chunk every document, preserving corpus order."""
    return [chunk for doc in documents for chunk in chunk_document(doc, max_chars)]
