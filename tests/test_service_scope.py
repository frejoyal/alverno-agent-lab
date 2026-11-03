"""Scope words written with a space for a hyphen (I6 review fix; ADR 0012 « scope_check »).

A hyphenated label or compound of another account, written with a space (the usual way to
type it), is still another account's value: ``check_scope`` treats a hyphen and a space
as one separator inside a word value. The request path (``scope_check``), the draft path
(``guard``) and the edit path (``reguard``) of the real 3a rig refuse or withhold it. The
labels come from the ERP fixture and are mutated by code (hyphen to space), never typed
in. The scope wiring reads the account values through ``account_scope_index`` only, from
``ops_pipeline.py``. Assertion messages cite item ids and rules, never a fixture value.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path
from typing import cast

import pytest
import yaml

from alverno_lab.agents.ops import TimelineLog
from alverno_lab.config import Settings
from alverno_lab.connectors import SimErp
from alverno_lab.connectors.sim import account_scope_index, account_scope_values
from alverno_lab.evals.service import SvcGoldItem, load_svc_gold
from alverno_lab.evals.service_eval import _scope_value_in
from alverno_lab.ops_pipeline import build_service_rig, ops_quarantine, service_scope_index
from alverno_lab.privacy.scope import build_scope_index, check_scope

from .conftest import EVAL_DATA, REPO_ROOT, hr_repo_settings
from .ops_privacy_helpers import ops_privacy_ctx

OPS = EVAL_DATA / "ops"
SVC_GOLD = EVAL_DATA / "svc_gold.jsonl"
SRC = REPO_ROOT / "src" / "alverno_lab"
_QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")
_HYPHENATED = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")


def ops_settings() -> Settings:
    return hr_repo_settings(
        ops_erp_path=OPS / "erp.yaml",
        ops_gmao_path=OPS / "gmao.yaml",
        ops_vocabulary_path=OPS / "vocabulary.yaml",
        svc_eval_path=SVC_GOLD,
    )


@functools.cache
def erp() -> SimErp:
    return SimErp.from_yaml(OPS / "erp.yaml", quarantine=ops_quarantine(ops_privacy_ctx()))


@functools.cache
def gold() -> tuple[SvcGoldItem, ...]:
    return tuple(load_svc_gold(SVC_GOLD, erp()))


def first(family: str, steps: list[str]) -> SvcGoldItem:
    for item in gold():
        if item.family == family and [s.decision for s in item.approval_script] == steps:
            return item
    raise AssertionError(f"gold:no_{family}")


def _accounts() -> list[dict[str, object]]:
    raw: object = yaml.safe_load((OPS / "erp.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(list[dict[str, object]], raw["accounts"])


def spaced_label(not_of: str) -> str:
    """Another account's hyphenated label, its qualifier dropped, hyphens made spaces."""
    for account in sorted(_accounts(), key=lambda a: str(a["account_id"])):
        label = _QUALIFIER.sub("", str(account["label"]))
        if account["account_id"] != not_of and _HYPHENATED.search(label):
            return label.replace("-", " ")
    raise AssertionError("fixture:no_hyphenated_label")


def spaced_compound(not_of: str) -> str:
    """Another account's hyphenated compound alone, written with a space."""
    for account in sorted(_accounts(), key=lambda a: str(a["account_id"])):
        found = _HYPHENATED.search(_QUALIFIER.sub("", str(account["label"])))
        if account["account_id"] != not_of and found is not None:
            return found.group(0).replace("-", " ")
    raise AssertionError("fixture:no_hyphenated_compound")


def account_of(item: SvcGoldItem) -> str:
    assert item.order_id is not None, item.id
    view = erp().get_order(item.order_id)
    assert view is not None, item.id
    return view.account_id


# --------------------------------------------------------------------------- check_scope
SYNTHETIC = {
    "A-1": ["Remorques Pic-Vert", "Usinage Haut Fond"],
    "A-2": ["Ateliers Rive-Sud"],
    "A-3": ["-"],
}


@pytest.mark.parametrize(
    ("text", "in_scope", "flags"),
    [
        ("Copie aux Remorques Pic Vert.", "A-2", True),
        ("Copie aux Remorques PIC-VERT.", "A-2", True),
        ("Copie aux Remorques\nPic Vert.", "A-2", True),
        ("Copie à Usinage Haut-Fond.", "A-2", True),
        ("Copie aux Ateliers Rive Sud.", "A-1", True),
        ("Copie aux Ateliers Rive-Sudiste.", "A-1", False),
        ("Copie aux Remorques Pic Vert.", "A-1", False),
        ("Une note - sans valeur.", "A-1", False),
    ],
    ids=[
        "spaced",
        "hyphen_upper",
        "wrapped",
        "spaced_value_hyphen_text",
        "spaced_other",
        "longer_word",
        "in_scope",
        "lone_hyphen",
    ],
)
def test_a_hyphen_and_a_space_are_one_separator(text: str, in_scope: str, flags: bool) -> None:
    index = build_scope_index(SYNTHETIC)
    assert check_scope(text, in_scope, index).ok is not flags
    # The evaluation's own matcher agrees for word values.
    found = any(
        _scope_value_in("word", word, text)
        for word, owners in index.words.items()
        if in_scope not in owners
    )
    assert found is flags


def test_the_fixture_labels_flag_with_a_space(tmp_path: Path) -> None:
    index = service_scope_index(erp())
    for account in _accounts():
        own = str(account["account_id"])
        text = f"Copie à {spaced_label(own)}."
        assert not check_scope(text, own, index).ok, own
        assert not check_scope(f"Copie à {spaced_compound(own)}.", own, index).ok, own


# --------------------------------------------------------------------------- the real rig
def rig(tmp_path: Path) -> TimelineLog:
    return TimelineLog(tmp_path / "access", prefix="service")


def test_a_request_naming_another_account_with_a_space_is_refused(tmp_path: Path) -> None:
    settings = ops_settings()
    for family in ("order_status", "customer_reply"):
        item = first(family, [] if family == "order_status" else ["approve"])
        built = build_service_rig(
            settings, mode="tools", fake=True, log=rig(tmp_path), channel="eval"
        )
        request = f"{item.request} Même chose que pour {spaced_label(account_of(item))}."
        turn = built.envelope.start(request)
        assert (turn.kind, turn.reason) == ("refused", "cross_account"), item.id
        assert not built.envelope.pending, item.id


def test_an_edit_naming_another_account_with_a_space_is_withheld(tmp_path: Path) -> None:
    item = first("customer_reply", ["approve"])
    built = build_service_rig(
        ops_settings(), mode="tools", fake=True, log=rig(tmp_path), channel="eval"
    )
    turn = built.envelope.start(item.request)
    assert turn.kind == "pending" and turn.text is not None, item.id
    edited = f"{turn.text}\n\nCopie à {spaced_compound(account_of(item))}."
    ended = built.envelope.decide(
        {"decision": "edit", "edited_text": edited}, draft_id=turn.draft_id
    )
    assert (ended.kind, ended.reason) == ("withheld", "cross_account"), item.id
    assert built.sims[0].executed == [], item.id


# --------------------------------------------------------------------------- structure
def _names_in(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias | ast.FunctionDef):
            names.add(node.name)
    return names


def test_only_the_scope_wiring_reads_the_account_scope_index() -> None:
    users = {
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if "account_scope_index" in _names_in(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert users == {"connectors/sim.py", "ops_pipeline.py"}
    import alverno_lab.connectors as connectors

    assert not hasattr(connectors, "account_scope_index")


def test_the_scope_index_is_built_from_the_account_values() -> None:
    built = account_scope_index(erp(), build_scope_index)
    assert built == build_scope_index(account_scope_values(erp()))
    assert account_scope_index(erp(), dict) == account_scope_values(erp())
