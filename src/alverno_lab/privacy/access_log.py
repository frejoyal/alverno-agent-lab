"""Minimal access log of the HR module: one frozen record per request (spec §5.8, ADR 0008).

A record holds metadata only: the decision, the refusal reason, the classification, the
PI types removed (never their values or offsets), the cited documents, the model and the
latency. The question, any digest, prefix or length of it, the answer and the extracts
are never stored; ``extra="forbid"`` and the field patterns reject free text.

Day files ``hr-YYYY-MM-DD.jsonl`` are dated in UTC and kept ``retention_days`` days (90 at
most, chosen at design, to be confirmed by the RPRP); older files are deleted on every
append and by ``alverno access purge``. Logging fails closed: any write or validation
error becomes an :class:`AccessLogError`, whose message names a path or a field, never a
value.

Module 3 (ADR 0014) reuses the same class with a file prefix and a record type:
``AccessLog(dir, prefix="service", record_type=OpsAccessLogRecord)`` writes
``service-YYYY-MM-DD.jsonl`` (``maint`` likewise). ``AccessLog(dir)`` keeps the HR prefix,
record model and behaviour. Each instance reads, writes and purges only the files of its
own prefix, and refuses a record whose ``module`` is not its prefix. The class is generic
over its record type, ``AccessLogRecord`` by default, and the constructor overloads tie
each prefix to its record type for mypy; an incompatible pair fails at construction.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Generic, Literal, cast, get_args, overload

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.privacy.types import Channel, Kind, PIType, RefusalCode, Subject

if TYPE_CHECKING:
    # The default of a type variable (PEP 696) is in ``typing`` from Python 3.13 only;
    # mypy reads it from the stubs, the runtime never imports typing_extensions.
    from typing_extensions import TypeVar

    from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord
else:
    from typing import TypeVar

MAX_RETENTION_DAYS = 90
# File prefix of each module: hr (ADR 0008), service and maint (ADR 0014).
AccessLogPrefix = Literal["hr", "service", "maint"]
ACCESS_LOG_PREFIXES: tuple[AccessLogPrefix, ...] = get_args(AccessLogPrefix)
_DAY_FILE_RE = re.compile(r"^(hr|service|maint)-(\d{4}-\d{2}-\d{2})\.jsonl$")
# Fields the log fills itself.
_LOG_FIELDS = frozenset({"event_id", "ts"})
# Highest pi_count a record accepts. Callers pass ``min(len(spans), PI_COUNT_CAP)``, so
# that 100 means « 100 or more »: a pasted document can hold thousands of spans, which
# must not turn a request into audit_unavailable.
PI_COUNT_CAP = 100


class AccessLogError(RuntimeError):
    """The access log cannot be written or read; the request must not be served."""


def utc_now() -> dt.datetime:
    """Current time, timezone-aware, in UTC."""
    return dt.datetime.now(dt.UTC)


DocId = Annotated[str, Field(pattern=r"^[A-Z0-9-]{2,32}$")]
# A loggable model name; the HR factories check the chat model against it first.
MODEL_PATTERN = r"^[\w.:/-]{1,64}$"


class AccessLogRecord(BaseModel):
    """One request, as metadata. The time is rounded to the hour (M11)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    v: Literal[1] = 1
    event_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:00Z$")
    module: Literal["hr"] = "hr"
    channel: Channel
    decision: Literal["answered", "refused", "error"]
    reason: RefusalCode | None = None
    kind: Kind | None = None
    subject: Subject | None = None
    requested_level: int | None = Field(default=None, ge=1, le=5)
    pi_types: tuple[PIType, ...] = ()
    pi_count: int = Field(default=0, ge=0, le=PI_COUNT_CAP)
    doc_ids: tuple[DocId, ...] = ()
    max_doc_level: int | None = Field(default=None, ge=1, le=5)
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    latency_ms: int = Field(ge=0)
    actor: None = None

    @property
    def day(self) -> dt.date:
        """UTC day of the record, which names its file."""
        return dt.date.fromisoformat(self.ts[:10])


if TYPE_CHECKING:
    R = TypeVar("R", AccessLogRecord, OpsAccessLogRecord, default=AccessLogRecord)
else:
    R = TypeVar("R")


def _record_type_of(prefix: str) -> type[BaseModel] | None:
    """The record model of a file prefix, or None for an unknown prefix."""
    # Imported here: ``ops_access_log`` imports the constants of this module.
    from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord

    types: dict[str, type[BaseModel]] = {
        "hr": AccessLogRecord,
        "service": OpsAccessLogRecord,
        "maint": OpsAccessLogRecord,
    }
    return types.get(prefix)


def _fields_summary(exc: ValidationError, *, no_loc: str = "") -> str:
    """Field locations and error types only: pydantic messages would echo input values.

    An error without a location (a cross-field rule, a line that is not a JSON object)
    is shown as ``no_loc``: empty for the HR log, as before module 3, and ``<record>``
    for the ops logs, whose cross-field rules name themselves in the error type.
    """
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or no_loc}: {e['type']}" for e in exc.errors())


def _as_utc(moment: dt.datetime) -> dt.datetime:
    """``moment`` in UTC; a naive datetime is taken as UTC (never as local time)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.UTC)
    return moment.astimezone(dt.UTC)


def hour_stamp(moment: dt.datetime) -> str:
    """``YYYY-MM-DDTHH:00Z`` in UTC; a naive datetime is taken as UTC."""
    return _as_utc(moment).strftime("%Y-%m-%dT%H:00Z")


# Generic[R], not PEP 695 parameters: the default of R needs Python 3.13 syntax.
class AccessLog(Generic[R]):  # noqa: UP046
    """Append-only day files under ``directory``, purged past the retention period.

    ``prefix`` names the files (``<prefix>-YYYY-MM-DD.jsonl``) and ``record_type`` their
    model: ``hr`` with :class:`AccessLogRecord` (the default), ``service`` or ``maint``
    with :class:`~alverno_lab.privacy.ops_access_log.OpsAccessLogRecord`.
    """

    @overload
    def __init__(
        self: AccessLog[AccessLogRecord],
        directory: Path,
        *,
        prefix: Literal["hr"] = ...,
        record_type: type[AccessLogRecord] = ...,
        retention_days: int = ...,
        clock: Callable[[], dt.datetime] = ...,
    ) -> None: ...

    @overload
    def __init__(
        self: AccessLog[OpsAccessLogRecord],
        directory: Path,
        *,
        prefix: Literal["service", "maint"],
        record_type: type[OpsAccessLogRecord],
        retention_days: int = ...,
        clock: Callable[[], dt.datetime] = ...,
    ) -> None: ...

    def __init__(
        self,
        directory: Path,
        *,
        prefix: AccessLogPrefix = "hr",
        record_type: type[AccessLogRecord] | type[OpsAccessLogRecord] = AccessLogRecord,
        retention_days: int = MAX_RETENTION_DAYS,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        if not 1 <= retention_days <= MAX_RETENTION_DAYS:
            raise AccessLogError(f"access log retention must be 1 to {MAX_RETENTION_DAYS} days")
        expected = _record_type_of(prefix)
        if expected is None or record_type is not expected:
            raise AccessLogError("access log prefix and record type do not match")
        self.directory = directory
        self.prefix: AccessLogPrefix = prefix
        self.record_type: type[R] = cast("type[R]", record_type)
        self.retention_days = retention_days
        self._clock = clock
        # HR error texts stay byte-identical to module 2 (see ``_fields_summary``).
        self._no_loc = "" if prefix == "hr" else "<record>"

    def path_for(self, day: dt.date) -> Path:
        """Day file of ``day`` (UTC)."""
        return self.directory / f"{self.prefix}-{day.isoformat()}.jsonl"

    def _own(self, rec: R) -> R:
        """``rec`` when its module is this log's prefix; an :class:`AccessLogError` if not."""
        if rec.module != self.prefix:
            raise AccessLogError("access log record module does not match the file prefix")
        return rec

    def record(self, **fields: object) -> R:
        """A validated record; ``event_id`` (random) and ``ts`` (this hour) are filled in.

        ``module`` defaults to the prefix; a record of another module is refused.
        """
        if _LOG_FIELDS & fields.keys():
            raise AccessLogError("event_id and ts are set by the access log")
        values = {
            "module": self.prefix,
            **fields,
            "event_id": uuid.uuid4().hex,
            "ts": hour_stamp(self._clock()),
        }
        try:
            rec = self.record_type.model_validate(values)
        except ValidationError as exc:
            # No chaining: the ValidationError text would show the rejected values.
            summary = _fields_summary(exc, no_loc=self._no_loc)
            raise AccessLogError(f"invalid access log record ({summary})") from None
        return self._own(rec)

    def append(self, rec: R) -> None:
        """Purge expired files, then write ``rec`` to its day file.

        The purge runs first: when it fails, nothing is written, so no record says
        « answered » for a request that is then served ``audit_unavailable``.
        """
        try:
            checked = self.record_type.model_validate(rec.model_dump())
        except ValidationError as exc:
            summary = _fields_summary(exc, no_loc=self._no_loc)
            raise AccessLogError(f"invalid access log record ({summary})") from None
        self._own(checked)
        self.purge()
        path = self.path_for(checked.day)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(checked.model_dump_json() + "\n")
        except OSError as exc:
            raise AccessLogError(f"access log not writable: {path}") from exc

    def expired(self) -> list[Path]:
        """Day files older than the retention period, oldest first."""
        if not self.directory.is_dir():
            return []
        today = _as_utc(self._clock()).date()
        cutoff = today - dt.timedelta(days=self.retention_days)
        found: list[tuple[dt.date, Path]] = []
        try:
            entries = list(self.directory.iterdir())
        except OSError as exc:
            raise AccessLogError(f"access log not readable: {self.directory}") from exc
        for path in entries:
            match = _DAY_FILE_RE.match(path.name)
            # Only this instance's prefix: another module's files keep their own retention.
            if match is None or match.group(1) != self.prefix or not path.is_file():
                continue
            try:
                day = dt.date.fromisoformat(match.group(2))
            except ValueError:
                continue
            if day < cutoff:
                found.append((day, path))
        return [p for _, p in sorted(found)]

    def purge(self, *, dry_run: bool = False) -> list[Path]:
        """Delete expired day files (only list them with ``dry_run``); return them."""
        paths = self.expired()
        if dry_run:
            return paths
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AccessLogError(f"access log file not removable: {path}") from exc
        return paths

    def read(self, day: dt.date) -> list[R]:
        """Records of one UTC day; an absent file means no request that day."""
        path = self.path_for(day)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise AccessLogError(f"access log not readable: {path}") from exc
        records: list[R] = []
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                rec = self.record_type.model_validate_json(line)
            except ValidationError as exc:
                summary = _fields_summary(exc, no_loc=self._no_loc)
                raise AccessLogError(
                    f"{path}:{lineno}: invalid access log record ({summary})"
                ) from None
            if rec.module != self.prefix:
                raise AccessLogError(f"{path}:{lineno}: access log record of another module")
            records.append(rec)
        return records
