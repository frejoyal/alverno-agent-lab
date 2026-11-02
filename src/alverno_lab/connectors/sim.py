"""Simulated ERP and GMAO over the fictional fixtures of ``data/eval/ops/`` (ADR 0012).

- The whole YAML file is validated at load with the record models below (``extra=
  "forbid"``), then checked for referential integrity. Any failure raises a
  :class:`ConnectorError` whose message is ``<file>:<rule>``: field locations, indices
  and rule names, never a value, raised ``from None`` outside any ``except`` block.
- The record models hold the full fixture records, canaries included (prices, costs,
  margins, internal notes, phones, addresses, technician identities, safety notes);
  their sensitive fields stay out of ``repr``. They are not in ``__all__``, and a
  structural test keeps every module outside ``connectors/`` from importing them.
  Their values leave this module only through a projection, with one exception:
  :func:`fixture_strings`, a module function (not a method of the connector objects
  that the executor and the graphs hold) that the gold-set loaders alone may call (a
  structural test checks this). :func:`account_scope_values` is a second such module
  function: it gives the values that identify each account, for the scope index of
  ``privacy/scope.py``; in ``src/`` only :func:`account_scope_index` reads it, the
  entry point of the scope wiring of ``ops_pipeline.py`` (structural tests check both).
  :func:`restricted_values`, :func:`technician_values` and :func:`planted_markers` are
  three more, for the evaluation's exposure and injection counts only
  (``evals/service_eval.py``; a structural test checks this).
- :func:`project_order`, :func:`project_contact` and :func:`project_work_order` are pure.
  They copy only the ADR 0012 view fields and pass every projected free text through
  the caller's quarantine; a safety remark is dropped before quarantine.
- No network call, no clock: ``as_of`` comes from the file. Writes append a
  :class:`SideEffectRecord` (identifiers and a digest) and never store text. The
  simulated classes do not verify tokens (the executor does); they only require one.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from alverno_lab.connectors.types import (
    CATALOGUE_CODES,
    CREATE_WORK_REQUEST_TOOL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    MAX_WORK_ORDER_LIMIT,
    RECORD_REPLY_TOOL,
    SYMPTOM_CODES,
    AccountId,
    ActionCode,
    AssetId,
    AssetView,
    CarrierCode,
    Civility,
    ConnectorError,
    ContactRef,
    ContactView,
    DelayReasonCode,
    FailureCategory,
    FailureCode,
    FailureCodeView,
    OrderId,
    OrderStatus,
    OrderView,
    PartRef,
    Quarantine,
    QuarantinedText,
    ReplyPrepared,
    SideEffectRecord,
    SymptomCode,
    TrackingRef,
    WorkOrderId,
    WorkOrderKind,
    WorkOrderView,
    WorkRequest,
    WriteReceipt,
    WriteToken,
    WriteToolName,
    args_sha256,
)

__all__ = [
    "SimErp",
    "SimGmao",
    "account_scope_index",
    "account_scope_values",
    "fixture_strings",
    "planted_markers",
    "project_contact",
    "project_order",
    "project_work_order",
    "restricted_values",
    "technician_values",
]


# --------------------------------------------------------------------------- fixture records
class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CarrierRecord(_Record):
    """A carrier code and its label (business vocabulary, not PI)."""

    code: CarrierCode
    label: str


class DelayReasonRecord(_Record):
    """A delay reason code and its label."""

    code: DelayReasonCode
    label: str


class AccountRecord(_Record):
    """A customer account; the label of a sole proprietorship is a name."""

    account_id: AccountId
    label: str = Field(repr=False)
    domain: str
    generic_mailbox: str
    natural_person: StrictBool


class ContactRecord(_Record):
    """A named contact of an account; phone and address are never projected."""

    contact_ref: ContactRef
    account_id: AccountId
    civility: Civility
    given_name: str = Field(repr=False)
    surname: str = Field(repr=False)
    function: str = Field(repr=False)
    business_email: str = Field(repr=False)
    cell_phone: str = Field(repr=False)
    home_address: str = Field(repr=False)


class OrderRecord(_Record):
    """A full order line; price, cost, margin and internal note are never projected."""

    order_id: OrderId
    account_id: AccountId
    contact_ref: ContactRef
    part_ref: PartRef
    qty_ordered: StrictInt = Field(ge=1)
    qty_shipped: StrictInt = Field(ge=0)
    status: OrderStatus
    promised_date: dt.date
    revised_date: dt.date | None
    carrier_code: CarrierCode | None
    tracking_ref: TrackingRef | None
    delay_reason_code: DelayReasonCode | None
    price_unit: str = Field(repr=False)
    unit_cost: str = Field(repr=False)
    margin_pct: str = Field(repr=False)
    customer_visible_note: str | None = Field(repr=False)
    internal_note: str | None = Field(repr=False)


class ErpMarker(_Record):
    """A planted injection marker of a customer-visible note (evaluation only)."""

    order_id: OrderId
    marker: str = Field(repr=False)


class ErpFixture(_Record):
    """The whole ``erp.yaml`` file."""

    version: Literal[1]
    as_of: dt.date
    carriers: tuple[CarrierRecord, ...]
    delay_reasons: tuple[DelayReasonRecord, ...]
    accounts: tuple[AccountRecord, ...]
    contacts: tuple[ContactRecord, ...]
    orders: tuple[OrderRecord, ...]
    planted_markers: tuple[ErpMarker, ...]


class FailureCodeRecord(_Record):
    """A catalogue entry."""

    code: FailureCode
    category: FailureCategory
    label: str


class SymptomRecord(_Record):
    """A symptom code, its label and the French keywords that describe it."""

    code: SymptomCode
    label: str
    keywords: tuple[str, ...]


class ActionRecord(_Record):
    """A maintenance action code."""

    code: ActionCode
    label: str


class AssetRecord(_Record):
    """A piece of equipment."""

    asset_id: AssetId
    name: str
    kind: str
    line: str


class WorkOrderRecord(_Record):
    """A full work order; identity, shift, time and downtime are never projected."""

    wo_id: WorkOrderId
    asset_id: AssetId
    opened_on: dt.date
    opened_time: str = Field(repr=False, pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    shift: Literal["jour", "soir", "nuit"] = Field(repr=False)
    kind: WorkOrderKind
    symptom_code: SymptomCode | None
    failure_code: FailureCode | None
    action_code: ActionCode
    technician_name: str = Field(repr=False)
    matricule: str = Field(repr=False)
    initials: str = Field(repr=False)
    login: str = Field(repr=False)
    downtime_min: StrictInt = Field(repr=False, ge=0)
    remark: str | None = Field(repr=False)
    safety: StrictBool


class GmaoMarker(_Record):
    """A planted injection marker of a work-order remark (evaluation only)."""

    wo_id: WorkOrderId
    marker: str = Field(repr=False)


class SpecialRemarks(_Record):
    """Work orders whose remark is a quarantine test case (evaluation only)."""

    initials_or_login: tuple[WorkOrderId, ...]
    small_role_fault: tuple[WorkOrderId, ...]


class GmaoFixture(_Record):
    """The whole ``gmao.yaml`` file."""

    version: Literal[1]
    as_of: dt.date
    failure_codes: tuple[FailureCodeRecord, ...]
    indeterminate_code: Literal["INDETERMINE"]
    symptom_codes: tuple[SymptomRecord, ...]
    action_codes: tuple[ActionRecord, ...]
    assets: tuple[AssetRecord, ...]
    work_orders: tuple[WorkOrderRecord, ...]
    planted_markers: tuple[GmaoMarker, ...]
    special_remarks: SpecialRemarks


# --------------------------------------------------------------------------- projections
def _quarantined(text: str, quarantine: Quarantine) -> QuarantinedText:
    result = quarantine(text)
    if not isinstance(result, QuarantinedText):
        raise ConnectorError("quarantine:invalid_result")
    return result


def project_order(raw: OrderRecord, quarantine: Quarantine) -> OrderView:
    """The ADR 0012 order view; the customer-visible note goes through ``quarantine``."""
    note = raw.customer_visible_note
    return OrderView(
        order_id=raw.order_id,
        account_id=raw.account_id,
        part_ref=raw.part_ref,
        qty_ordered=raw.qty_ordered,
        qty_shipped=raw.qty_shipped,
        status=raw.status,
        promised_date=raw.promised_date,
        revised_date=raw.revised_date,
        carrier_code=raw.carrier_code,
        tracking_ref=raw.tracking_ref,
        delay_reason_code=raw.delay_reason_code,
        customer_visible_note=None if note is None else _quarantined(note, quarantine),
    )


def project_contact(raw_contact: ContactRecord, raw_account: AccountRecord) -> ContactView:
    """The ADR 0012 contact view: no phone, no address."""
    if raw_contact.account_id != raw_account.account_id:
        raise ConnectorError("project_contact:account_mismatch")
    return ContactView(
        contact_ref=raw_contact.contact_ref,
        account_id=raw_contact.account_id,
        account_label=raw_account.label,
        natural_person=raw_account.natural_person,
        function=raw_contact.function,
        civility=raw_contact.civility,
        surname=raw_contact.surname,
        given_name=raw_contact.given_name,
        business_email=raw_contact.business_email,
    )


def project_work_order(raw: WorkOrderRecord, quarantine: Quarantine) -> WorkOrderView:
    """The ADR 0012 history line; a safety remark is dropped, never quarantined."""
    remark = None if raw.safety or raw.remark is None else _quarantined(raw.remark, quarantine)
    return WorkOrderView(
        wo_id=raw.wo_id,
        asset_id=raw.asset_id,
        opened_on=raw.opened_on,
        kind=raw.kind,
        symptom_code=raw.symptom_code,
        failure_code=raw.failure_code,
        action_code=raw.action_code,
        remark=remark,
    )


# --------------------------------------------------------------------------- loading
def _validation_summary(exc: ValidationError) -> str:
    """Field locations and error types only: pydantic messages would echo input values."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['type']}" for e in exc.errors()
    )


def _parse_fixture[M: BaseModel](path: Path, model: type[M]) -> M | str:
    """The validated fixture, or the broken rule. The exception is dropped here."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "file.missing"
    except (OSError, UnicodeDecodeError):
        return "file.unreadable"
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return "yaml.invalid"
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        return f"schema[{_validation_summary(exc)}]"


def _read_fixture[M: BaseModel](path: Path, model: type[M]) -> M:
    # No chaining: a YAMLError shows a snippet of the file and a ValidationError shows
    # input values, so the error is raised outside any except block (``__context__`` is
    # ``None``, not merely suppressed) and ``from None``.
    outcome = _parse_fixture(path, model)
    if isinstance(outcome, str):
        raise ConnectorError(f"{path}:{outcome}") from None
    return outcome


def _duplicates(section: str, field: str, values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    errors: list[str] = []
    for index, value in enumerate(values):
        if value in seen:
            errors.append(f"{section}[{index}].{field}: duplicate")
        seen.add(value)
    return errors


def _erp_errors(fx: ErpFixture) -> list[str]:
    errors = [
        *_duplicates("carriers", "code", (c.code for c in fx.carriers)),
        *_duplicates("delay_reasons", "code", (d.code for d in fx.delay_reasons)),
        *_duplicates("accounts", "account_id", (a.account_id for a in fx.accounts)),
        *_duplicates("contacts", "contact_ref", (c.contact_ref for c in fx.contacts)),
        *_duplicates("orders", "order_id", (o.order_id for o in fx.orders)),
    ]
    accounts = {a.account_id for a in fx.accounts}
    contacts = {c.contact_ref: c for c in fx.contacts}
    carriers = {c.code for c in fx.carriers}
    delays = {d.code for d in fx.delay_reasons}
    for index, record in enumerate(fx.contacts):
        if record.account_id not in accounts:
            errors.append(f"contacts[{index}].account_id: unknown_account")
    for index, order in enumerate(fx.orders):
        where = f"orders[{index}]"
        if order.account_id not in accounts:
            errors.append(f"{where}.account_id: unknown_account")
        contact = contacts.get(order.contact_ref)
        if contact is None:
            errors.append(f"{where}.contact_ref: unknown_contact")
        elif contact.account_id != order.account_id:
            errors.append(f"{where}.contact_ref: other_account")
        if order.carrier_code is not None and order.carrier_code not in carriers:
            errors.append(f"{where}.carrier_code: unknown_carrier")
        if order.delay_reason_code is not None and order.delay_reason_code not in delays:
            errors.append(f"{where}.delay_reason_code: unknown_delay_reason")
        if order.qty_shipped > order.qty_ordered:
            errors.append(f"{where}.qty_shipped: exceeds_qty_ordered")
    notes = {o.order_id: o.customer_visible_note for o in fx.orders}
    for index, planted in enumerate(fx.planted_markers):
        if planted.order_id not in notes:
            errors.append(f"planted_markers[{index}].order_id: unknown_order")
        elif planted.marker not in (notes[planted.order_id] or ""):
            errors.append(f"planted_markers[{index}].marker: not_in_note")
    return errors


def _gmao_errors(fx: GmaoFixture) -> list[str]:
    errors = [
        *_duplicates("failure_codes", "code", (f.code for f in fx.failure_codes)),
        *_duplicates("symptom_codes", "code", (s.code for s in fx.symptom_codes)),
        *_duplicates("action_codes", "code", (a.code for a in fx.action_codes)),
        *_duplicates("assets", "asset_id", (a.asset_id for a in fx.assets)),
        *_duplicates("work_orders", "wo_id", (w.wo_id for w in fx.work_orders)),
    ]
    catalogue = {f.code for f in fx.failure_codes}
    if catalogue != CATALOGUE_CODES:
        errors.append("failure_codes: catalogue_mismatch")
    if {s.code for s in fx.symptom_codes} != SYMPTOM_CODES:
        errors.append("symptom_codes: catalogue_mismatch")
    assets = {a.asset_id for a in fx.assets}
    actions = {a.code for a in fx.action_codes}
    for index, wo in enumerate(fx.work_orders):
        where = f"work_orders[{index}]"
        if wo.asset_id not in assets:
            errors.append(f"{where}.asset_id: unknown_asset")
        if wo.failure_code is not None and wo.failure_code not in catalogue:
            errors.append(f"{where}.failure_code: not_in_catalogue")
        if wo.action_code not in actions:
            errors.append(f"{where}.action_code: unknown_action")
    remarks = {w.wo_id: w.remark for w in fx.work_orders}
    for index, planted in enumerate(fx.planted_markers):
        if planted.wo_id not in remarks:
            errors.append(f"planted_markers[{index}].wo_id: unknown_work_order")
        elif planted.marker not in (remarks[planted.wo_id] or ""):
            errors.append(f"planted_markers[{index}].marker: not_in_remark")
    special = fx.special_remarks
    for name, ids in (
        ("initials_or_login", special.initials_or_login),
        ("small_role_fault", special.small_role_fault),
    ):
        for index, wo_id in enumerate(ids):
            if wo_id not in remarks:
                errors.append(f"special_remarks.{name}[{index}]: unknown_work_order")
    return errors


def _check_integrity(where: str, errors: list[str]) -> None:
    if errors:
        raise ConnectorError(f"{where}:{'; '.join(errors)}")


def _require_quarantine(owner: str, quarantine: object) -> None:
    # Fail closed: no default, and no projection without a quarantine.
    if not callable(quarantine):
        raise ConnectorError(f"{owner}:quarantine_required")


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def _side_effect_or_none(
    tool: WriteToolName, token: WriteToken, payload: BaseModel
) -> SideEffectRecord | None:
    try:
        return SideEffectRecord(
            action_id=token.action_id, tool=tool, args_sha256=args_sha256(payload)
        )
    except ValidationError:
        return None


def _side_effect(tool: WriteToolName, token: WriteToken, payload: BaseModel) -> SideEffectRecord:
    # Raised outside the except block: no chain keeps the token's fields.
    record = _side_effect_or_none(tool, token, payload)
    if record is None:
        raise ConnectorError(f"{tool}:invalid_token") from None
    return record


def _reply_payload(order_id: object, draft_sha256: object) -> ReplyPrepared | None:
    try:
        return ReplyPrepared.model_validate({"order_id": order_id, "draft_sha256": draft_sha256})
    except ValidationError:
        return None


def _receipt(record: SideEffectRecord) -> WriteReceipt:
    return WriteReceipt(
        action_id=record.action_id, tool=record.tool, args_sha256=record.args_sha256
    )


# --------------------------------------------------------------------------- SimErp
class SimErp:
    """The simulated ERP (implements :class:`~alverno_lab.connectors.erp.ErpConnector`)."""

    def __init__(
        self, fixture: ErpFixture, *, quarantine: Quarantine, where: str = "<memory>"
    ) -> None:
        _require_quarantine("SimErp", quarantine)
        _check_integrity(where, _erp_errors(fixture))
        self._fixture = fixture
        self._quarantine: Callable[[str], QuarantinedText] = quarantine
        self._accounts = {a.account_id: a for a in fixture.accounts}
        self._contacts = {c.contact_ref: c for c in fixture.contacts}
        self._orders = {o.order_id: o for o in fixture.orders}
        self._carriers = {c.code: c.label for c in fixture.carriers}
        self._delays = {d.code: d.label for d in fixture.delay_reasons}
        self.executed: list[SideEffectRecord] = []

    @classmethod
    def from_yaml(cls, path: Path, *, quarantine: Quarantine) -> SimErp:
        """Load and validate ``erp.yaml``; raise :class:`ConnectorError` on any broken rule."""
        return cls(_read_fixture(path, ErpFixture), quarantine=quarantine, where=str(path))

    @property
    def as_of(self) -> dt.date:
        """Simulation date of the fixture."""
        return self._fixture.as_of

    @property
    def account_ids(self) -> frozenset[str]:
        """Every account id (for the gold-set loaders' existence checks)."""
        return frozenset(self._accounts)

    @property
    def order_ids(self) -> frozenset[str]:
        """Every order id (for the gold-set loaders' existence checks)."""
        return frozenset(self._orders)

    def get_order(self, order_id: OrderId) -> OrderView | None:
        """The order's view, or ``None`` for an unknown or ill-formed id."""
        raw = self._orders.get(order_id)
        return None if raw is None else project_order(raw, self._quarantine)

    def get_order_contact(self, order_id: OrderId) -> ContactView | None:
        """The order's contact (``compose`` only; not a tool), or ``None``."""
        raw = self._orders.get(order_id)
        if raw is None:
            return None
        contact = self._contacts[raw.contact_ref]
        return project_contact(contact, self._accounts[contact.account_id])

    def carrier_label(self, code: str) -> str | None:
        """The fixture label of a carrier code (read-only), or ``None`` when unknown."""
        return self._carriers.get(code) if isinstance(code, str) else None

    def delay_label(self, code: str) -> str | None:
        """The fixture label of a delay reason code (read-only), or ``None`` when unknown."""
        return self._delays.get(code) if isinstance(code, str) else None

    def record_reply_prepared(
        self, order_id: OrderId, draft_sha256: str, *, token: WriteToken
    ) -> WriteReceipt:
        """Record a prepared reply by digest; no text is stored."""
        tool = RECORD_REPLY_TOOL
        if not isinstance(token, WriteToken):
            raise ConnectorError(f"{tool}:no_token")
        payload = _reply_payload(order_id, draft_sha256)
        if payload is None:
            raise ConnectorError(f"{tool}:invalid_args") from None
        if payload.order_id not in self._orders:
            raise ConnectorError(f"{tool}:unknown_order")
        record = _side_effect(tool, token, payload)
        self.executed.append(record)
        return _receipt(record)


# --------------------------------------------------------------------------- SimGmao
def _check_bound(name: str, value: object, high: int | None) -> int:
    # bool is an int subclass: True must not pass for 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConnectorError(f"gmao.list_work_orders:{name}")
    if high is not None and value > high:
        raise ConnectorError(f"gmao.list_work_orders:{name}")
    return value


class SimGmao:
    """The simulated GMAO (implements :class:`~alverno_lab.connectors.gmao.GmaoConnector`)."""

    def __init__(
        self, fixture: GmaoFixture, *, quarantine: Quarantine, where: str = "<memory>"
    ) -> None:
        _require_quarantine("SimGmao", quarantine)
        _check_integrity(where, _gmao_errors(fixture))
        self._fixture = fixture
        self._quarantine: Callable[[str], QuarantinedText] = quarantine
        self._assets = {a.asset_id: a for a in fixture.assets}
        self._work_orders = {w.wo_id: w for w in fixture.work_orders}
        # History of each asset, newest first, ties broken by wo_id descending.
        history: dict[str, list[WorkOrderRecord]] = {a: [] for a in self._assets}
        for wo in fixture.work_orders:
            history[wo.asset_id].append(wo)
        self._history = {
            asset: tuple(sorted(rows, key=lambda w: (w.opened_on, w.wo_id), reverse=True))
            for asset, rows in history.items()
        }
        self.executed: list[SideEffectRecord] = []

    @classmethod
    def from_yaml(cls, path: Path, *, quarantine: Quarantine) -> SimGmao:
        """Load and validate ``gmao.yaml``; raise :class:`ConnectorError` on any broken rule."""
        return cls(_read_fixture(path, GmaoFixture), quarantine=quarantine, where=str(path))

    @property
    def as_of(self) -> dt.date:
        """Simulation date of the fixture."""
        return self._fixture.as_of

    @property
    def asset_ids(self) -> frozenset[str]:
        """Every asset id (for the gold-set loaders' existence checks)."""
        return frozenset(self._assets)

    @property
    def work_order_ids(self) -> frozenset[str]:
        """Every work order id."""
        return frozenset(self._work_orders)

    def get_asset(self, asset_id: AssetId) -> AssetView | None:
        """The asset's view, or ``None`` for an unknown or ill-formed id."""
        raw = self._assets.get(asset_id)
        if raw is None:
            return None
        return AssetView(asset_id=raw.asset_id, name=raw.name, kind=raw.kind, line=raw.line)

    def list_work_orders(
        self,
        asset_id: AssetId,
        symptom_code: SymptomCode | None,
        since_days: int = DEFAULT_HISTORY_DAYS,
        limit: int = DEFAULT_WORK_ORDER_LIMIT,
    ) -> tuple[WorkOrderView, ...]:
        """Work orders opened on or after ``as_of - since_days``, newest first, ``limit`` max.

        ``since_days`` must be at least 1 and ``limit`` between 1 and 12; a symptom code
        outside the closed list is refused. An unknown asset gives an empty tuple.
        """
        days = _check_bound("since_days", since_days, None)
        count = _check_bound("limit", limit, MAX_WORK_ORDER_LIMIT)
        if symptom_code is not None and symptom_code not in SYMPTOM_CODES:
            raise ConnectorError("gmao.list_work_orders:symptom_code")
        rows = self._history.get(asset_id, ())
        span = (self.as_of - dt.date.min).days
        start = dt.date.min if days >= span else self.as_of - dt.timedelta(days=days)
        selected = [
            wo
            for wo in rows
            if wo.opened_on >= start and (symptom_code is None or wo.symptom_code == symptom_code)
        ]
        return tuple(project_work_order(wo, self._quarantine) for wo in selected[:count])

    def failure_catalog(self) -> tuple[FailureCodeView, ...]:
        """The 16 catalogue codes, in fixture order (``INDETERMINE`` is not an entry)."""
        return tuple(
            FailureCodeView(code=f.code, category=f.category, label=f.label)
            for f in self._fixture.failure_codes
        )

    def create_work_request(self, req: WorkRequest, *, token: WriteToken) -> WriteReceipt:
        """Open a work request; every cited work order must belong to the asset."""
        tool = CREATE_WORK_REQUEST_TOOL
        if not isinstance(token, WriteToken):
            raise ConnectorError(f"{tool}:no_token")
        if not isinstance(req, WorkRequest):
            raise ConnectorError(f"{tool}:invalid_args")
        if req.asset_id not in self._assets:
            raise ConnectorError(f"{tool}:unknown_asset")
        for wo_id in req.cited_wo_ids:
            wo = self._work_orders.get(wo_id)
            if wo is None:
                raise ConnectorError(f"{tool}:unknown_work_order")
            if wo.asset_id != req.asset_id:
                raise ConnectorError(f"{tool}:foreign_work_order")
        record = _side_effect(tool, token, req)
        self.executed.append(record)
        return _receipt(record)


# --------------------------------------------------------------------------- loaders only
def fixture_strings(connector: SimErp | SimGmao) -> frozenset[str]:
    """Every string leaf of a simulated connector's fixture, canaries included.

    For the gold-set loaders only (« every canary is a verbatim fixture value »): never
    projected, never logged, and not reachable from the connector object itself. A
    structural test allows no other caller in ``src/``.
    """
    return frozenset(_strings(connector._fixture.model_dump()))


# A trailing qualifier of a fixture label (« (fictive) »): the label is also indexed
# without it, as a request or a draft would write it.
_LABEL_QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")
# Leading function words of a label tail (« Charpentes du Versant Gris » -> « Versant
# Gris »), and hyphenated proper-noun compounds (« Rive-Clair »).
_TAIL_LEAD_RE = re.compile(r"^(?:(?:du|de|des|la|le|les)\s+|l'|d')+", re.IGNORECASE)
_COMPOUND_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")


def _label_short_forms(label: str) -> set[str]:
    """How a label is written for short: its distinctive tail and its compounds.

    The tail is the label without its qualifier, its first (generic) word and the
    function words after it; it is kept when it has a hyphen or two words or more, so a
    lone common word never becomes a value. Every hyphenated compound is kept too. A
    value shared with another account never flags (``privacy.scope``).
    """
    core = _LABEL_QUALIFIER_RE.sub("", label)
    forms = set(_COMPOUND_RE.findall(core))
    _, _, rest = core.partition(" ")
    tail = _TAIL_LEAD_RE.sub("", rest).strip()
    if "-" in tail or " " in tail:
        forms.add(tail)
    return forms


def account_scope_values(erp: SimErp) -> dict[str, frozenset[str]]:
    """Per account id, the values that identify the account (``privacy.scope`` index).

    The account id, its label (also without a trailing qualifier such as « (fictive) »,
    and in its short forms: the distinctive tail and the hyphenated compounds), its
    e-mail domain, every contact's surname, « given_name surname », business e-mail
    and contact ref, and the id and tracking ref of every order. Phones and home
    addresses are not needed: they are never projected. Like :func:`fixture_strings`, a
    module function, not a method of the connector objects; only the scope wiring reads
    it (a structural test checks this).
    """
    fixture = erp._fixture
    values: dict[str, set[str]] = {}
    for account in fixture.accounts:
        values[account.account_id] = {
            account.account_id,
            account.label,
            _LABEL_QUALIFIER_RE.sub("", account.label),
            *_label_short_forms(account.label),
            account.domain,
        }
    for contact in fixture.contacts:
        values[contact.account_id].update(
            {
                contact.surname,
                f"{contact.given_name} {contact.surname}",
                contact.business_email,
                contact.contact_ref,
            }
        )
    for order in fixture.orders:
        values[order.account_id].add(order.order_id)
        if order.tracking_ref is not None:
            values[order.account_id].add(order.tracking_ref)
    return {account_id: frozenset(found) for account_id, found in values.items()}


def account_scope_index[T](erp: SimErp, build: Callable[[dict[str, frozenset[str]]], T]) -> T:
    """The scope index of every account of ``erp``: ``build`` over :func:`account_scope_values`.

    ``connectors`` imports nothing outside itself, so the caller passes the index builder
    (``privacy.scope.build_scope_index``). This is the scope wiring's only way to the
    account values; a structural test keeps its readers to ``ops_pipeline.py``.
    """
    return build(account_scope_values(erp))


# --------------------------------------------------------------------------- metrics only
def _decimal_forms(value: str) -> set[str]:
    """A fixture decimal as it could be written: as stored, and with a decimal comma."""
    return {value, value.replace(".", ",")}


def restricted_values(erp: SimErp) -> frozenset[str]:
    """Every price, unit cost and margin of the ERP fixture, as they could appear in a text.

    Each value is given as stored (« 792.68 ») and with a decimal comma (« 792,68 »); a
    currency or percent sign after it does not hide it from a bounded match. For the
    evaluation's ``restricted_field_exposure`` only: like :func:`fixture_strings`, a module
    function that no connector object exposes, and a structural test allows no reader in
    ``src/`` but ``evals/service_eval.py``.
    """
    values: set[str] = set()
    for order in erp._fixture.orders:
        for raw in (order.price_unit, order.unit_cost, order.margin_pct):
            values |= _decimal_forms(raw)
    return frozenset(values)


def technician_values(gmao: SimGmao) -> frozenset[str]:
    """Every technician identity of the GMAO fixture: name, matricule, initials, login.

    For the evaluation's ``technician_identity_exposure`` only (same reader rule as
    :func:`restricted_values`).
    """
    values: set[str] = set()
    for wo in gmao._fixture.work_orders:
        values |= {wo.technician_name, wo.matricule, wo.initials, wo.login}
    return frozenset(values)


def planted_markers(connector: SimErp | SimGmao) -> frozenset[str]:
    """The injection marker phrases planted in a fixture's free texts (evaluation only).

    For the evaluation's ``injection_echo_count``, which looks for them in served texts
    whether or not the quarantine reacted (same reader rule as :func:`restricted_values`).
    """
    return frozenset(planted.marker for planted in connector._fixture.planted_markers)
