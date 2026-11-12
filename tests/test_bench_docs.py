"""Guards on the module 4 documents (EFVP, ADRs 0022 to 0024) and on what the code
pre-registers with them (increment I1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from alverno_lab.evals import matrix as mx

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
)

DOCS_DIR = REPO_ROOT / "docs"
BENCH_EFVP = DOCS_DIR / "efvp" / "bench-module.md"
MODULE4_ADRS = tuple(
    path
    for path in sorted((DOCS_DIR / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    if 22 <= int(path.name[:4]) <= 24
)
ADR_0022, ADR_0023, ADR_0024 = MODULE4_ADRS
BENCH_DOCS = (BENCH_EFVP, *MODULE4_ADRS)
MODULE4_SRC = (REPO_ROOT / "src" / "alverno_lab" / "evals" / "matrix.py",)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- documents
def test_module4_documents_exist() -> None:
    assert BENCH_EFVP.is_file()
    assert [p.name[:4] for p in MODULE4_ADRS] == ["0022", "0023", "0024"]


def test_bench_efvp_headings_match_template_in_order() -> None:
    assert h2_headings(BENCH_EFVP) == h2_headings(TEMPLATE)


@pytest.mark.parametrize("path", BENCH_DOCS, ids=lambda p: p.name)
def test_document_carries_disclaimer(path: Path) -> None:
    assert DISCLAIMER in normalized_text(path).casefold()


@pytest.mark.parametrize("path", BENCH_DOCS, ids=lambda p: p.name)
def test_document_makes_no_absolute_claim(path: Path) -> None:
    found = forbidden_claims(normalized_text(path))
    assert not found, f"{path.name} contains forbidden words: {found}"


@pytest.mark.parametrize("path", BENCH_DOCS, ids=lambda p: p.name)
def test_legal_keys_exist(path: Path) -> None:
    # Module 4 adds no legal key: every key comes from the HR or the module 3 register.
    known = set(hr_register_keys()) | set(ops_new_keys())
    prefixes = "|".join(sorted(legal_key_prefixes()))
    bare = set(re.findall(rf"\b((?:{prefixes})_[a-z0-9_]+)\b", text(path)))
    unknown = sorted((cited_keys(path) | bare) - known)
    assert not unknown, f"{path.name} names unknown legal keys: {unknown}"


@pytest.mark.parametrize("path", BENCH_DOCS, ids=lambda p: p.name)
def test_relative_links_resolve(path: Path) -> None:
    broken = [
        target
        for target in relative_links(path)
        if not (path.parent / target.split("#", 1)[0]).exists()
    ]
    assert not broken, f"{path.name} has broken links: {broken}"


@pytest.mark.parametrize("path", BENCH_DOCS, ids=lambda p: p.name)
def test_link_anchors_resolve(path: Path) -> None:
    broken = [
        f"{target.name}#{anchor}"
        for target, anchor in anchored_links(path)
        if target.suffix == ".md" and target.exists() and anchor not in heading_anchors(target)
    ]
    assert not broken, f"{path.name} has broken anchors: {broken}"


def test_module4_code_exists_only_after_the_approval() -> None:
    present = [p for p in MODULE4_SRC if p.exists()]
    if not approved(BENCH_EFVP):
        assert not present, "module 4 code before the EFVP approval (ADR 0024, criterion 1)"
    for adr in MODULE4_ADRS:
        status = next(line for line in text(adr).splitlines() if line.startswith("- **Statut**"))
        assert "accepté" in status, adr.name


# --------------------------------------------------------------------------- pre-registration
def test_the_code_measures_the_models_of_adr_0022() -> None:
    adr = text(ADR_0022)
    for model in (*mx.CHAT_MODELS, *mx.T1_ONLY_MODELS, *mx.EMBED_MODELS):
        assert f"`{model}`" in adr, model
    assert "`wizardcoder`" in adr
    assert "wizardcoder" not in {*mx.CHAT_MODELS, *mx.T1_ONLY_MODELS, *mx.EMBED_MODELS}


def test_the_heavy_model_runs_on_t1_only() -> None:
    for task in mx.TASKS.values():
        chosen = mx.models_for(task, mx.CHAT_MODELS, mx.EMBED_MODELS, mx.T1_ONLY_MODELS)
        assert ("gemma4:26b" in chosen) is (task.id == "T1"), task.id


def test_the_tasks_are_those_of_adr_0022() -> None:
    adr = text(ADR_0022)
    profiles = {p for task in mx.TASKS.values() for p in task.profiles("ollama")}
    assert profiles == {
        "full-ollama",
        "hr-full-ollama",
        "svc-full-ollama",
        "maint-full-ollama",
        "dir-full-ollama",
        "retrieval-ollama",
        "hr-retrieval-ollama",
    }
    for profile in profiles:
        assert f"`{profile}`" in adr, profile


def test_every_profile_exists_in_both_backends() -> None:
    raw = yaml.safe_load((REPO_ROOT / "evals" / "thresholds.yaml").read_text(encoding="utf-8"))
    for task in mx.TASKS.values():
        for backend in ("ollama", "fake"):
            for profile in task.profiles(backend):
                assert profile in raw, profile


def test_tiers_margin_and_tie_are_those_of_the_adrs() -> None:
    assert mx.TIERS_GB == (8, 12, 16, 24)
    assert "8, 12, 16 et 24 Go" in text(ADR_0022)
    assert mx.TIER_MARGIN_GB == 1.0 and "plus 1 Go de marge" in text(ADR_0022)
    assert mx.TIE_GB == 0.5 and "à égalité à 0,5 Go près" in text(ADR_0024)
    assert mx.CONFIRM_K == 3 and "(k = 3 au total)" in text(ADR_0024)


def test_published_indexes_and_logs_are_never_committed() -> None:
    ignored = text(REPO_ROOT / ".gitignore").splitlines()
    assert "reports/**/index/" in ignored
    assert "logs/" in ignored and "reports/**/access/" in ignored
    assert set(mx.UNPUBLISHED) >= {"index", "access", "logs", "traces"}
