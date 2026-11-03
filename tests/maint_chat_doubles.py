"""BROKEN chat responders of the 3b full-mode negative controls (I9 spec decision 13).

Each double starts from :func:`~alverno_lab.agents.ops.fakes.ops_diagnose_echo` and adds
one fault that a barrier of the 3b graph must catch; the doubles live in the tests, never
in ``src/``. They read the prompt, and the GMAO fixture with their own loader (never
through the rig under test):

- :class:`TechnicianNamer`: adds a next check that names a technician who worked on the
  machine, by one identity field (full name, initials, login or matricule); the strict
  guard must withhold the diagnosis;
- :class:`UnfetchedCiter`: cites a work order of the machine that the prompt does not
  hold, i.e. one the run never read (``verify`` must drop it);
- :func:`remark_follower`: copies every remark of the prompt's history lines into the
  next checks, as if the remarks were orders (the quarantine and the guard's quarantine
  rules must stop them).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel

from alverno_lab.agents.maintenance.diagnosis import MAX_CHECK_CHARS, MAX_CHECKS, MAX_CITATIONS
from alverno_lab.agents.maintenance.prompts import HISTORY_HEADER
from alverno_lab.agents.ops.fakes import ops_diagnose_echo

__all__ = ["TechnicianField", "TechnicianNamer", "UnfetchedCiter", "remark_follower"]

TechnicianField = Literal["technician_name", "initials", "login", "matricule"]

_ASSET_ID = re.compile(r"EQ-[A-Z]{3}-[0-9]{2}")
_WO_ID = re.compile(r"OT-[0-9]{4}-[0-9]{4}")
_REMARK_FIELD = 6


def _work_orders(gmao_path: Path) -> list[dict[str, object]]:
    raw: object = yaml.safe_load(gmao_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return cast(list[dict[str, object]], raw["work_orders"])


def _asset_of(user: str) -> str | None:
    found = _ASSET_ID.search(user)
    return found.group(0) if found is not None else None


class TechnicianNamer:
    """BROKEN responder: the echo, plus a next check naming a technician of the machine
    by ``field`` (the first technician of the machine in the fixture)."""

    def __init__(self, gmao_path: Path, field: TechnicianField = "technician_name") -> None:
        self.values: dict[str, str] = {}
        for wo in _work_orders(gmao_path):
            self.values.setdefault(str(wo["asset_id"]), str(wo[field]))

    def __call__(self, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        answer = ops_diagnose_echo(system, user, schema)
        value = self.values.get(_asset_of(user) or "")
        if value is None:
            return answer
        check = f"Revoir le réglage avec {value}, qui connaît cette machine."
        checks = [*answer["next_checks"], check][-MAX_CHECKS:]
        return {**answer, "next_checks": checks}


class UnfetchedCiter:
    """BROKEN responder: the echo, plus a cited work order of the machine not in the prompt."""

    def __init__(self, gmao_path: Path) -> None:
        self.by_asset: dict[str, list[str]] = {}
        for wo in sorted(_work_orders(gmao_path), key=lambda w: str(w["wo_id"])):
            self.by_asset.setdefault(str(wo["asset_id"]), []).append(str(wo["wo_id"]))

    def __call__(self, system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
        answer = ops_diagnose_echo(system, user, schema)
        shown = set(_WO_ID.findall(user))
        unread = [w for w in self.by_asset.get(_asset_of(user) or "", []) if w not in shown]
        if not unread:
            return answer
        cited = [*answer["cited_wo_ids"][: MAX_CITATIONS - 1], unread[0]]
        return {**answer, "cited_wo_ids": cited}


def _remarks(user: str) -> list[str]:
    lines = user.splitlines()
    try:
        start = lines.index(f"{HISTORY_HEADER} :") + 1
    except ValueError:
        return []
    found: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            break
        fields = line.split(" | ", _REMARK_FIELD)
        if len(fields) > _REMARK_FIELD and fields[_REMARK_FIELD].strip():
            found.append(fields[_REMARK_FIELD].strip())
    return found


def remark_follower(system: str, user: str, schema: type[BaseModel]) -> dict[str, Any]:
    """BROKEN responder: the echo, with every remark of the prompt as a next check."""
    answer = ops_diagnose_echo(system, user, schema)
    checks = [remark[:MAX_CHECK_CHARS] for remark in _remarks(user)][:MAX_CHECKS]
    return {**answer, "next_checks": checks or answer["next_checks"]}
