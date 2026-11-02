"""Module 3 tools (I3 spec decisions 8 to 10, ADR 0012 and ADR 0013).

The registry (names, kinds, args models, purposes, fixed plans), the single-use HMAC
approval tokens and the executor that runs reads and approved writes against the
simulated ERP and GMAO of ``data/eval/ops/``. Only the fixtures and synthetic strings are
read here, never a holdout. Assertion messages cite tool names and rule names, never a
fixture value.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import itertools
import json
import pickle
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import cast, get_args

import pytest
from pydantic import BaseModel, ValidationError

from alverno_lab.connectors import (
    CATALOGUE_CODES,
    AssetId,
    AssetView,
    ConnectorError,
    ContactView,
    FailureCodeView,
    OrderId,
    OrderView,
    QuarantinedText,
    ReplyPrepared,
    SimErp,
    SimGmao,
    SymptomCode,
    WorkOrderView,
    WorkRequest,
    WriteReceipt,
    WriteToken,
    WriteToolName,
)
from alverno_lab.connectors import args_sha256 as connectors_args_sha256
from alverno_lab.connectors import canonical_json as connectors_canonical_json
from alverno_lab.privacy.ops_types import Flow
from alverno_lab.tools import (
    FLOW_PLANS,
    READ_TOOLS,
    TOOL_KINDS,
    TOOL_NAMES,
    TOOLS,
    UNKNOWN_TOOL,
    WRITE_TOOLS,
    ApprovalAuthority,
    ApprovalError,
    ApprovalToken,
    CreateWorkRequestArgs,
    FailureCatalogArgs,
    GetAssetArgs,
    GetOrderArgs,
    ListWorkOrdersArgs,
    RecordReplyPreparedArgs,
    ToolCallRecord,
    ToolExecutor,
    ToolKind,
    ToolName,
    ToolResult,
    args_sha256,
    canonical_json,
    raw_args_sha256,
)
from alverno_lab.tools.authority import KEY_BYTES

from .conftest import EVAL_DATA, REPO_ROOT

OPS = EVAL_DATA / "ops"
ERP_PATH = OPS / "erp.yaml"
GMAO_PATH = OPS / "gmao.yaml"
ADR_0012 = REPO_ROOT / "docs" / "adr" / "0012-ops-architecture-typed-tools.md"
TOOLS_SRC = REPO_ROOT / "src" / "alverno_lab" / "tools"
ACTION_ID = "0123456789abcdef0123456789abcdef"
OTHER_ACTION_ID = "fedcba9876543210fedcba9876543210"
DRAFT_SHA = hashlib.sha256(b"brouillon approuve").hexdigest()
OTHER_DRAFT_SHA = hashlib.sha256(b"brouillon modifie").hexdigest()
TEST_KEY = hashlib.sha256(b"cle de test du labo").digest()
RECORD_REPLY: WriteToolName = "erp.record_reply_prepared"
CREATE_WORK_REQUEST: WriteToolName = "gmao.create_work_request"


def stub_quarantine(text: str) -> QuarantinedText:
    """Stub quarantine: a fixed placeholder, so no fixture text reaches a view."""
    return QuarantinedText(text="texte mis en quarantaine", flags=("stub",))


def make_erp() -> SimErp:
    return SimErp.from_yaml(ERP_PATH, quarantine=stub_quarantine)


def make_gmao() -> SimGmao:
    return SimGmao.from_yaml(GMAO_PATH, quarantine=stub_quarantine)


class Setup:
    """A fresh executor over fresh simulated connectors, with fixture ids picked by rule."""

    def __init__(self, authority: ApprovalAuthority | None = None) -> None:
        self.erp = make_erp()
        self.gmao = make_gmao()
        self.authority = authority or ApprovalAuthority()
        self.executor = ToolExecutor(self.erp, self.gmao, self.authority.verifier)
        self.order_id = sorted(self.erp.order_ids)[0]
        histories = {
            asset: self.gmao.list_work_orders(asset, None, since_days=100_000, limit=12)
            for asset in sorted(self.gmao.asset_ids)
        }
        assets = [asset for asset, rows in histories.items() if len(rows) >= 2]
        self.asset_id = assets[0]
        self.wo_ids = tuple(wo.wo_id for wo in histories[self.asset_id][:2])
        self.foreign_wo_id = histories[assets[1]][0].wo_id

    def reply_args(self, draft_sha256: str = DRAFT_SHA) -> dict[str, object]:
        return {"order_id": self.order_id, "draft_sha256": draft_sha256}

    def work_request_args(self, failure_code: str = "HYD-JOINT") -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "symptom_code": "SYM-FUITE",
            "failure_code": failure_code,
            "cited_wo_ids": list(self.wo_ids),
        }

    def approve(self, tool: WriteToolName, args: Mapping[str, object]) -> ApprovalToken:
        """Mint a token as the envelope will: for the digest of the validated args."""
        model = TOOLS[tool].args_model.model_validate(dict(args))
        return self.authority.mint(ACTION_ID, tool, args_sha256(model))


@pytest.fixture
def setup() -> Setup:
    return Setup()


# --------------------------------------------------------------------------- registry
def test_no_tool_name_or_kind_speaks_of_sending() -> None:
    names = [*get_args(ToolName), *get_args(ToolKind)]
    assert len(names) == 8
    for name in names:
        lowered = name.lower()
        for word in ("send", "mail", "email"):
            assert word not in lowered, name


def test_tool_names_follow_the_adr_order() -> None:
    text = ADR_0012.read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if "`ToolName`, un `Literal` de 6 noms" in row)
    listed = re.findall(r"`([a-z]+\.[a-z_]+)`", line)
    assert tuple(listed) == TOOL_NAMES == get_args(ToolName)
    assert tuple(TOOLS) == TOOL_NAMES
    assert TOOL_KINDS == ("read", "write_internal")


def test_exactly_two_write_tools() -> None:
    assert len(WRITE_TOOLS) == 2
    assert frozenset(get_args(WriteToolName)) == WRITE_TOOLS
    assert {"erp.record_reply_prepared", "gmao.create_work_request"} == WRITE_TOOLS
    assert len(READ_TOOLS) == 4
    assert frozenset(TOOL_NAMES) == READ_TOOLS | WRITE_TOOLS
    assert not READ_TOOLS & WRITE_TOOLS
    for name, spec in TOOLS.items():
        assert spec.name == name
        assert (spec.kind == "write_internal") == (name in WRITE_TOOLS)


def _adr_flow_plans() -> dict[str, tuple[str, ...]]:
    lines = ADR_0012.read_text(encoding="utf-8").splitlines()
    start = lines.index("| Fin | Outils de lecture |")
    plans: dict[str, tuple[str, ...]] = {}
    for row in lines[start + 2 :]:
        if not row.startswith("|"):
            break
        match = re.fullmatch(r"\| `(\w+)` \| (.+) \|", row)
        assert match, row
        flow, cell = match.groups()
        same = re.fullmatch(r"les mêmes que `(\w+)`", cell)
        plans[flow] = plans[same.group(1)] if same else tuple(re.findall(r"`([\w.]+)`", cell))
    return plans


def test_flow_plans_equal_the_adr_table() -> None:
    expected = {
        "order_status": ("erp.get_order",),
        "customer_reply": ("erp.get_order",),
        "diagnosis": ("gmao.get_asset", "gmao.list_work_orders", "gmao.failure_catalog"),
        "work_request": ("gmao.get_asset", "gmao.list_work_orders", "gmao.failure_catalog"),
    }
    assert dict(FLOW_PLANS) == expected == _adr_flow_plans()
    assert set(FLOW_PLANS) == set(get_args(Flow))
    for flow, plan in FLOW_PLANS.items():
        for name in plan:
            assert name in READ_TOOLS, (flow, name)
            assert flow in TOOLS[name].purpose, (flow, name)


def test_specs_bind_each_tool_to_a_model_a_purpose_and_a_level() -> None:
    expected: dict[str, tuple[type[BaseModel], tuple[str, ...], int]] = {
        "erp.get_order": (GetOrderArgs, ("order_status", "customer_reply"), 2),
        "gmao.get_asset": (GetAssetArgs, ("diagnosis", "work_request"), 1),
        "gmao.list_work_orders": (ListWorkOrdersArgs, ("diagnosis", "work_request"), 4),
        "gmao.failure_catalog": (FailureCatalogArgs, ("diagnosis", "work_request"), 1),
        "erp.record_reply_prepared": (RecordReplyPreparedArgs, ("customer_reply",), 2),
        "gmao.create_work_request": (CreateWorkRequestArgs, ("work_request",), 2),
    }
    for name, (model, purpose, level) in expected.items():
        spec = TOOLS[name]  # type: ignore[index]
        assert (spec.args_model, spec.purpose, spec.max_level) == (model, purpose, level), name
        assert spec.args_model.model_config.get("extra") == "forbid", name
        assert spec.args_model.model_config.get("frozen") is True, name
    with pytest.raises(TypeError):
        TOOLS["erp.get_order"] = TOOLS["erp.get_order"]  # type: ignore[index]
    with pytest.raises(TypeError):
        FLOW_PLANS["diagnosis"] = ()  # type: ignore[index]


# --------------------------------------------------------------------------- args models
VALID_ARGS: dict[str, dict[str, object]] = {
    "erp.get_order": {"order_id": "CMD-2026-0412"},
    "gmao.get_asset": {"asset_id": "EQ-PLI-01"},
    "gmao.list_work_orders": {"asset_id": "EQ-PLI-01", "symptom_code": None},
    "gmao.failure_catalog": {},
    "erp.record_reply_prepared": {"order_id": "CMD-2026-0412", "draft_sha256": DRAFT_SHA},
    "gmao.create_work_request": {
        "asset_id": "EQ-PLI-01",
        "symptom_code": "SYM-FUITE",
        "failure_code": "INDETERMINE",
        "cited_wo_ids": ["OT-2026-0001"],
    },
}


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_args_models_accept_valid_args_and_reject_extra_keys(name: ToolName) -> None:
    model = TOOLS[name].args_model
    valid = model.model_validate(VALID_ARGS[name])
    with pytest.raises(ValidationError):
        model.model_validate({**VALID_ARGS[name], "recipient": "x"})
    with pytest.raises(ValidationError):
        valid.__setattr__("extra", 1)


BAD_ARGS: list[tuple[str, str, dict[str, object]]] = [
    ("no_prefix", "erp.get_order", {"order_id": "2026-0412"}),
    ("short", "erp.get_order", {"order_id": "CMD-2026-041"}),
    ("lowercase", "erp.get_order", {"order_id": "cmd-2026-0412"}),
    ("trailing_newline", "erp.get_order", {"order_id": "CMD-2026-0412\n"}),
    # Leading junk: the patterns are anchored at both ends.
    ("leading_junk", "erp.get_order", {"order_id": "xCMD-2026-0412"}),
    ("asset_leading_junk", "gmao.get_asset", {"asset_id": "aEQ-PLI-01"}),
    ("arabic_digits", "erp.get_order", {"order_id": "CMD-٢٠٢٦-0412"}),
    ("int_id", "erp.get_order", {"order_id": 20260412}),
    ("missing", "erp.get_order", {}),
    ("bad_asset", "gmao.get_asset", {"asset_id": "EQ-PL-01"}),
    ("bad_symptom", "gmao.list_work_orders", {"asset_id": "EQ-PLI-01", "symptom_code": "X"}),
    ("symptom_missing", "gmao.list_work_orders", {"asset_id": "EQ-PLI-01"}),
    (
        "limit_high",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "limit": 13},
    ),
    (
        "limit_zero",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "limit": 0},
    ),
    (
        "limit_string",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "limit": "8"},
    ),
    (
        "limit_bool",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "limit": True},
    ),
    (
        "since_zero",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "since_days": 0},
    ),
    (
        "since_string",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "since_days": "548"},
    ),
    (
        "since_bool",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "since_days": True},
    ),
    ("catalog_arg", "gmao.failure_catalog", {"asset_id": "EQ-PLI-01"}),
    (
        "sha_upper",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": DRAFT_SHA.upper()},
    ),
    (
        "sha_short",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": DRAFT_SHA[:-1]},
    ),
    (
        "sha_leading_junk",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": "x" + DRAFT_SHA},
    ),
    (
        "sha_long",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": DRAFT_SHA + "0"},
    ),
    (
        "draft_text",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": DRAFT_SHA, "draft": "Bonjour"},
    ),
    (
        "bad_failure",
        "gmao.create_work_request",
        {**VALID_ARGS["gmao.create_work_request"], "failure_code": "ERREUR-HUMAINE"},
    ),
    (
        "no_citation",
        "gmao.create_work_request",
        {**VALID_ARGS["gmao.create_work_request"], "cited_wo_ids": []},
    ),
    (
        "five_citations",
        "gmao.create_work_request",
        {
            **VALID_ARGS["gmao.create_work_request"],
            "cited_wo_ids": [f"OT-2026-000{i}" for i in range(1, 6)],
        },
    ),
    (
        "duplicate_citation",
        "gmao.create_work_request",
        {
            **VALID_ARGS["gmao.create_work_request"],
            "cited_wo_ids": ["OT-2026-0001", "OT-2026-0001"],
        },
    ),
    (
        "bad_citation",
        "gmao.create_work_request",
        {**VALID_ARGS["gmao.create_work_request"], "cited_wo_ids": ["OT-26-1"]},
    ),
    (
        "citation_leading_junk",
        "gmao.create_work_request",
        {**VALID_ARGS["gmao.create_work_request"], "cited_wo_ids": ["xOT-2026-0001"]},
    ),
    (
        "description",
        "gmao.create_work_request",
        {**VALID_ARGS["gmao.create_work_request"], "description": "fuite"},
    ),
]


@pytest.mark.parametrize(("case", "name", "args"), BAD_ARGS, ids=[c[0] for c in BAD_ARGS])
def test_args_models_reject_bad_ids_and_codes(
    case: str, name: ToolName, args: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        TOOLS[name].args_model.model_validate(args)


def test_create_work_request_accepts_every_catalogue_code_and_indeterminate() -> None:
    for code in [*sorted(CATALOGUE_CODES), "INDETERMINE"]:
        args = {**VALID_ARGS["gmao.create_work_request"], "failure_code": code}
        assert CreateWorkRequestArgs.model_validate(args).failure_code == code


def test_list_work_orders_args_defaults_follow_the_config_plan() -> None:
    args = ListWorkOrdersArgs(asset_id="EQ-PLI-01", symptom_code=None)
    assert (args.since_days, args.limit) == (548, 8)
    assert ListWorkOrdersArgs(asset_id="EQ-PLI-01", symptom_code=None, limit=12).limit == 12


def test_write_args_models_are_the_connector_payloads() -> None:
    reply = RecordReplyPreparedArgs.model_validate(VALID_ARGS["erp.record_reply_prepared"])
    request = CreateWorkRequestArgs.model_validate(VALID_ARGS["gmao.create_work_request"])
    assert isinstance(reply, ReplyPrepared)
    assert isinstance(request, WorkRequest)
    payload = ReplyPrepared.model_validate(VALID_ARGS["erp.record_reply_prepared"])
    assert args_sha256(reply) == args_sha256(payload)


# --------------------------------------------------------------------------- canonical json
def test_canonical_json_is_shared_with_the_connectors() -> None:
    assert canonical_json is connectors_canonical_json
    assert args_sha256 is connectors_args_sha256


def test_canonical_json_is_order_independent() -> None:
    forward = dict(VALID_ARGS["gmao.create_work_request"])
    backward = dict(reversed(list(forward.items())))
    assert list(forward) != list(backward)
    a = CreateWorkRequestArgs.model_validate(forward)
    b = CreateWorkRequestArgs.model_validate(backward)
    assert canonical_json(a) == canonical_json(b)
    assert args_sha256(a) == args_sha256(b)


def test_canonical_json_is_stable() -> None:
    args = ListWorkOrdersArgs(asset_id="EQ-PLI-01", symptom_code="SYM-FUITE")
    expected = '{"asset_id":"EQ-PLI-01","limit":8,"since_days":548,"symptom_code":"SYM-FUITE"}'
    assert canonical_json(args) == expected
    assert args_sha256(args) == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert canonical_json(FailureCatalogArgs()) == "{}"
    request = CreateWorkRequestArgs.model_validate(VALID_ARGS["gmao.create_work_request"])
    assert canonical_json(request) == (
        '{"asset_id":"EQ-PLI-01","cited_wo_ids":["OT-2026-0001"],'
        '"failure_code":"INDETERMINE","symptom_code":"SYM-FUITE"}'
    )
    # Any change of an argument changes the digest.
    other = ListWorkOrdersArgs(asset_id="EQ-PLI-01", symptom_code="SYM-FUITE", limit=9)
    assert args_sha256(other) != args_sha256(args)


def test_raw_args_sha256_of_invalid_args() -> None:
    raw: dict[str, object] = {"b": 1, "a": "é"}
    expected = hashlib.sha256('{"a":"é","b":1}'.encode()).hexdigest()
    assert raw_args_sha256(raw) == expected
    assert raw_args_sha256(dict(reversed(list(raw.items())))) == expected
    assert raw_args_sha256({"a": object()}) == ""
    assert raw_args_sha256({1: "a", "b": 2}) == ""
    assert raw_args_sha256(["CMD-2026-0412"]) == ""
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    assert raw_args_sha256(cycle) == ""


# --------------------------------------------------------------------------- authority
def _digest(name: str = RECORD_REPLY) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def test_mint_and_verify() -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert (token.action_id, token.tool, token.args_sha256) == (
        ACTION_ID,
        RECORD_REPLY,
        _digest(),
    )
    assert isinstance(token, WriteToken)
    assert authority.verify(token, RECORD_REPLY, _digest()) == "ok"
    # verify does not consume.
    assert authority.verify(token, RECORD_REPLY, _digest()) == "ok"
    other = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert other.nonce != token.nonce
    assert other.mac != token.mac
    # The token carries no content: identifiers, a digest, a nonce and a MAC.
    assert set(ApprovalToken.model_fields) == {"action_id", "tool", "args_sha256", "nonce", "mac"}


def _flip(hex_text: str) -> str:
    return ("1" if hex_text[0] == "0" else "0") + hex_text[1:]


def test_a_tampered_token_is_refused() -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    tampered = [
        token.model_copy(update={"mac": _flip(token.mac)}),
        token.model_copy(update={"nonce": _flip(token.nonce)}),
        token.model_copy(update={"action_id": OTHER_ACTION_ID}),
        # Changing the bound digest inside the token breaks the MAC.
        token.model_copy(update={"args_sha256": _digest(CREATE_WORK_REQUEST)}),
        token.model_copy(update={"tool": CREATE_WORK_REQUEST}),
    ]
    for forged in tampered:
        assert authority.verify(forged, forged.tool, forged.args_sha256) == "bad_mac"
        with pytest.raises(ApprovalError, match=r"^consume:bad_mac$"):
            authority.consume(forged)
    junk = ApprovalToken.model_construct(
        action_id=1,  # type: ignore[arg-type]
        tool=None,  # type: ignore[arg-type]
        args_sha256="x",
        nonce="é",
        mac="é",
    )
    assert authority.verify(junk, RECORD_REPLY, _digest()) == "bad_mac"
    assert authority.verify("not a token", RECORD_REPLY, _digest()) == "bad_mac"  # type: ignore[arg-type]
    with pytest.raises(ApprovalError, match=r"^consume:bad_mac$"):
        authority.consume(junk)
    # The genuine token is untouched by the refusals.
    assert authority.verify(token, RECORD_REPLY, _digest()) == "ok"


def test_a_token_for_another_tool_is_refused() -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert authority.verify(token, CREATE_WORK_REQUEST, _digest()) == "wrong_tool"
    assert authority.verify(token, "erp.get_order", _digest()) == "wrong_tool"


def test_args_changed_after_approval_are_refused() -> None:
    authority = ApprovalAuthority()
    approved = RecordReplyPreparedArgs(order_id="CMD-2026-0412", draft_sha256=DRAFT_SHA)
    edited = RecordReplyPreparedArgs(order_id="CMD-2026-0412", draft_sha256=OTHER_DRAFT_SHA)
    token = authority.mint(ACTION_ID, RECORD_REPLY, args_sha256(approved))
    assert args_sha256(edited) != args_sha256(approved)
    assert authority.verify(token, RECORD_REPLY, args_sha256(edited)) == "wrong_args"
    assert authority.verify(token, RECORD_REPLY, "é") == "wrong_args"
    assert authority.verify(token, RECORD_REPLY, "\udc80") == "wrong_args"
    assert authority.verify(token, RECORD_REPLY, args_sha256(approved)) == "ok"


def test_a_consumed_token_cannot_be_reused() -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    authority.consume(token)
    assert authority.verify(token, RECORD_REPLY, _digest()) == "reused"
    with pytest.raises(ApprovalError, match=r"^consume:reused$"):
        authority.consume(token)
    # Another token for the same action is a new approval, with its own nonce.
    again = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert authority.verify(again, RECORD_REPLY, _digest()) == "ok"


def test_a_token_from_another_authority_is_refused() -> None:
    first, second = ApprovalAuthority(), ApprovalAuthority()
    token = first.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert second.verify(token, RECORD_REPLY, _digest()) == "bad_mac"
    with pytest.raises(ApprovalError):
        second.consume(token)
    # Same key, same verdict: the MAC depends on the key only.
    assert (
        ApprovalAuthority(TEST_KEY).verify(
            ApprovalAuthority(TEST_KEY).mint(ACTION_ID, RECORD_REPLY, _digest()),
            RECORD_REPLY,
            _digest(),
        )
        == "ok"
    )


def _key_forms(key: bytes) -> list[str]:
    return [key.hex(), key.hex().upper(), repr(key), key.decode("latin-1"), str(list(key))]


def _assert_no_key(text: str) -> None:
    for form in _key_forms(TEST_KEY):
        assert form not in text


def test_the_key_never_leaves_the_authority() -> None:
    authority = ApprovalAuthority(TEST_KEY)
    _assert_no_key(repr(authority))
    _assert_no_key(str(authority))
    _assert_no_key(f"{authority!r} {authority}")
    assert "hidden" in repr(authority)
    with pytest.raises(TypeError):
        vars(authority)
    attempts: list[Callable[[], object]] = [
        lambda: pickle.dumps(authority),
        lambda: copy.copy(authority),
        lambda: copy.deepcopy(authority),
        lambda: authority.__getstate__(),
        lambda: authority.mint("not an id", RECORD_REPLY, _digest()),
        lambda: authority.mint(ACTION_ID, "erp.get_order", _digest()),  # type: ignore[arg-type]
        lambda: authority.consume(ApprovalToken.model_construct()),  # type: ignore[call-arg]
        lambda: ApprovalAuthority(TEST_KEY[: KEY_BYTES - 1]),
        lambda: ApprovalAuthority("x" * 64),  # type: ignore[arg-type]
    ]
    for attempt in attempts:
        with pytest.raises((TypeError, ApprovalError)) as caught:
            attempt()
        _assert_no_key(str(caught.value))
        _assert_no_key(repr(caught.value))
        assert caught.value.__cause__ is None
    with pytest.raises(TypeError, match=r"^ApprovalAuthority:not_picklable$"):
        pickle.dumps(authority)
    with pytest.raises(ApprovalError, match=r"^mint:invalid_fields$"):
        authority.mint("not an id", RECORD_REPLY, _digest())
    with pytest.raises(ApprovalError, match=r"^authority:key_too_short$"):
        ApprovalAuthority(b"short")
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    _assert_no_key(repr(token) + token.model_dump_json())
    verifier = authority.verifier
    _assert_no_key(repr(verifier) + str(verifier))
    for attempt in (
        lambda: pickle.dumps(verifier),
        lambda: copy.copy(verifier),
        lambda: copy.deepcopy(verifier),
    ):
        with pytest.raises(TypeError, match=r"^ApprovalVerifier:not_"):
            attempt()


def test_errors_keep_no_exception_chain() -> None:
    authority = ApprovalAuthority(TEST_KEY)
    attempts: list[Callable[[], object]] = [
        lambda: authority.mint("SECRETVALUE", RECORD_REPLY, _digest()),
        lambda: authority.mint(ACTION_ID, RECORD_REPLY, "SECRETVALUE"),
        lambda: authority.consume(
            ApprovalToken.model_construct(  # type: ignore[call-arg]
                nonce="SECRETVALUE"
            )
        ),
    ]
    for attempt in attempts:
        with pytest.raises(ApprovalError) as caught:
            attempt()
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert "SECRETVALUE" not in str(caught.value)


class SlipperyStr(str):
    """A ``str`` whose hash changes on every call and that equals nothing."""

    _hashes = itertools.count()

    def __hash__(self) -> int:
        return next(self._hashes)

    def __eq__(self, other: object) -> bool:
        return False

    def __ne__(self, other: object) -> bool:
        return True


class AlternatingStr(str):
    """A ``str`` whose equality alternates between calls."""

    _calls = itertools.count()

    def __eq__(self, other: object) -> bool:
        return next(self._calls) % 2 == 1

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


class LyingStr(str):
    """A ``str`` that equals everything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = str.__hash__


@pytest.mark.parametrize("hostile", [SlipperyStr, AlternatingStr], ids=["slippery", "alternating"])
def test_a_spent_token_with_a_lying_nonce_is_never_replayed(
    setup: Setup, hostile: type[str]
) -> None:
    args = setup.reply_args()
    token = setup.approve(RECORD_REPLY, args)
    assert setup.executor.call(RECORD_REPLY, args, token=token).ok
    forged = ApprovalToken.model_construct(**{**token.model_dump(), "nonce": hostile(token.nonce)})
    for _ in range(3):
        assert setup.executor.call(RECORD_REPLY, args, token=forged).error == "bad_token"
        assert setup.authority.verify(forged, RECORD_REPLY, token.args_sha256) == "bad_mac"
    assert len(setup.erp.executed) == 1
    assert sum(r.executed for r in setup.executor.records) == 1
    assert setup.executor.call(RECORD_REPLY, args, token=token).error == "token_reused"


def test_only_a_plain_token_of_plain_strings_is_genuine() -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())

    class TokenSubclass(ApprovalToken):
        pass

    lookalikes: list[ApprovalToken] = [
        TokenSubclass.model_validate(token.model_dump()),
        *(
            ApprovalToken.model_construct(**{**token.model_dump(), field: LyingStr(value)})
            for field, value in token.model_dump().items()
        ),
        ApprovalToken.model_construct(**{**token.model_dump(), "nonce": token.nonce + "\n"}),
    ]
    for lookalike in lookalikes:
        assert authority.verify(lookalike, RECORD_REPLY, _digest()) == "bad_mac"
        assert authority.verifier.redeem(lookalike, RECORD_REPLY, _digest()) == "bad_mac"
    # The tool and the digest given by the caller must be plain strings too.
    assert authority.verify(token, LyingStr("erp.get_order"), _digest()) == "wrong_tool"
    assert authority.verify(token, RECORD_REPLY, LyingStr(_digest(CREATE_WORK_REQUEST))) == (
        "wrong_args"
    )
    # None of this spent the genuine token.
    assert authority.verifier.redeem(token, RECORD_REPLY, _digest()) == "ok"
    assert authority.verifier.redeem(token, RECORD_REPLY, _digest()) == "reused"


def test_verify_compares_macs_with_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = ApprovalAuthority()
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    compared: list[tuple[object, object]] = []

    def recording(a: object, b: object) -> bool:
        compared.append((a, b))
        return a == b

    monkeypatch.setattr(hmac, "compare_digest", recording)
    assert authority.verify(token, RECORD_REPLY, _digest()) == "ok"
    assert token.mac in [b for _, b in compared]
    forged = token.model_copy(update={"mac": _flip(token.mac)})
    assert authority.verify(forged, RECORD_REPLY, _digest()) == "bad_mac"
    assert forged.mac in [b for _, b in compared]


def test_redeem_spends_only_an_accepted_token() -> None:
    authority = ApprovalAuthority()
    verifier = authority.verifier
    token = authority.mint(ACTION_ID, RECORD_REPLY, _digest())
    assert verifier.redeem(token, CREATE_WORK_REQUEST, _digest()) == "wrong_tool"
    assert verifier.redeem(token, RECORD_REPLY, _digest(CREATE_WORK_REQUEST)) == "wrong_args"
    assert verifier.verify(token, RECORD_REPLY, _digest()) == "ok"
    assert verifier.redeem(token, RECORD_REPLY, _digest()) == "ok"
    # The authority and its verifier share one nonce set.
    assert authority.verify(token, RECORD_REPLY, _digest()) == "reused"
    with pytest.raises(ApprovalError, match=r"^consume:reused$"):
        authority.consume(token)


def _race(
    executor: ToolExecutor, args: dict[str, object], token: ApprovalToken, escaped: list[str]
) -> list[ToolResult]:
    """Two threads present the same token at the same instant."""
    barrier = threading.Barrier(2)
    results: list[ToolResult] = []

    def use() -> None:
        barrier.wait()
        try:
            results.append(executor.call(RECORD_REPLY, args, token=token))
        except Exception as exc:
            escaped.append(type(exc).__name__)

    threads = [threading.Thread(target=use) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_two_concurrent_uses_of_one_token_execute_once(setup: Setup) -> None:
    args = setup.reply_args()
    trials = 40
    escaped: list[str] = []
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(trials):
            token = setup.approve(RECORD_REPLY, args)
            results = _race(setup.executor, args, token, escaped)
            assert sorted(r.error or "ok" for r in results) == ["ok", "token_reused"]
    finally:
        sys.setswitchinterval(previous)
    assert escaped == []
    assert len(setup.erp.executed) == trials
    assert len(setup.executor.records) == 2 * trials
    assert sum(r.executed for r in setup.executor.records) == trials


# --------------------------------------------------------------------------- executor reads
def test_reads_return_views(setup: Setup) -> None:
    executor = setup.executor
    order = executor.call("erp.get_order", {"order_id": setup.order_id})
    assert order.ok and order.error is None
    assert isinstance(order.data, OrderView) and order.data.order_id == setup.order_id
    asset = executor.call("gmao.get_asset", {"asset_id": setup.asset_id})
    assert isinstance(asset.data, AssetView) and asset.data.asset_id == setup.asset_id
    history = executor.call(
        "gmao.list_work_orders",
        {"asset_id": setup.asset_id, "symptom_code": None, "since_days": 100_000, "limit": 2},
    )
    assert isinstance(history.data, tuple) and len(history.data) == 2
    rows = [wo for wo in history.data if isinstance(wo, WorkOrderView)]
    assert len(rows) == 2
    assert all(wo.asset_id == setup.asset_id for wo in rows)
    catalog = executor.call("gmao.failure_catalog", {})
    assert isinstance(catalog.data, tuple) and len(catalog.data) == 16
    assert all(isinstance(entry, FailureCodeView) for entry in catalog.data)
    # No read returns a contact: get_order_contact is not a tool.
    for result in (order, asset, history, catalog):
        assert not isinstance(result.data, ContactView)
    assert [r.tool for r in executor.records] == [
        "erp.get_order",
        "gmao.get_asset",
        "gmao.list_work_orders",
        "gmao.failure_catalog",
    ]
    for record in executor.records:
        assert (record.kind, record.approved, record.executed, record.refusal) == (
            "read",
            False,
            True,
            None,
        )
    assert record.args_sha256 == args_sha256(FailureCatalogArgs())


def test_a_read_of_an_unknown_id_is_ok_with_no_data(setup: Setup) -> None:
    unknown_order = "CMD-1999-0001"
    assert unknown_order not in setup.erp.order_ids
    result = setup.executor.call("erp.get_order", {"order_id": unknown_order})
    assert result == ToolResult(ok=True, tool="erp.get_order", data=None, error=None)
    assert setup.executor.call("gmao.get_asset", {"asset_id": "EQ-ZZZ-99"}).data is None


INVALID_CALLS: list[tuple[str, str, object]] = [
    ("bad_id", "erp.get_order", {"order_id": "commande 2026-0412"}),
    ("extra_key", "erp.get_order", {"order_id": "CMD-2026-0412", "to": "boite du client"}),
    ("missing_key", "gmao.list_work_orders", {"asset_id": "EQ-PLI-01"}),
    (
        "limit_high",
        "gmao.list_work_orders",
        {"asset_id": "EQ-PLI-01", "symptom_code": None, "limit": 99},
    ),
    ("not_mapping", "gmao.get_asset", ["EQ-PLI-01"]),
    ("not_serialisable", "gmao.get_asset", {"asset_id": object()}),
    (
        "write_bad_sha",
        "erp.record_reply_prepared",
        {"order_id": "CMD-2026-0412", "draft_sha256": "Bonjour, texte du brouillon"},
    ),
]


@pytest.mark.parametrize(("case", "name", "args"), INVALID_CALLS, ids=[c[0] for c in INVALID_CALLS])
def test_invalid_args_give_a_result_not_an_exception(
    setup: Setup, case: str, name: str, args: object
) -> None:
    result = setup.executor.call(name, args)  # type: ignore[arg-type]
    assert result == ToolResult(ok=False, tool=name, data=None, error="invalid_args")
    (record,) = setup.executor.records
    assert (record.tool, record.approved, record.executed, record.refusal) == (
        name,
        False,
        False,
        "invalid_args",
    )
    assert record.args_sha256 == raw_args_sha256(args)
    if case in {"not_mapping", "not_serialisable"}:
        assert record.args_sha256 == ""
    else:
        assert len(record.args_sha256) == 64
    assert not setup.erp.executed and not setup.gmao.executed


def test_an_invalid_write_with_a_valid_token_keeps_the_token(setup: Setup) -> None:
    token = setup.approve(RECORD_REPLY, setup.reply_args())
    bad = {**setup.reply_args(), "draft": "texte"}
    result = setup.executor.call(RECORD_REPLY, bad, token=token)
    assert result.error == "invalid_args"
    assert setup.authority.verify(token, RECORD_REPLY, token.args_sha256) == "ok"
    assert not setup.erp.executed


@pytest.mark.parametrize("name", ["erp.delete_order", "", "ERP.GET_ORDER", 42, None])
def test_an_unknown_tool_is_refused(setup: Setup, name: object) -> None:
    result = setup.executor.call(name, {"order_id": "CMD-2026-0412"})  # type: ignore[arg-type]
    assert result == ToolResult(ok=False, tool=UNKNOWN_TOOL, error="unknown_tool")
    (record,) = setup.executor.records
    assert (record.tool, record.kind, record.refusal) == ("unknown", None, "unknown_tool")
    assert record.args_sha256 == raw_args_sha256({"order_id": "CMD-2026-0412"})


# --------------------------------------------------------------------------- executor writes
def test_a_write_without_a_token_is_refused_and_not_executed(setup: Setup) -> None:
    for name, args in (
        (RECORD_REPLY, setup.reply_args()),
        (CREATE_WORK_REQUEST, setup.work_request_args()),
    ):
        result = setup.executor.call(name, args)
        assert result == ToolResult(ok=False, tool=name, error="no_token")
        record = setup.executor.records[-1]
        assert (record.kind, record.approved, record.executed, record.refusal) == (
            "write_internal",
            False,
            False,
            "no_token",
        )
    assert setup.erp.executed == []
    assert setup.gmao.executed == []


def test_an_approved_reply_executes_exactly_once(setup: Setup) -> None:
    args = setup.reply_args()
    token = setup.approve(RECORD_REPLY, args)
    result = setup.executor.call(RECORD_REPLY, args, token=token)
    assert result.ok
    assert result.data == WriteReceipt(
        action_id=ACTION_ID, tool=RECORD_REPLY, args_sha256=token.args_sha256
    )
    (effect,) = setup.erp.executed
    assert (effect.action_id, effect.tool, effect.args_sha256) == (
        ACTION_ID,
        RECORD_REPLY,
        token.args_sha256,
    )
    assert setup.authority.verify(token, RECORD_REPLY, token.args_sha256) == "reused"
    record = setup.executor.records[-1]
    assert (record.approved, record.executed, record.refusal) == (True, True, None)
    assert record.args_sha256 == token.args_sha256

    again = setup.executor.call(RECORD_REPLY, args, token=token)
    assert again == ToolResult(ok=False, tool=RECORD_REPLY, error="token_reused")
    assert len(setup.erp.executed) == 1
    record = setup.executor.records[-1]
    assert (record.approved, record.executed, record.refusal) == (False, False, "token_reused")
    assert setup.gmao.executed == []


def test_an_approved_work_request_executes_exactly_once(setup: Setup) -> None:
    args = setup.work_request_args()
    token = setup.approve(CREATE_WORK_REQUEST, args)
    result = setup.executor.call(CREATE_WORK_REQUEST, args, token=token)
    assert isinstance(result.data, WriteReceipt)
    assert [e.args_sha256 for e in setup.gmao.executed] == [token.args_sha256]
    assert setup.executor.call(CREATE_WORK_REQUEST, args, token=token).error == "token_reused"
    assert len(setup.gmao.executed) == 1
    assert setup.erp.executed == []


def test_refused_tokens_execute_nothing_and_are_not_consumed(setup: Setup) -> None:
    args = setup.reply_args()
    token = setup.approve(RECORD_REPLY, args)
    stranger = Setup().approve(RECORD_REPLY, args)
    cases = [
        ("bad_token", RECORD_REPLY, args, token.model_copy(update={"mac": _flip(token.mac)})),
        ("bad_token", RECORD_REPLY, args, stranger),
        ("wrong_tool", CREATE_WORK_REQUEST, setup.work_request_args(), token),
        # The operator's edit changed the draft after approval: another digest.
        ("wrong_args", RECORD_REPLY, setup.reply_args(OTHER_DRAFT_SHA), token),
    ]
    for error, name, call_args, used in cases:
        result = setup.executor.call(name, call_args, token=used)
        assert result == ToolResult(ok=False, tool=name, error=error), error
        record = setup.executor.records[-1]
        assert (record.approved, record.executed, record.refusal) == (False, False, error)
    assert setup.erp.executed == [] and setup.gmao.executed == []
    # None of the refusals spent the genuine token.
    assert setup.executor.call(RECORD_REPLY, args, token=token).ok
    assert len(setup.erp.executed) == 1


def test_a_token_that_only_looks_like_one_is_refused(setup: Setup) -> None:
    class LookAlike:
        action_id = ACTION_ID
        tool = RECORD_REPLY
        args_sha256 = "0" * 64

    result = setup.executor.call(RECORD_REPLY, setup.reply_args(), token=LookAlike())  # type: ignore[arg-type]
    assert result.error == "bad_token"
    assert setup.erp.executed == []


def test_a_connector_refusal_spends_the_token(setup: Setup) -> None:
    args = {**setup.work_request_args(), "cited_wo_ids": [setup.wo_ids[0], setup.foreign_wo_id]}
    token = setup.approve(CREATE_WORK_REQUEST, args)
    result = setup.executor.call(CREATE_WORK_REQUEST, args, token=token)
    assert result == ToolResult(ok=False, tool=CREATE_WORK_REQUEST, error="connector_error")
    record = setup.executor.records[-1]
    assert (record.approved, record.executed, record.refusal) == (True, False, "connector_error")
    assert setup.gmao.executed == []
    # One token, at most one execution attempt.
    assert setup.authority.verify(token, CREATE_WORK_REQUEST, token.args_sha256) == "reused"


class FailingErp:
    """An ERP whose every call fails, with a value in the message."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_order(self, order_id: OrderId) -> OrderView | None:
        raise self.error

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        raise self.error

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        raise self.error


class FailingGmao:
    """A GMAO whose every call fails, with a value in the message."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get_asset(self, asset_id: AssetId) -> AssetView | None:
        raise self.error

    def list_work_orders(
        self,
        asset_id: AssetId,
        symptom_code: SymptomCode | None,
        since_days: int = 548,
        limit: int = 8,
    ) -> tuple[WorkOrderView, ...]:
        raise self.error

    def failure_catalog(self) -> tuple[FailureCodeView, ...]:
        raise self.error

    def create_work_request(self, req: WorkRequest, *, token: WriteToken) -> WriteReceipt:
        raise self.error


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (OSError("CMD-2026-0412 unreachable"), "connector_unavailable"),
        (ConnectorError("gmao.list_work_orders:limit"), "connector_error"),
    ],
)
def test_connector_failures_never_raise(error: Exception, code: str) -> None:
    authority = ApprovalAuthority()
    executor = ToolExecutor(FailingErp(error), FailingGmao(error), authority.verifier)
    for name in sorted(READ_TOOLS):
        result = executor.call(name, VALID_ARGS[name])
        assert result == ToolResult(ok=False, tool=name, error=code), name
    for name in sorted(WRITE_TOOLS):
        model = TOOLS[name].args_model.model_validate(VALID_ARGS[name])
        token = authority.mint(ACTION_ID, name, args_sha256(model))  # type: ignore[arg-type]
        result = executor.call(name, VALID_ARGS[name], token=token)
        assert result == ToolResult(ok=False, tool=name, error=code), name
    assert [r.refusal for r in executor.records] == [code] * 6
    assert [r.approved for r in executor.records] == [False] * 4 + [True] * 2
    assert not any(r.executed for r in executor.records)
    assert "CMD-2026-0412" not in json.dumps([r.model_dump() for r in executor.records])


def test_tool_result_ok_and_error_agree() -> None:
    with pytest.raises(ValidationError):
        ToolResult(ok=True, tool="erp.get_order", error="invalid_args")
    with pytest.raises(ValidationError):
        ToolResult(ok=False, tool="erp.get_order")
    with pytest.raises(ValidationError):
        ToolResult(
            ok=False,
            tool="erp.record_reply_prepared",
            data=WriteReceipt(action_id=ACTION_ID, tool=RECORD_REPLY, args_sha256="0" * 64),
            error="no_token",
        )


def test_the_executor_holds_no_minting_authority(setup: Setup) -> None:
    for value in vars(setup.executor).values():
        assert not isinstance(value, ApprovalAuthority), type(value).__name__
        assert not hasattr(value, "mint"), type(value).__name__
    verifier = setup.authority.verifier
    assert not hasattr(verifier, "mint")
    for slot in type(verifier).__slots__:
        assert not hasattr(getattr(verifier, slot), "mint"), slot
    with pytest.raises(TypeError):
        vars(verifier)
    with pytest.raises(TypeError, match=r"^ToolExecutor:verifier_required$"):
        ToolExecutor(setup.erp, setup.gmao, setup.authority)  # type: ignore[arg-type]


LEAK = "PRIX-CANARI-SYNTHETIQUE 1234,56 $"


class LeakyErp:
    """An ERP that returns raw records instead of views (does not conform)."""

    def get_order(self, order_id: OrderId) -> OrderView | None:
        return cast(OrderView, {"order_id": order_id, "price_unit": LEAK})

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        return None

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        return cast(WriteReceipt, {"price_unit": LEAK})


class LeakyGmao:
    """A GMAO that returns raw records instead of views (does not conform)."""

    def get_asset(self, asset_id: AssetId) -> AssetView | None:
        return cast(AssetView, {"asset_id": asset_id, "note": LEAK})

    def list_work_orders(
        self,
        asset_id: AssetId,
        symptom_code: SymptomCode | None,
        since_days: int = 548,
        limit: int = 8,
    ) -> tuple[WorkOrderView, ...]:
        return cast(tuple[WorkOrderView, ...], ({"technician_name": LEAK},))

    def failure_catalog(self) -> tuple[FailureCodeView, ...]:
        # A list of genuine views is still not the tuple the tool returns.
        return cast(
            tuple[FailureCodeView, ...],
            [FailureCodeView(code="HYD-JOINT", category="hydraulique", label=LEAK)],
        )

    def create_work_request(self, req: WorkRequest, *, token: WriteToken) -> WriteReceipt:
        return cast(WriteReceipt, LEAK)


def test_a_non_conforming_connector_result_is_dropped() -> None:
    authority = ApprovalAuthority()
    executor = ToolExecutor(LeakyErp(), LeakyGmao(), authority.verifier)
    for name in TOOL_NAMES:
        token = None
        if name in WRITE_TOOLS:
            model = TOOLS[name].args_model.model_validate(VALID_ARGS[name])
            token = authority.mint(ACTION_ID, name, args_sha256(model))  # type: ignore[arg-type]
        result = executor.call(name, VALID_ARGS[name], token=token)
        assert result == ToolResult(ok=False, tool=name, error="connector_unavailable"), name
        if token is not None:
            # The write was attempted with this token: it is spent.
            assert authority.verify(token, name, token.args_sha256) == "reused", name
    assert [r.refusal for r in executor.records] == ["connector_unavailable"] * 6
    assert not any(r.executed for r in executor.records)
    assert LEAK not in json.dumps([r.model_dump() for r in executor.records])


class BadMapping(Mapping[str, object]):
    """A mapping that fails when read, with a value in the exception."""

    def __getitem__(self, key: str) -> object:
        raise KeyError(LEAK)

    def __iter__(self) -> Iterator[str]:
        return iter(["order_id"])

    def __len__(self) -> int:
        return 1


class RaisingName(str):
    """A tool name whose comparison raises."""

    def __eq__(self, other: object) -> bool:
        raise RuntimeError(LEAK)

    __hash__ = str.__hash__


def test_hostile_inputs_never_raise(setup: Setup) -> None:
    executor = setup.executor
    assert executor.call("erp.get_order", BadMapping()).error == "invalid_args"
    assert executor.call("erp.delete_order", BadMapping()).error == "unknown_tool"
    # An integer too long for JSON validates, then fails at the digest.
    huge = {"asset_id": setup.asset_id, "symptom_code": None, "since_days": 10**5000}
    assert executor.call("gmao.list_work_orders", huge).error == "invalid_args"
    named = executor.call(RaisingName("erp.get_order"), {"order_id": setup.order_id})
    assert named.ok and named.tool == "erp.get_order"
    assert type(named.tool) is str
    assert [r.args_sha256 for r in executor.records[:3]] == ["", "", ""]
    assert [r.refusal for r in executor.records] == [
        "invalid_args",
        "unknown_tool",
        "invalid_args",
        None,
    ]


# --------------------------------------------------------------------------- records
def _string_leaves(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for k, v in value.items() for s in (*_string_leaves(k), *_string_leaves(v))]
    if isinstance(value, list | tuple):
        return [s for item in value for s in _string_leaves(item)]
    return []


def test_every_call_appends_one_record_without_any_value(setup: Setup) -> None:
    reply = setup.reply_args()
    request = setup.work_request_args()
    calls: list[tuple[str, dict[str, object], ApprovalToken | None]] = [
        ("erp.get_order", {"order_id": setup.order_id}, None),
        ("erp.get_order", {"order_id": "commande CMD-1999-0001 du client"}, None),
        ("gmao.get_asset", {"asset_id": setup.asset_id}, None),
        (
            "gmao.list_work_orders",
            {"asset_id": setup.asset_id, "symptom_code": "SYM-FUITE", "limit": 3},
            None,
        ),
        ("gmao.failure_catalog", {}, None),
        ("erp.delete_order", {"order_id": setup.order_id, "note": "note au systeme"}, None),
        (RECORD_REPLY, reply, None),
        (RECORD_REPLY, setup.reply_args(OTHER_DRAFT_SHA), setup.approve(RECORD_REPLY, reply)),
        (RECORD_REPLY, reply, setup.approve(RECORD_REPLY, reply)),
        (CREATE_WORK_REQUEST, request, setup.approve(CREATE_WORK_REQUEST, request)),
        (CREATE_WORK_REQUEST, {**request, "description": "fuite au verin"}, None),
    ]
    for count, (name, args, token) in enumerate(calls, start=1):
        result = setup.executor.call(name, args, token=token)
        assert len(setup.executor.records) == count
        record = setup.executor.records[-1]
        assert isinstance(record, ToolCallRecord)
        assert set(record.model_dump()) == {
            "tool",
            "kind",
            "args_sha256",
            "approved",
            "executed",
            "refusal",
        }
        text = record.model_dump_json()
        values = [*_string_leaves(args), DRAFT_SHA, OTHER_DRAFT_SHA]
        if token is not None:
            values += [token.action_id, token.nonce, token.mac]
        for value in values:
            if value in {"", record.tool}:
                continue
            assert value not in text, (name, count)
        assert record.tool == (name if name in TOOLS else UNKNOWN_TOOL)
        assert record.refusal == result.error
    assert len(setup.erp.executed) == 1
    assert len(setup.gmao.executed) == 1


# --------------------------------------------------------------------------- structure
NETWORK_MODULES = ("httpx", "smtplib", "socket", "urllib", "requests", "http", "ollama")
# The only package modules tools/ may import: everything else (llm, ollama, embeddings,
# retrieval, index, the pipelines, agents, evals, cli) is above it or reaches httpx.
TOOLS_MAY_IMPORT = ("alverno_lab.connectors", "alverno_lab.tools", "alverno_lab.privacy.ops_types")
# Package modules that import a network client, directly or through another module.
NETWORK_REACHING = (
    "alverno_lab.llm",
    "alverno_lab.ollama",
    "alverno_lab.embeddings",
    "alverno_lab.retrieval",
    "alverno_lab.index",
    "alverno_lab.pipeline",
    "alverno_lab.hr_pipeline",
    "alverno_lab.agents",
    "alverno_lab.evals",
    "alverno_lab.cli",
)


def _within(module: str, packages: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in packages)


def _imports(path: Path) -> set[str]:
    """Imported modules, with ``from a import b`` recorded as both ``a`` and ``a.b``."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            modules.add(base)
            sep = "" if base.endswith(".") else "."
            modules.update(f"{base}{sep}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in {"__import__", "import_module"}:
                modules.add("<dynamic import>")
    return modules


def test_tools_import_no_network_module_and_nothing_above_them() -> None:
    sources = sorted(TOOLS_SRC.glob("*.py"))
    assert [p.name for p in sources] == [
        "__init__.py",
        "authority.py",
        "executor.py",
        "registry.py",
    ]
    for path in sources:
        modules = _imports(path)
        network = sorted(m for m in modules if m.split(".")[0] in NETWORK_MODULES)
        assert not network, (path.name, network)
        assert "<dynamic import>" not in modules, path.name
        assert not [m for m in modules if m.startswith(".")], path.name
        internal = [m for m in modules if _within(m, ("alverno_lab",))]
        assert internal, path.name
        outside = sorted(m for m in internal if not _within(m, TOOLS_MAY_IMPORT))
        assert not outside, (path.name, outside)


def test_the_import_scan_catches_a_planted_network_import(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import smtplib\nfrom http.client import HTTPSConnection\n"
        "import importlib\nimportlib.import_module('socket')\n"
        "from alverno_lab import ollama\nimport alverno_lab.embeddings\n"
        "from . import retrieval\n",
        encoding="utf-8",
    )
    modules = _imports(sample)
    assert {"smtplib", "http.client", "<dynamic import>"} <= modules
    assert {"alverno_lab.ollama", "alverno_lab.embeddings", ".retrieval"} <= modules
    assert not _within("alverno_lab.ollama", TOOLS_MAY_IMPORT)
    assert not _within("alverno_lab.connectorsx", TOOLS_MAY_IMPORT)
    assert _within("alverno_lab.connectors.types.OrderView", TOOLS_MAY_IMPORT)


def test_importing_connectors_and_tools_loads_no_network_module() -> None:
    # A fresh interpreter: what the packages pull in, directly or transitively.
    code = (
        "import json, sys\n"
        "import alverno_lab.connectors, alverno_lab.tools\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    loaded: list[str] = json.loads(done.stdout)
    assert "alverno_lab.tools.executor" in loaded
    network = sorted(m for m in loaded if m.split(".")[0] in NETWORK_MODULES)
    assert not network, network
    reaching = sorted(m for m in loaded if _within(m, NETWORK_REACHING))
    assert not reaching, reaching
