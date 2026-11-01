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
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alverno_lab.privacy.types import Kind, PIType, RefusalCode, Subject

MAX_RETENTION_DAYS = 90
_DAY_FILE_RE = re.compile(r"^hr-(\d{4}-\d{2}-\d{2})\.jsonl$")
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
    channel: Literal["cli", "eval"]
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


def _fields_summary(exc: ValidationError) -> str:
    """Field locations and error types only: pydantic messages would echo input values."""
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors())


def _as_utc(moment: dt.datetime) -> dt.datetime:
    """``moment`` in UTC; a naive datetime is taken as UTC (never as local time)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.UTC)
    return moment.astimezone(dt.UTC)


def hour_stamp(moment: dt.datetime) -> str:
    """``YYYY-MM-DDTHH:00Z`` in UTC; a naive datetime is taken as UTC."""
    return _as_utc(moment).strftime("%Y-%m-%dT%H:00Z")


class AccessLog:
    """Append-only day files under ``directory``, purged past the retention period."""

    def __init__(
        self,
        directory: Path,
        *,
        retention_days: int = MAX_RETENTION_DAYS,
        clock: Callable[[], dt.datetime] = utc_now,
    ) -> None:
        if not 1 <= retention_days <= MAX_RETENTION_DAYS:
            raise AccessLogError(f"access log retention must be 1 to {MAX_RETENTION_DAYS} days")
        self.directory = directory
        self.retention_days = retention_days
        self._clock = clock

    def path_for(self, day: dt.date) -> Path:
        """Day file of ``day`` (UTC)."""
        return self.directory / f"hr-{day.isoformat()}.jsonl"

    def record(self, **fields: object) -> AccessLogRecord:
        """A validated record; ``event_id`` (random) and ``ts`` (this hour) are filled in."""
        if _LOG_FIELDS & fields.keys():
            raise AccessLogError("event_id and ts are set by the access log")
        values = {**fields, "event_id": uuid.uuid4().hex, "ts": hour_stamp(self._clock())}
        try:
            return AccessLogRecord.model_validate(values)
        except ValidationError as exc:
            # No chaining: the ValidationError text would show the rejected values.
            raise AccessLogError(f"invalid access log record ({_fields_summary(exc)})") from None

    def append(self, rec: AccessLogRecord) -> None:
        """Purge expired files, then write ``rec`` to its day file.

        The purge runs first: when it fails, nothing is written, so no record says
        « answered » for a request that is then served ``audit_unavailable``.
        """
        try:
            checked = AccessLogRecord.model_validate(rec.model_dump())
        except ValidationError as exc:
            raise AccessLogError(f"invalid access log record ({_fields_summary(exc)})") from None
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
            if match is None or not path.is_file():
                continue
            try:
                day = dt.date.fromisoformat(match.group(1))
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

    def read(self, day: dt.date) -> list[AccessLogRecord]:
        """Records of one UTC day; an absent file means no request that day."""
        path = self.path_for(day)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise AccessLogError(f"access log not readable: {path}") from exc
        records: list[AccessLogRecord] = []
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(AccessLogRecord.model_validate_json(line))
            except ValidationError as exc:
                raise AccessLogError(
                    f"{path}:{lineno}: invalid access log record ({_fields_summary(exc)})"
                ) from None
        return records
