"""Guards on the module 6 documents (EFVP, ADRs 0025 to 0028, docs/m365/), written
before any module 6 code (increment I0)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import REPO_ROOT
from .test_direction_docs import approved
from .test_hr_docs import DISCLAIMER, TEMPLATE, forbidden_claims, h2_headings, normalized_text
from .test_ops_docs import (
    anchored_links,
    cited_keys,
    heading_anchors,
    hr_register_keys,
    legal_key_prefixes,
    ops_new_keys,
    relative_links,
    section,
    table_rows,
)

DOCS_DIR = REPO_ROOT / "docs"
M365_EFVP = DOCS_DIR / "efvp" / "m365-module.md"
MODULE6_ADRS = tuple(
    path
    for path in sorted((DOCS_DIR / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if 25 <= int(path.name[:4]) <= 28
)
M365_DOCS = tuple(sorted((DOCS_DIR / "m365").glob("*.md")))
LEGAL_DOCS = (M365_EFVP, *MODULE6_ADRS)
ALL_DOCS = (*LEGAL_DOCS, *M365_DOCS)
# Code that may exist only once the EFVP v1.0 is approved (ADR 0028, exit criterion 1).
MODULE6_SRC = (
    REPO_ROOT / "src" / "alverno_lab" / "m365",
    REPO_ROOT / "integrations" / "copilot-studio",
)
ADR_0028 = DOCS_DIR / "adr" / "0028-m365-evaluation-protocol.md"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_module6_documents_exist() -> None:
    assert M365_EFVP.is_file()
    assert [p.name[:4] for p in MODULE6_ADRS] == ["0025", "0026", "0027", "0028"]
    assert [p.name for p in M365_DOCS] == [
        "adoption-plan.md",
        "article-17.md",
        "comparison.md",
        "deployment-guide.md",
        "governance.md",
    ]


def test_m365_efvp_headings_match_template_in_order() -> None:
    assert h2_headings(M365_EFVP) == h2_headings(TEMPLATE)


@pytest.mark.parametrize("path", LEGAL_DOCS, ids=lambda p: p.name)
def test_document_carries_disclaimer(path: Path) -> None:
    assert DISCLAIMER in normalized_text(path).casefold()


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_document_makes_no_absolute_claim(path: Path) -> None:
    found = forbidden_claims(normalized_text(path))
    assert not found, f"{path.name} contains forbidden words: {found}"


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_legal_keys_exist(path: Path) -> None:
    # Module 6 adds no legal key: every key comes from the HR or the module 3 register.
    known = set(hr_register_keys()) | set(ops_new_keys())
    prefixes = "|".join(sorted(legal_key_prefixes()))
    bare = set(re.findall(rf"\b((?:{prefixes})_[a-z0-9_]+)\b", text(path)))
    unknown = sorted((cited_keys(path) | bare) - known)
    assert not unknown, f"{path.name} names unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    broken = [
        target
        for target in relative_links(path)
        if not (path.parent / target.split("#", 1)[0]).exists()
    ]
    assert not broken, f"{path.name} has broken links: {broken}"


@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_link_anchors_resolve(path: Path) -> None:
    broken = [
        f"{target.name}#{anchor}"
        for target, anchor in anchored_links(path)
        if target.suffix == ".md" and target.exists() and anchor not in heading_anchors(target)
    ]
    assert not broken, f"{path.name} has broken anchors: {broken}"


def test_no_module6_code_before_the_efvp_is_approved() -> None:
    if approved(M365_EFVP):
        pytest.skip("EFVP v1.0 approved: module 6 code is allowed")
    present = [str(p.relative_to(REPO_ROOT)) for p in MODULE6_SRC if p.exists()]
    assert not present, f"module 6 code before the EFVP approval: {present}"


def test_every_metric_the_efvp_cites_is_defined_in_adr_0028() -> None:
    defined = {
        row[0].strip("`")
        for row in table_rows(section(ADR_0028, "Profil `m365-fake`"))
        if row[0].startswith("`")
    }
    lines = [*section(M365_EFVP, "5. Risques"), *section(M365_EFVP, "B. Affirmations")]
    cited = {
        m
        for row in table_rows(lines)
        for m in re.findall(r"`([a-z]+(?:_[a-z0-9]+)+)`", row[-1] if len(row) == 3 else row[-2])
    }
    assert cited
    assert not sorted(cited - defined), sorted(cited - defined)


def test_the_profile_keeps_every_count_at_zero() -> None:
    rows = table_rows(section(ADR_0028, "Profil `m365-fake`"))
    bounds = {row[0].strip("`"): row[-1] for row in rows if row[0].startswith("`")}
    counts = [k for k in bounds if k.endswith(("_bypass", "_hits", "_reject", "_flags"))]
    assert counts and all(bounds[k] == "= 0" for k in counts)
    assert bounds["api_parity"] == "= 1,0" and bounds["n_items"] == "≥ 50"
