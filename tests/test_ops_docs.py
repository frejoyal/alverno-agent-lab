"""Checks on the module 3 privacy impact assessment (EFVP), its legal register and
ADRs 0012 to 0015, written before any module 3 code (increment I0)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

from .conftest import REPO_ROOT
from .test_hr_docs import DISCLAIMER, TEMPLATE, forbidden_claims, h2_headings, normalized_text

DOCS_DIR = REPO_ROOT / "docs"
OPS_EFVP = DOCS_DIR / "efvp" / "ops-module.md"
OPS_REGISTER = DOCS_DIR / "ops-legal-sources.md"
HR_REGISTER = DOCS_DIR / "hr-legal-sources.md"
ADR_0015 = DOCS_DIR / "adr" / "0015-ops-evaluation-protocol.md"
MODULE3_ADRS = tuple(
    path
    for path in sorted((DOCS_DIR / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if 12 <= int(path.name[:4]) <= 15
)
OPS_DOCS = (OPS_EFVP, OPS_REGISTER, *MODULE3_ADRS)

FENCE = re.compile(r"^(```|~~~)")
HEADING = re.compile(r"^(#{1,6}) ")
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.IGNORECASE)
URL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
# GitHub drops every character that is not a letter, a digit, a space, "-" or "_".
SLUG_DROP = re.compile(r"[^\w\- ]")
TABLE_KEY = re.compile(r"^\|\s*([a-z][a-z0-9]*_[a-z0-9_]+)\s*\|")
BACKTICKED = re.compile(r"`([a-z][a-z0-9]*_[a-z0-9_]+)`")
BOUND = re.compile(r"`([a-z0-9_]+)`\s*([≥≤])\s*(\d+(?:,\d+)?)")
PRECONDITION = re.compile(r"`(n_[a-z_]+)`\s+(\d+)")
FALSE_BLOCK = re.compile(r"`false_block_rate`[^.]*?(\d+,\d+)")
# HR register tables keyed by a YAML field, not by a legal key.
HR_NON_LEGAL_SECTIONS = ("Planchers vérifiés par test",)


def body_lines(path: Path) -> list[str]:
    """Return the lines of a Markdown file outside fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def sections(path: Path) -> dict[str, list[str]]:
    """Map each heading title to its lines, up to the next heading of the same or higher level."""
    lines = body_lines(path)
    result: dict[str, list[str]] = {}
    for start, line in enumerate(lines):
        match = HEADING.match(line)
        if not match:
            continue
        level = len(match.group(1))
        end = len(lines)
        for index in range(start + 1, len(lines)):
            other = HEADING.match(lines[index])
            if other and len(other.group(1)) <= level:
                end = index
                break
        result[line[level + 1 :].strip()] = lines[start + 1 : end]
    return result


def section(path: Path, prefix: str) -> list[str]:
    """Return the lines of the single section whose title starts with ``prefix``."""
    found = [lines for title, lines in sections(path).items() if title.startswith(prefix)]
    assert len(found) == 1, f"{path.name}: expected one section starting with {prefix!r}"
    return found[0]


def table_rows(lines: list[str]) -> list[list[str]]:
    """Return the cells of every Markdown table row, header and separator rows excluded."""
    rows: list[list[str]] = []
    for line in lines:
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|$", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] in {"Clé", "Métrique", "Précondition", "Profil", "Jeu"}:
            continue
        rows.append(cells)
    return rows


def table_keys(lines: list[str]) -> list[str]:
    return [m.group(1) for line in lines if (m := TABLE_KEY.match(line))]


def hr_register_keys() -> list[str]:
    keys: list[str] = []
    for title, lines in sections(HR_REGISTER).items():
        if title in HR_NON_LEGAL_SECTIONS:
            continue
        # Keep only the lines before the first sub-heading, so no table is read twice.
        own = next((i for i, line in enumerate(lines) if HEADING.match(line)), len(lines))
        keys.extend(table_keys(lines[:own]))
    return keys


def ops_new_keys() -> list[str]:
    return table_keys(section(OPS_REGISTER, "Clés nouvelles du module 3"))


def ops_referenced_hr_keys() -> list[str]:
    return table_keys(section(OPS_REGISTER, "Clés RH reprises par renvoi"))


def legal_key_prefixes() -> set[str]:
    return {key.split("_", 1)[0] for key in (*hr_register_keys(), *ops_new_keys())}


def cited_keys(path: Path) -> set[str]:
    prefixes = legal_key_prefixes()
    text = "\n".join(body_lines(path))
    return {key for key in BACKTICKED.findall(text) if key.split("_", 1)[0] in prefixes}


def relative_links(path: Path) -> list[str]:
    text = INLINE_CODE.sub(" ", "\n".join(body_lines(path)))
    return [target for target in LINK.findall(text) if not EXTERNAL.match(target)]


def github_slug(heading: str) -> str:
    """Return the anchor GitHub derives from a heading title, before duplicate suffixes."""
    return SLUG_DROP.sub("", heading.strip().lower()).replace(" ", "-")


def heading_anchors(path: Path) -> set[str]:
    """Return every heading anchor of a Markdown file, with GitHub's ``-1``, ``-2`` suffixes."""
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for line in body_lines(path):
        if match := HEADING.match(line):
            slug = github_slug(line[len(match.group(1)) + 1 :].rstrip("#"))
            count = seen.get(slug, 0)
            anchors.add(slug if count == 0 else f"{slug}-{count}")
            seen[slug] = count + 1
    return anchors


def anchored_links(path: Path) -> list[tuple[Path, str]]:
    """Return (target file, anchor) for every relative or internal link that has an anchor."""
    text = INLINE_CODE.sub(" ", "\n".join(body_lines(path)))
    found: list[tuple[Path, str]] = []
    for target in LINK.findall(text):
        if "#" not in target or URL.match(target):
            continue
        file_part, anchor = target.split("#", 1)
        found.append((path.parent / file_part if file_part else path, unquote(anchor)))
    return found


def backticked_names(lines: list[str], marker: str) -> set[str]:
    """Return the backticked names of the paragraph line that starts with ``marker``."""
    found = [line for line in lines if line.startswith(marker)]
    assert len(found) == 1, f"expected one line starting with {marker!r}"
    return set(re.findall(r"`([a-z0-9_]+)`", found[0].split(".", 1)[1]))


def first_cell_names(lines: list[str]) -> set[str]:
    return {row[0].strip("`") for row in table_rows(lines) if row[0].startswith("`")}


def number(text: str) -> float:
    return float(text.replace(",", "."))


Bounds = dict[tuple[str, str], tuple[str, float]]


def efvp_annex_c() -> list[str]:
    return section(OPS_EFVP, "C. Bornes de la porte")


def efvp_service_bounds() -> Bounds:
    bounds: Bounds = {}
    for row in table_rows(efvp_annex_c()):
        if row[0].startswith("`") and row[0].endswith("-full-ollama`"):
            for metric, op, value in BOUND.findall(row[1]):
                bounds[(row[0].strip("`"), metric)] = (op, number(value))
    return bounds


def adr_service_bounds() -> Bounds:
    bounds: Bounds = {}
    for row in table_rows(section(ADR_0015, "Profils et bornes")):
        if len(row) == 3 and row[0].endswith("-full-ollama`"):
            for metric, op, value in BOUND.findall(row[1]):
                bounds[(row[0].strip("`"), metric)] = (op, number(value))
    return bounds


def efvp_tools_fake_bounds() -> Bounds:
    bounds: Bounds = {}
    for row in table_rows(efvp_annex_c()):
        if row[0].startswith("`") and row[0].endswith("-tools-fake`"):
            for metric, op, value in BOUND.findall(row[1]):
                bounds[(row[0].strip("`"), metric)] = (op, number(value))
    return bounds


def adr_tools_fake_bounds() -> Bounds:
    """Parse cells such as "`a`, `b` `min` 1,0 ; `c` `max` 0,0" of the ADR 0015 profile table."""
    bounds: Bounds = {}
    for row in table_rows(section(ADR_0015, "Profils et bornes")):
        if len(row) != 6 or not row[0].endswith("-tools-fake`"):
            continue
        for part in row[5].split(";"):
            limit = re.search(r"`(min|max)`\s*(\d+(?:,\d+)?)", part)
            assert limit, f"unparsed bound {part!r}"
            op = "≥" if limit.group(1) == "min" else "≤"
            for metric in re.findall(r"`([a-z0-9_]+)`", part[: limit.start()]):
                bounds[(row[0].strip("`"), metric)] = (op, number(limit.group(2)))
    return bounds


def efvp_preconditions() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for row in table_rows(efvp_annex_c()):
        if row[0] in {"svc", "maint"}:
            result[row[0]] = {name: int(value) for name, value in PRECONDITION.findall(row[1])}
    return result


def adr_preconditions() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {"svc": {}, "maint": {}}
    for row in table_rows(section(ADR_0015, "Préconditions exactes")):
        name = row[0].strip("`")
        for subset, cell in (("svc", row[1]), ("maint", row[2])):
            if match := re.match(r"\d+", cell):
                result[subset][name] = int(match.group(0))
    return result


def test_module3_documents_exist() -> None:
    for path in (OPS_EFVP, OPS_REGISTER):
        assert path.is_file(), f"missing {path.relative_to(REPO_ROOT)}"
    assert [p.name[:4] for p in MODULE3_ADRS] == ["0012", "0013", "0014", "0015"]


def test_ops_efvp_headings_match_template_in_order() -> None:
    assert h2_headings(OPS_EFVP) == h2_headings(TEMPLATE)


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_document_carries_disclaimer(path: Path) -> None:
    assert DISCLAIMER in normalized_text(path).casefold()


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_document_makes_no_absolute_claim(path: Path) -> None:
    found = forbidden_claims(normalized_text(path))
    assert not found, f"{path.name} contains forbidden words: {found}"


def test_register_keys_are_unique_across_both_registers() -> None:
    hr_keys = hr_register_keys()
    new_keys = ops_new_keys()
    assert hr_keys and new_keys
    assert len(set(hr_keys)) == len(hr_keys), "duplicate key in the HR register"
    assert len(set(new_keys)) == len(new_keys), "duplicate key in the module 3 register"
    assert not set(new_keys) & set(hr_keys), "module 3 key already defined in the HR register"


def test_referenced_hr_keys_exist_in_hr_register() -> None:
    referenced = ops_referenced_hr_keys()
    assert referenced
    assert len(set(referenced)) == len(referenced)
    assert not set(referenced) - set(hr_register_keys())


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_cited_legal_keys_exist(path: Path) -> None:
    known = set(hr_register_keys()) | set(ops_new_keys())
    unknown = sorted(cited_keys(path) - known)
    assert not unknown, f"{path.name} cites unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_bare_legal_keys_exist(path: Path) -> None:
    """Keys written without backticks, as in the register's hypotheses table, exist too."""
    known = set(hr_register_keys()) | set(ops_new_keys())
    prefixes = "|".join(sorted(legal_key_prefixes()))
    bare = set(re.findall(rf"\b((?:{prefixes})_[a-z0-9_]+)\b", path.read_text(encoding="utf-8")))
    unknown = sorted(bare - known)
    assert not unknown, f"{path.name} names unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    broken = [
        target
        for target in relative_links(path)
        if not (path.parent / target.split("#", 1)[0]).exists()
    ]
    assert not broken, f"{path.name} has broken links: {broken}"


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_link_anchors_resolve(path: Path) -> None:
    broken = [
        f"{target.name}#{anchor}"
        for target, anchor in anchored_links(path)
        if target.suffix == ".md" and target.exists() and anchor not in heading_anchors(target)
    ]
    assert not broken, f"{path.name} has broken anchors: {broken}"


@pytest.mark.parametrize("path", OPS_DOCS, ids=lambda p: p.name)
def test_adr_0016_is_not_linked(path: Path) -> None:
    linked = [target for target in LINK.findall("\n".join(body_lines(path))) if "0016" in target]
    assert not linked, f"{path.name} links ADR 0016, which is not written yet: {linked}"


def test_annex_c_safety_counts_match_adr_0015() -> None:
    efvp = backticked_names(efvp_annex_c(), "**Comptes de sécurité")
    adr = first_cell_names(section(ADR_0015, "Comptes de sécurité"))
    assert len(adr) == 23
    assert efvp == adr


def test_annex_c_control_rates_match_adr_0015() -> None:
    efvp = backticked_names(efvp_annex_c(), "**Taux")
    adr = first_cell_names(section(ADR_0015, "Taux de contrôle"))
    assert len(adr) == 7
    assert efvp == adr


def test_annex_c_false_block_bound_matches_adr_0015() -> None:
    efvp = FALSE_BLOCK.findall(" ".join(efvp_annex_c()))
    adr = FALSE_BLOCK.findall(" ".join(section(ADR_0015, "Taux de contrôle")))
    assert efvp and adr
    assert number(efvp[0]) == number(adr[0]) == 0.0


def test_annex_c_preconditions_match_adr_0015() -> None:
    efvp = efvp_preconditions()
    assert set(efvp) == {"svc", "maint"}
    assert efvp == adr_preconditions()


def test_annex_c_service_bounds_match_adr_0015() -> None:
    efvp = efvp_service_bounds()
    assert len(efvp) == 8
    assert efvp == adr_service_bounds()


def test_annex_c_tools_fake_bounds_match_adr_0015() -> None:
    efvp = efvp_tools_fake_bounds()
    assert len(efvp) == 5
    assert efvp == adr_tools_fake_bounds()


def test_github_slug_and_duplicate_anchors(tmp_path: Path) -> None:
    assert (
        github_slug("7. Avis du responsable, décision et approbation")
        == "7-avis-du-responsable-décision-et-approbation"
    )
    assert (
        github_slug("Empreinte des arguments : un risque résiduel accepté")
        == "empreinte-des-arguments--un-risque-résiduel-accepté"
    )
    assert github_slug("`executed_external_send: Literal[False]`") == (
        "executed_external_send-literalfalse"
    )
    doc = tmp_path / "doc.md"
    doc.write_text(
        "# Titre\n## Préconditions\n## Préconditions\n```\n## pas un titre\n```\n"
        "Voir [a](#préconditions-1), [b](#absente) et [c](https://example.com#x).\n",
        encoding="utf-8",
    )
    assert heading_anchors(doc) == {"titre", "préconditions", "préconditions-1"}
    assert anchored_links(doc) == [(doc, "préconditions-1"), (doc, "absente")]


def test_helpers_parse_links_and_keys(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(
        "Voir [a](a.md#x), [b](https://example.com), [c](#ancre) et `[d](d.md)`.\n"
        "```\n[e](e.md) `lprp_absente`\n```\n"
        "Clé `lprp_gouvernance` et identifiant `draft_id`.\n",
        encoding="utf-8",
    )
    assert relative_links(doc) == ["a.md#x"]
    assert cited_keys(doc) == {"lprp_gouvernance"}
