"""Tokenizer, corpus loading and chunking."""

from __future__ import annotations

from pathlib import Path

import pytest

from alverno_lab.corpus import Chunk, CorpusError, chunk_document, load_corpus, parse_document
from alverno_lab.text import normalize_for_match, tokenize

from .conftest import FIXTURES


def test_tokenize_lowercases_keeps_accents_and_drops_stopwords() -> None:
    tokens = tokenize("L'Épaisseur de la PEINTURE est vérifiée à chaque quart")
    assert tokens == ["épaisseur", "peinture", "vérifiée", "quart"]


def test_tokenize_keeps_decimal_numbers_and_light_plural_stemming() -> None:
    assert tokenize("caniveau de 0,5 mm") == ["caniveau", "0.5", "mm"]
    assert tokenize("pièces") == tokenize("pièce") == ["pièce"]
    assert tokenize("classe") == ["classe"]


def test_tokenize_keep_stopwords() -> None:
    assert "la" in tokenize("la soudure", keep_stopwords=True)


def test_normalize_for_match_handles_decimal_comma_and_spaces() -> None:
    nbsp, apostrophe = chr(0xA0), chr(0x2019)
    assert normalize_for_match(f"0,25{nbsp}MM") == normalize_for_match("0.25 mm")
    assert normalize_for_match(f"l{apostrophe}inox") == "l'inox"


def test_load_corpus_reads_front_matter() -> None:
    docs = load_corpus(FIXTURES / "corpus")
    assert [d.doc_id for d in docs] == ["IT-021", "QP-004-V1", "QP-004", "QP-010", "SPEC-101"]
    qp010 = next(d for d in docs if d.doc_id == "QP-010")
    assert qp010.version == "2.1"
    assert qp010.effective_date.isoformat() == "2026-02-03"
    assert qp010.doc_type == "procedure"
    assert qp010.body.startswith("## Objet")


def _write(path: Path, front: str, body: str = "## A\n\nTexte.") -> Path:
    path.write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return path


FRONT = (
    'doc_id: X-1\ntitle: "T"\ndoc_type: policy\ndepartment: qualite\n'
    "version: 1.0\neffective_date: 2026-01-01\nsensitivity: 2"
)


def test_load_corpus_rejects_duplicates_and_high_sensitivity(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", FRONT)
    _write(tmp_path / "b.md", FRONT)
    with pytest.raises(CorpusError, match="duplicate"):
        load_corpus(tmp_path)
    (tmp_path / "b.md").unlink()
    _write(
        tmp_path / "c.md", FRONT.replace("X-1", "X-2").replace("sensitivity: 2", "sensitivity: 4")
    )
    with pytest.raises(CorpusError, match="sensitivity"):
        load_corpus(tmp_path)
    assert len(load_corpus(tmp_path, max_sensitivity=None)) == 2


def test_parse_document_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.md"
    bad.write_text("pas de front matter", encoding="utf-8")
    with pytest.raises(CorpusError, match="front matter"):
        parse_document(bad)
    with pytest.raises(CorpusError, match="invalid front matter"):
        parse_document(_write(tmp_path / "m.md", 'title: "T"\nsensitivity: 2'))
    with pytest.raises(CorpusError):
        load_corpus(tmp_path / "missing")


def test_missing_sensitivity_fails_closed(tmp_path: Path) -> None:
    # Regression: a document without a level defaulted to 2 and was indexed.
    path = _write(tmp_path / "s.md", FRONT.replace("\nsensitivity: 2", ""))
    with pytest.raises(CorpusError, match="missing sensitivity"):
        parse_document(path)
    with pytest.raises(CorpusError, match="missing sensitivity"):
        load_corpus(tmp_path)


def test_version_is_coerced_to_string(tmp_path: Path) -> None:
    doc = parse_document(_write(tmp_path / "v.md", FRONT))
    assert doc.version == "1.0"


def test_chunking_by_heading_with_stable_ids() -> None:
    doc = parse_document(FIXTURES / "corpus" / "QP-010.md")
    chunks = chunk_document(doc)
    assert [c.chunk_id for c in chunks] == ["QP-010#01", "QP-010#02", "QP-010#03"]
    assert [c.section for c in chunks] == ["Objet", "Épaisseur du feuil", "Adhérence"]
    assert chunks[1].indexed_text.startswith("Contrôle de la peinture en poudre — Épaisseur")
    assert chunk_document(doc) == chunks  # deterministic


def test_preamble_uses_title_as_section() -> None:
    doc = parse_document(FIXTURES / "corpus" / "SPEC-101.md")
    first = chunk_document(doc)[0]
    assert first.section == doc.title
    assert "316L" in first.text


def test_long_sections_are_split_under_max_chars(tmp_path: Path) -> None:
    paragraph = " ".join(["La soudure est inspectée visuellement."] * 12)
    body = "## Long\n\n" + "\n\n".join([paragraph] * 3)
    doc = parse_document(_write(tmp_path / "l.md", FRONT, body))
    chunks = chunk_document(doc, max_chars=300)
    assert len(chunks) > 3
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[-1].chunk_id == f"X-1#{len(chunks):02d}"
    with pytest.raises(ValueError):
        chunk_document(doc, max_chars=10)


def test_archived_version_is_marked_superseded(tmp_path: Path) -> None:
    docs = {d.doc_id: d for d in load_corpus(FIXTURES / "corpus")}
    assert docs["QP-004-V1"].superseded_by == "QP-004"
    assert docs["QP-004"].superseded_by is None
    chunk = chunk_document(docs["QP-004-V1"])[0]
    assert chunk.superseded_by == "QP-004"

    _write(tmp_path / "a.md", FRONT + "\nstatus: superseded")
    assert load_corpus(tmp_path)[0].superseded_by == "?"
    _write(tmp_path / "a.md", FRONT + "\nsuperseded_by: X-9")
    assert load_corpus(tmp_path)[0].superseded_by == "X-9"


def test_lexical_text_drops_the_title_but_keeps_the_section() -> None:
    chunk = Chunk(
        chunk_id="D-1#01", doc_id="D-1", title="Titre du document", section="Portée", text="Corps."
    )
    assert chunk.lexical_text == "Portée\n\nCorps."
    assert chunk.indexed_text.startswith("Titre du document — Portée")
