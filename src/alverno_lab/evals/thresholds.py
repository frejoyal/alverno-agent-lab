"""Release gate: compare a report (Quality, HR or detector) with ``evals/thresholds.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class GateReport(Protocol):
    """What the gate reads from a report: its profile name and its metrics."""

    @property
    def profile(self) -> str: ...

    @property
    def metrics(self) -> Mapping[str, float]: ...


class ThresholdError(ValueError):
    """The thresholds file is missing or malformed."""


class Profile(BaseModel):
    """Bounds for one ``<mode>-<backend>`` profile."""

    # A misspelt key ("minimum") must fail loudly, not drop the bounds under it.
    model_config = ConfigDict(extra="forbid")

    min: dict[str, float] = Field(default_factory=dict)
    max: dict[str, float] = Field(default_factory=dict)


class ThresholdResult(BaseModel):
    """Outcome of :func:`check_thresholds`."""

    profile: str
    passed: bool
    checks: list[str]
    failures: list[str]

    @property
    def exit_code(self) -> int:
        """0 when every bound holds, 1 on regression."""
        return 0 if self.passed else 1


def load_profiles(path: Path) -> dict[str, Profile]:
    """Parse the thresholds YAML into profiles."""
    if not path.exists():
        raise ThresholdError(f"thresholds file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ThresholdError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ThresholdError(f"{path}: top level must be a mapping of profiles")
    try:
        return {str(name): Profile.model_validate(body or {}) for name, body in raw.items()}
    except ValidationError as exc:
        raise ThresholdError(f"{path}: invalid profile: {exc}") from exc


def check_thresholds(
    report: GateReport, thresholds: Path, profile: str | None = None
) -> ThresholdResult:
    """Check every bound of the report's profile; a missing metric or an empty profile fails."""
    name = profile or report.profile
    profiles = load_profiles(thresholds)
    if name not in profiles:
        return ThresholdResult(
            profile=name, passed=False, checks=[], failures=[f"no profile {name!r} in {thresholds}"]
        )
    bounds = profiles[name]
    if not bounds.min and not bounds.max:
        return ThresholdResult(
            profile=name, passed=False, checks=[], failures=[f"profile {name!r} has no bounds"]
        )
    checks: list[str] = []
    failures: list[str] = []
    for metric, minimum in bounds.min.items():
        value = report.metrics.get(metric)
        line = f"{metric} = {value} (min {minimum})"
        checks.append(line)
        if value is None or value < minimum:
            failures.append(line)
    for metric, maximum in bounds.max.items():
        value = report.metrics.get(metric)
        line = f"{metric} = {value} (max {maximum})"
        checks.append(line)
        if value is None or value > maximum:
            failures.append(line)
    return ThresholdResult(profile=name, passed=not failures, checks=checks, failures=failures)
