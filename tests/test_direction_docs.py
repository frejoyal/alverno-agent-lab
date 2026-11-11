"""Checks on the module 5 privacy impact assessment (EFVP) and ADRs 0017 to 0020, written
before any module 5 code (increment I0)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import REPO_ROOT
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
DIR_EFVP = DOCS_DIR / "efvp" / "direction-module.md"
MODULE5_ADRS = tuple(
    path
    for path in sorted((DOCS_DIR / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if 17 <= int(path.name[:4]) <= 21
)
ADR_0020 = DOCS_DIR / "adr" / "0020-direction-evaluation-protocol.md"
DIR_DOCS = (DIR_EFVP, *MODULE5_ADRS)
# Packages that may exist only once the EFVP v1.0 is approved (ADR 0020, exit criterion 1).
MODULE5_SRC = (
    REPO_ROOT / "src" / "alverno_lab" / "mcp_server",
    REPO_ROOT / "src" / "alverno_lab" / "agents" / "direction",
)
METRIC = re.compile(r"`([a-z]+(?:_[a-z0-9]+)+)`")


def adr_0020_metrics() -> set[str]:
    """Metric names in the first column of the ADR 0020 measurement tables."""
    names: set[str] = set()
    for prefix in ("5a — Jeux et mesures", "5b — Mesures"):
        for row in table_rows(section(ADR_0020, prefix)):
            if row[0].startswith("`"):
                names.add(row[0].strip("`"))
    return names


def efvp_metrics() -> set[str]:
    """Metric names the EFVP cites as proof (risk table and annex B)."""
    lines = [*section(DIR_EFVP, "5. Risques"), *section(DIR_EFVP, "B. Affirmations")]
    found: set[str] = set()
    for row in table_rows(lines):
        found |= set(METRIC.findall(row[-2] if len(row) == 5 else row[-1]))
    return found


def approved(efvp: Path) -> bool:
    rows = {row[0]: row[1] for row in table_rows(section(efvp, "Approbation de la haute"))}
    return not rows["Approbation de la version 1.0"].startswith("En attente")


def test_module5_documents_exist() -> None:
    assert DIR_EFVP.is_file()
    # ADR 0021 (calibration of the dir-*-fake profiles) joined on 2026-11-11, step I6.
    assert [p.name[:4] for p in MODULE5_ADRS] == ["0017", "0018", "0019", "0020", "0021"]


def test_direction_efvp_headings_match_template_in_order() -> None:
    assert h2_headings(DIR_EFVP) == h2_headings(TEMPLATE)


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_document_carries_disclaimer(path: Path) -> None:
    assert DISCLAIMER in normalized_text(path).casefold()


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_document_makes_no_absolute_claim(path: Path) -> None:
    found = forbidden_claims(normalized_text(path))
    assert not found, f"{path.name} contains forbidden words: {found}"


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_cited_legal_keys_exist(path: Path) -> None:
    # Module 5 adds no legal key: every key comes from the HR or the module 3 register.
    known = set(hr_register_keys()) | set(ops_new_keys())
    unknown = sorted(cited_keys(path) - known)
    assert not unknown, f"{path.name} cites unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_bare_legal_keys_exist(path: Path) -> None:
    known = set(hr_register_keys()) | set(ops_new_keys())
    prefixes = "|".join(sorted(legal_key_prefixes()))
    bare = set(re.findall(rf"\b((?:{prefixes})_[a-z0-9_]+)\b", path.read_text(encoding="utf-8")))
    unknown = sorted(bare - known)
    assert not unknown, f"{path.name} names unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    broken = [
        target
        for target in relative_links(path)
        if not (path.parent / target.split("#", 1)[0]).exists()
    ]
    assert not broken, f"{path.name} has broken links: {broken}"


@pytest.mark.parametrize("path", DIR_DOCS, ids=lambda p: p.name)
def test_link_anchors_resolve(path: Path) -> None:
    broken = [
        f"{target.name}#{anchor}"
        for target, anchor in anchored_links(path)
        if target.suffix == ".md" and target.exists() and anchor not in heading_anchors(target)
    ]
    assert not broken, f"{path.name} has broken anchors: {broken}"


def test_every_metric_the_efvp_cites_is_defined_in_adr_0020() -> None:
    cited = efvp_metrics()
    assert cited
    # Service metrics of the orchestrator are defined in their own table.
    defined = adr_0020_metrics() | {"count_fidelity", "trap_refusal_rate"}
    unknown = sorted(cited - defined)
    assert not unknown, f"EFVP cites metrics ADR 0020 does not define: {unknown}"


def test_no_module5_code_before_the_efvp_is_approved() -> None:
    if approved(DIR_EFVP):
        pytest.skip("EFVP v1.0 approved: module 5 code is allowed")
    present = [str(p.relative_to(REPO_ROOT)) for p in MODULE5_SRC if p.exists()]
    assert not present, f"module 5 code before the EFVP approval: {present}"
