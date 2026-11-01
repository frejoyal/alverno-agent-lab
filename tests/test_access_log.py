"""HR access log (spec §5.8 with M11, ADR 0008): schema, day files, purge, fail-closed.

Records hold metadata only; the tests also write one record per gold-set screening and
scan the files for every canary of the set.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from alverno_lab.cli import USER_ERRORS, app
from alverno_lab.config import Settings
from alverno_lab.evals.hr import gold_canaries, load_hr_gold
from alverno_lab.privacy import (
    AccessLog,
    AccessLogError,
    AccessLogRecord,
    PrivacyContext,
    scan_paths,
    screen,
)
from alverno_lab.privacy.access_log import PI_COUNT_CAP, hour_stamp

from .conftest import EVAL_DATA

GOLD = EVAL_DATA / "hr_gold.jsonl"
NOW = dt.datetime(2026, 11, 1, 14, 37, 12, tzinfo=dt.UTC)
FIELDS = {
    "v",
    "event_id",
    "ts",
    "module",
    "channel",
    "decision",
    "reason",
    "kind",
    "subject",
    "requested_level",
    "pi_types",
    "pi_count",
    "doc_ids",
    "max_doc_level",
    "model",
    "latency_ms",
    "actor",
}
VALID = {"channel": "cli", "decision": "answered", "latency_ms": 1200}


class Clock:
    """A settable clock, injected into the log."""

    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def log(tmp_path: Path, clock: Clock) -> AccessLog:
    return AccessLog(tmp_path / "logs" / "access", clock=clock)


def day_file(log: AccessLog, day: dt.date) -> Path:
    path = log.path_for(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- schema
def test_record_fills_a_random_id_and_the_hour(log: AccessLog) -> None:
    first, second = log.record(**VALID), log.record(**VALID)
    assert first.ts == "2026-11-01T14:00Z"
    assert len(first.event_id) == 32 and first.event_id != second.event_id
    assert set(AccessLogRecord.model_fields) == FIELDS
    assert first.actor is None and first.module == "hr" and first.v == 1


@pytest.mark.parametrize(
    ("moment", "stamp"),
    [
        (dt.datetime(2026, 11, 1, 10, 59, tzinfo=dt.timezone(dt.timedelta(hours=-4))), "14:00Z"),
        (dt.datetime(2026, 11, 1, 14, 0, 0), "14:00Z"),  # naive: taken as UTC
        (dt.datetime(2026, 11, 1, 14, 59, 59, 999999, tzinfo=dt.UTC), "14:00Z"),
    ],
)
def test_timestamps_are_rounded_to_the_hour_in_utc(moment: dt.datetime, stamp: str) -> None:
    assert hour_stamp(moment) == f"2026-11-01T{stamp}"


@pytest.mark.parametrize(
    "ts", ["2026-11-01T14:37Z", "2026-11-01T14:00:00Z", "2026-11-01 14:00Z", "14:00Z"]
)
def test_minute_or_free_timestamps_are_rejected(ts: str) -> None:
    with pytest.raises(ValidationError):
        AccessLogRecord(event_id="0" * 32, ts=ts, channel="cli", decision="answered", latency_ms=0)


@pytest.mark.parametrize(
    "extra",
    [
        {"question": "Combien de jours de vacances me reste-t-il ?"},
        {"redacted": "Mon NAS [NAS]"},
        {"question_sha256": "ab" * 32},
        {"question_length": 42},
        {"answer": "Texte de la réponse"},
        {"host": "poste-01"},
        {"model": "un modèle avec du texte libre"},
        {"doc_ids": ["RH-005", "Isabelle Lavoie"]},
        {"reason": "parce que Marc Bergeron a demandé"},
        {"kind": "personne"},
        {"pi_count": 101},
        {"requested_level": 6},
        {"actor": "employe-42"},
        {"channel": "web"},
    ],
)
def test_extra_fields_and_free_text_are_rejected(log: AccessLog, extra: dict[str, object]) -> None:
    with pytest.raises(AccessLogError) as caught:
        log.record(**{**VALID, **extra})
    message = str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    for value in extra.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in message


def test_the_log_sets_id_and_time_itself(log: AccessLog) -> None:
    with pytest.raises(AccessLogError):
        log.record(**VALID, ts="2026-11-01T14:00Z")
    with pytest.raises(AccessLogError):
        log.record(**VALID, event_id="0" * 32)


def test_records_are_frozen(log: AccessLog) -> None:
    rec = log.record(**VALID)
    with pytest.raises(ValidationError):
        rec.decision = "refused"  # type: ignore[misc]


# --------------------------------------------------------------------------- day files
def test_records_go_to_daily_utc_files(log: AccessLog, clock: Clock) -> None:
    log.append(log.record(**VALID, doc_ids=("RH-005",), model="gemma4:e4b"))
    # 23:30 in Québec (UTC-5 in November) is already the next UTC day.
    clock.now = dt.datetime(2026, 11, 1, 23, 30, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
    log.append(log.record(channel="eval", decision="refused", reason="own_record", latency_ms=3))
    names = sorted(p.name for p in log.directory.iterdir())
    assert names == ["hr-2026-11-01.jsonl", "hr-2026-11-02.jsonl"]
    first = log.read(dt.date(2026, 11, 1))
    assert [r.doc_ids for r in first] == [("RH-005",)]
    second = log.read(dt.date(2026, 11, 2))
    assert second[0].ts == "2026-11-02T04:00Z" and second[0].reason == "own_record"
    assert log.read(dt.date(2026, 10, 1)) == []
    line = log.path_for(dt.date(2026, 11, 1)).read_text(encoding="utf-8").splitlines()[0]
    assert set(json.loads(line)) == FIELDS


def test_append_rejects_a_record_built_without_validation(log: AccessLog) -> None:
    forged = AccessLogRecord.model_construct(
        event_id="0" * 32, ts="2026-11-01T14:37Z", channel="cli", decision="answered"
    )
    with pytest.raises(AccessLogError):
        log.append(forged)
    assert not log.directory.exists()


def test_read_rejects_a_tampered_line_without_echoing_it(log: AccessLog) -> None:
    path = day_file(log, NOW.date())
    path.write_text('{"v": 1, "question": "Isabelle Lavoie"}\n', encoding="utf-8")
    with pytest.raises(AccessLogError) as caught:
        log.read(NOW.date())
    assert "Isabelle" not in str(caught.value) and f"{path}:1" in str(caught.value)


# --------------------------------------------------------------------------- retention
def test_purge_uses_the_injected_clock(log: AccessLog, clock: Clock) -> None:
    today = NOW.date()
    old = day_file(log, today - dt.timedelta(days=91))
    older = day_file(log, today - dt.timedelta(days=200))
    kept = [day_file(log, today - dt.timedelta(days=d)) for d in (90, 1, 0)]
    other = log.directory / "notes.txt"
    other.write_text("", encoding="utf-8")
    odd = log.directory / "hr-2026-13-45.jsonl"
    odd.write_text("", encoding="utf-8")
    assert log.purge(dry_run=True) == [older, old]
    assert older.exists() and old.exists()
    assert log.purge() == [older, old]
    assert not older.exists() and not old.exists()
    assert all(p.exists() for p in [*kept, other, odd])
    # One day later, the 90-day-old file expires too, on the next append.
    clock.now = NOW + dt.timedelta(days=1)
    log.append(log.record(**VALID))
    assert not kept[0].exists() and kept[1].exists()


def test_purge_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert AccessLog(tmp_path / "absent").purge() == []


@pytest.mark.parametrize("days", [0, -1, 91, 365])
def test_retention_outside_1_to_90_days_is_rejected(tmp_path: Path, days: int) -> None:
    with pytest.raises(AccessLogError):
        AccessLog(tmp_path, retention_days=days)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, access_log_retention_days=days)


def test_short_retention(log: AccessLog, tmp_path: Path, clock: Clock) -> None:
    short = AccessLog(log.directory, retention_days=1, clock=clock)
    yesterday = day_file(log, NOW.date() - dt.timedelta(days=1))
    before = day_file(log, NOW.date() - dt.timedelta(days=2))
    assert short.purge() == [before] and yesterday.exists()


# --------------------------------------------------------------------------- fail closed
def test_unwritable_directory_becomes_an_access_log_error(tmp_path: Path, clock: Clock) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("not a directory", encoding="utf-8")
    log = AccessLog(blocker / "access", clock=clock)
    with pytest.raises(AccessLogError) as caught:
        log.append(log.record(**VALID))
    assert isinstance(caught.value.__cause__, OSError)


def test_failed_write_becomes_an_access_log_error(
    log: AccessLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    rec = log.record(**VALID)
    monkeypatch.setattr(Path, "open", refuse)
    with pytest.raises(AccessLogError, match="not writable"):
        log.append(rec)


def test_failed_read_and_unlink_become_access_log_errors(
    log: AccessLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    day_file(log, NOW.date() - dt.timedelta(days=120))

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(AccessLogError, match="not removable"):
        log.purge()
    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(AccessLogError, match="not readable"):
        log.read(NOW.date())
    monkeypatch.setattr(Path, "iterdir", refuse)
    with pytest.raises(AccessLogError, match="not readable"):
        log.purge(dry_run=True)


# --------------------------------------------------------------------------- no canary
def test_no_canary_is_ever_written(
    log: AccessLog, privacy_ctx: PrivacyContext, clock: Clock
) -> None:
    items = load_hr_gold(GOLD)
    for n, item in enumerate(items):
        clock.now = NOW + dt.timedelta(hours=n)  # spread over several day files
        screening = screen(item.question, privacy_ctx)
        a = screening.assessment
        log.append(
            log.record(
                channel="eval",
                decision="refused" if a.refusal else "answered",
                reason=a.refusal,
                kind=a.kind,
                subject=a.subject,
                requested_level=a.requested_level,
                pi_types=tuple(sorted({s.type for s in screening.spans})),
                pi_count=len(screening.spans),
                doc_ids=tuple(item.expected_doc_ids),
                max_doc_level=2 if item.expected_doc_ids else None,
                model="gemma4:e4b",
                latency_ms=n,
            )
        )
    files = sorted(log.directory.iterdir())
    assert len(files) == 4  # 64 hours from 14:00 UTC
    canaries = gold_canaries(items)
    assert len(canaries) >= 13
    assert scan_paths([log.directory], canaries) == []
    text = "\n".join(p.read_text(encoding="utf-8") for p in files)
    assert sum(1 for line in text.splitlines() if line) == len(items)
    for item in items:
        assert item.question not in text
    assert "[NOM]" not in text and "[NAS]" not in text


# --------------------------------------------------------------------------- CLI
def old_files(directory: Path, days: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=days)
    path = directory / f"hr-{day.isoformat()}.jsonl"
    path.write_text("", encoding="utf-8")
    return path


def test_cli_access_purge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "access"
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_DIR", str(directory))
    old, recent = old_files(directory, 120), old_files(directory, 3)
    runner = CliRunner()
    dry = runner.invoke(app, ["access", "purge", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "Would remove 1 day file(s) older than 90 days" in dry.output
    assert old.exists()
    done = runner.invoke(app, ["access", "purge"])
    assert done.exit_code == 0, done.output
    assert "Removed 1 day file(s)" in done.output
    assert not old.exists() and recent.exists()


def test_cli_access_purge_exits_2_on_access_log_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "access"
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_DIR", str(directory))
    old_files(directory, 120)

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse)
    result = CliRunner().invoke(app, ["access", "purge"])
    assert result.exit_code == 2
    assert "not removable" in result.output
    assert AccessLogError in USER_ERRORS


def test_cli_access_purge_exits_2_on_invalid_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALVERNO_ACCESS_LOG_RETENTION_DAYS", "365")
    result = CliRunner().invoke(app, ["access", "purge"])
    assert result.exit_code == 2


@pytest.mark.parametrize("hour", [0, 23])
def test_purge_reads_a_naive_clock_as_utc_like_the_timestamps(
    log: AccessLog, clock: Clock, hour: int
) -> None:
    # hour_stamp takes a naive datetime as UTC; the purge cutoff must use the same day,
    # never the local time zone (near midnight, the two would disagree on the day).
    clock.now = dt.datetime(2026, 11, 1, hour, 30)
    today = clock.now.date()
    kept = day_file(log, today - dt.timedelta(days=90))
    old = day_file(log, today - dt.timedelta(days=91))
    assert log.record(**VALID).day == today
    assert log.expired() == [old]
    assert kept.exists()


def test_pi_count_cap_is_the_schema_bound(log: AccessLog) -> None:
    # Callers store min(len(spans), PI_COUNT_CAP): 100 means « 100 or more ».
    assert PI_COUNT_CAP == 100
    assert log.record(**VALID, pi_count=PI_COUNT_CAP).pi_count == PI_COUNT_CAP
    with pytest.raises(AccessLogError):
        log.record(**VALID, pi_count=PI_COUNT_CAP + 1)
