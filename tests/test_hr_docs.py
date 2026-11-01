"""Checks on the module 2 privacy impact assessment (EFVP), its template and the docs
that make legal statements about module 2 (ADRs 0006 to 0010, legal sources)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import REPO_ROOT

DOCS_DIR = REPO_ROOT / "docs"
EFVP_DIR = DOCS_DIR / "efvp"
TEMPLATE = EFVP_DIR / "template.md"
HR_EFVP = EFVP_DIR / "hr-module.md"
DOCS = (TEMPLATE, HR_EFVP)
MODULE2_ADRS = tuple(
    path
    for path in sorted((DOCS_DIR / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if 6 <= int(path.name[:4]) <= 10
)
CLAIM_DOCS = (*DOCS, *MODULE2_ADRS, DOCS_DIR / "hr-legal-sources.md")

DISCLAIMER = "ceci n'est pas un avis juridique"
# Absolute claims the lab never makes about its own documents: compliance,
# certification, or approval by the CAI or the CNESST (inflected forms included).
FORBIDDEN_CLAIMS = re.compile(
    r"\b(?:conformes?|certifiée?s?)\b|\bapprouvée?s? par la (?:CAI|CNESST)\b",
    re.IGNORECASE,
)
# Fictional role names from docs/company.md that contain a forbidden word but make no
# claim about a document. Listed one by one so the rule itself stays strict.
ALLOWED_ROLE_NAMES = ("inspecteur en soudage certifié",)
FENCE = re.compile(r"^(```|~~~)")
H2 = re.compile(r"^## (?!#)(.+?)\s*$")


def h2_headings(path: Path) -> list[str]:
    """Return the level-2 headings of a Markdown file, skipping fenced code blocks."""
    headings: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else H2.match(line)
        if match:
            headings.append(match.group(1))
    return headings


def normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace(chr(0x2019), "'")


def forbidden_claims(text: str) -> list[str]:
    """Return the forbidden claims in ``text``, ignoring the allow-listed role names."""
    for name in ALLOWED_ROLE_NAMES:
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return sorted({m.group(0) for m in FORBIDDEN_CLAIMS.finditer(text)})


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_document_exists(path: Path) -> None:
    assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"


def test_module2_adrs_are_all_checked() -> None:
    assert [p.name[:4] for p in MODULE2_ADRS] == ["0006", "0007", "0008", "0009", "0010"]


def test_template_has_the_expected_sections() -> None:
    assert h2_headings(TEMPLATE) == [
        "0. Résumé",
        "1. Description du projet et objet de l'EFVP",
        "2. Rôles et responsabilités",
        "3. Renseignements personnels et ampleur de l'évaluation",
        "4. Conformité aux obligations",
        "5. Risques et stratégies d'atténuation",
        "6. Plan d'action",
        "7. Avis du responsable, décision et approbation",
        "Annexes",
    ]


def test_hr_efvp_headings_match_template_in_order() -> None:
    assert h2_headings(HR_EFVP) == h2_headings(TEMPLATE)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_document_carries_disclaimer(path: Path) -> None:
    assert DISCLAIMER in normalized_text(path).casefold()


@pytest.mark.parametrize("path", CLAIM_DOCS, ids=lambda p: p.name)
def test_document_makes_no_absolute_claim(path: Path) -> None:
    found = forbidden_claims(normalized_text(path))
    assert not found, f"{path.name} contains forbidden words: {found}"


@pytest.mark.parametrize(
    "text",
    [
        "Ce module est conforme à la Loi 25.",
        "Des politiques conformes.",
        "Une politique certifiée.",
        "Gabarit approuvé par la CAI.",
        "Politique approuvée par la CNESST.",
    ],
)
def test_forbidden_claims_are_caught(text: str) -> None:
    assert forbidden_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        "Conformité aux obligations",
        "L'inspecteur en soudage certifié vérifie les soudures.",
        "Aucun document n'affirme avoir été validé ou reconnu par la CAI.",
        "un organisme de certification",
    ],
)
def test_ordinary_wording_is_not_a_forbidden_claim(text: str) -> None:
    assert not forbidden_claims(text)
