"""Typed identifiers, closed codes and purpose-bound views of the module 3 connectors.

ADR 0012 fixes every type here. Identifiers carry a letter prefix and at most 4
consecutive digits, so that the PI detector (ADR 0006) never reads one as a number;
patterns use ``[0-9]`` rather than ``\\d``, which also matches non-ASCII digits. Codes
are static ``Literal`` types: a test compares them with the code sets of
``data/eval/ops/gmao.yaml`` and ``erp.yaml``.

Views are what leaves a connector. Each one is frozen, rejects unknown fields and holds
only the fields ADR 0012 lists: no price, cost, margin, internal note or contact in
:class:`OrderView`, no phone or address in :class:`ContactView`, no technician
identity, shift, time or downtime in :class:`WorkOrderView`. Free text from a tool
reaches a view only as :class:`QuarantinedText`, built by the quarantine callable that
the caller supplies. Write receipts and side-effect records hold identifiers and
digests, never text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from typing import Annotated, Literal, Protocol, get_args, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class ConnectorError(ValueError):
    """A fixture, an argument or a write is invalid.

    The message is ``<where>:<rule>`` (a file path or a method name, then a rule name),
    never a value: errors are raised ``from None`` and outside any ``except`` block,
    so that no traceback and no exception chain shows one.
    """


# --------------------------------------------------------------------------- identifiers
OrderId = Annotated[str, StringConstraints(pattern=r"^CMD-[0-9]{4}-[0-9]{4}$")]
AccountId = Annotated[str, StringConstraints(pattern=r"^CLI-[0-9]{3}$")]
AssetId = Annotated[str, StringConstraints(pattern=r"^EQ-[A-Z]{3}-[0-9]{2}$")]
WorkOrderId = Annotated[str, StringConstraints(pattern=r"^OT-[0-9]{4}-[0-9]{4}$")]
ContactRef = Annotated[str, StringConstraints(pattern=r"^CT-[0-9]{3}$")]
# Codes and references of the fixtures that are not ADR 0012 identifiers.
PartRef = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,3}-[0-9]{3,4}$")]
CarrierCode = Annotated[str, StringConstraints(pattern=r"^TRP-[A-Z]{1,3}$")]
TrackingRef = Annotated[str, StringConstraints(pattern=r"^TRK-[0-9]{6}$")]
DelayReasonCode = Annotated[str, StringConstraints(pattern=r"^RET-[A-Z]{3,6}$")]
ActionCode = Annotated[str, StringConstraints(pattern=r"^ACT-[A-Z]{3,6}$")]
# One proposed write; the envelope creates it (uuid4 hex, ADR 0013).
ActionId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# --------------------------------------------------------------------------- closed codes
FailureCode = Literal[
    "MEC-ROUL",
    "MEC-COURR",
    "MEC-ALIGN",
    "ELE-CAPT",
    "ELE-MOTEUR",
    "ELE-CONT",
    "HYD-JOINT",
    "HYD-FLEX",
    "HYD-POMPE",
    "PNE-RACC",
    "PNE-VALVE",
    "COM-PROG",
    "COM-AUTOM",
    "USU-BUSE",
    "USU-OUTIL",
    "EXT-ALIM",
    "INDETERMINE",
]
INDETERMINATE_CODE: Literal["INDETERMINE"] = "INDETERMINE"
FAILURE_CODES: frozenset[str] = frozenset(get_args(FailureCode))
# The 16 catalogue codes: INDETERMINE is what verify writes, never a catalogue entry.
CATALOGUE_CODES: frozenset[str] = FAILURE_CODES - {INDETERMINATE_CODE}

SymptomCode = Literal[
    "SYM-FUITE",
    "SYM-BRUIT",
    "SYM-ARRET",
    "SYM-CHAUFF",
    "SYM-PRECIS",
    "SYM-ALARME",
    "SYM-PRESS",
    "SYM-QUALI",
]
SYMPTOM_CODES: frozenset[str] = frozenset(get_args(SymptomCode))

# No human-error category exists (ADR 0012): a fault never points at a small group.
FailureCategory = Literal[
    "mécanique", "électrique", "hydraulique", "pneumatique", "commande", "usure", "externe"
]
FAILURE_CATEGORIES: frozenset[str] = frozenset(get_args(FailureCategory))

OrderStatus = Literal[
    "recue",
    "planifiee",
    "en_production",
    "en_finition",
    "expediee_partielle",
    "expediee",
    "en_attente_client",
]
ORDER_STATUSES: frozenset[str] = frozenset(get_args(OrderStatus))

WorkOrderKind = Literal["correctif", "preventif"]
WORK_ORDER_KINDS: frozenset[str] = frozenset(get_args(WorkOrderKind))

Civility = Literal["Mme", "M."]

# The two simulated internal writes (ADR 0012); no sending tool exists.
WriteToolName = Literal["erp.record_reply_prepared", "gmao.create_work_request"]
RECORD_REPLY_TOOL: WriteToolName = "erp.record_reply_prepared"
CREATE_WORK_REQUEST_TOOL: WriteToolName = "gmao.create_work_request"

# list_work_orders bounds (ADR 0012, config plan: ops_history_days 548,
# ops_max_work_orders 8, at most 12).
DEFAULT_HISTORY_DAYS = 548
DEFAULT_WORK_ORDER_LIMIT = 8
MAX_WORK_ORDER_LIMIT = 12
MAX_CITED_WORK_ORDERS = 4


# --------------------------------------------------------------------------- quarantine
class QuarantinedText(BaseModel):
    """Tool text that went through the quarantine; ``text`` stays out of ``repr``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(repr=False)
    flags: tuple[str, ...]


# Supplied by the caller (privacy/quarantine.py through an adapter in I4, a stub in
# tests). Projections never decide quarantine themselves, and no default exists.
Quarantine = Callable[[str], QuarantinedText]


# --------------------------------------------------------------------------- views
class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderView(_View):
    """Order facts for ``order_status`` and ``customer_reply`` (ADR 0012)."""

    order_id: OrderId
    account_id: AccountId
    part_ref: PartRef
    qty_ordered: int = Field(ge=1)
    qty_shipped: int = Field(ge=0)
    status: OrderStatus
    promised_date: dt.date
    revised_date: dt.date | None = None
    carrier_code: CarrierCode | None = None
    tracking_ref: TrackingRef | None = None
    delay_reason_code: DelayReasonCode | None = None
    customer_visible_note: QuarantinedText | None = None


class ContactView(_View):
    """The order's contact, read by the ``compose`` node only; PI fields stay out of repr.

    ``account_label`` of a ``natural_person`` account is a name: compose uses
    ``natural_person`` to write a neutral salutation, and the label never enters a prompt.
    """

    contact_ref: ContactRef
    account_id: AccountId
    account_label: str = Field(repr=False)
    natural_person: bool
    function: str = Field(repr=False)
    civility: Civility
    surname: str = Field(repr=False)
    given_name: str = Field(repr=False)
    business_email: str = Field(repr=False)


class AssetView(_View):
    """One piece of equipment of the simulated GMAO."""

    asset_id: AssetId
    name: str
    kind: str
    line: str


class WorkOrderView(_View):
    """One history line: a date only, codes, and the quarantined remark if any."""

    wo_id: WorkOrderId
    asset_id: AssetId
    opened_on: dt.date
    kind: WorkOrderKind
    symptom_code: SymptomCode | None = None
    failure_code: FailureCode | None = None
    action_code: ActionCode
    remark: QuarantinedText | None = None


class FailureCodeView(_View):
    """One catalogue entry."""

    code: FailureCode
    category: FailureCategory
    label: str


class WriteReceipt(_View):
    """What a simulated write returns: identifiers and a digest, no text."""

    action_id: ActionId
    tool: WriteToolName
    args_sha256: Sha256Hex


class SideEffectRecord(_View):
    """One executed write, kept in a simulated connector's ``executed`` list."""

    action_id: ActionId
    tool: WriteToolName
    args_sha256: Sha256Hex


# --------------------------------------------------------------------------- write payloads
class ReplyPrepared(_View):
    """Arguments of ``erp.record_reply_prepared``: an order and the approved draft's digest."""

    order_id: OrderId
    draft_sha256: Sha256Hex


class WorkRequest(_View):
    """Arguments of ``gmao.create_work_request``: identifiers and closed codes only."""

    asset_id: AssetId
    symptom_code: SymptomCode
    failure_code: FailureCode
    cited_wo_ids: tuple[WorkOrderId, ...] = Field(min_length=1, max_length=MAX_CITED_WORK_ORDERS)

    @field_validator("cited_wo_ids")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate work order id")
        return value


@runtime_checkable
class WriteToken(Protocol):
    """What a write needs from an approval token; the executor verifies the token first."""

    @property
    def action_id(self) -> str: ...

    @property
    def tool(self) -> str: ...

    @property
    def args_sha256(self) -> str: ...


def canonical_json(model: BaseModel) -> str:
    """Canonical JSON of a payload: sorted keys, no spaces, UTF-8 characters kept."""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def args_sha256(model: BaseModel) -> str:
    """SHA-256 (hex) of :func:`canonical_json`."""
    return hashlib.sha256(canonical_json(model).encode("utf-8")).hexdigest()
