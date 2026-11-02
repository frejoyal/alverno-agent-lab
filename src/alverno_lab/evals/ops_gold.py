"""Shared helpers of the module 3 gold-set loaders (ADR 0015 « Attentes écrites item par item »).

The service (:mod:`alverno_lab.evals.service`) and maintenance
(:mod:`alverno_lab.evals.maint`) loaders check the schema and the existence of what an
item refers to, nothing more:

- each line is one JSON object validated by a frozen model with ``extra="forbid"``;
- ids are unique and well-formed, and each pair joins exactly one trap and one
  answerable twin;
- expected tool calls validate against the registry args models, with two placeholders
  accepted only in the fields the runner fills (:data:`APPROVED_TEXT`,
  :data:`FROM_DIAGNOSIS`);
- the identifiers they name exist in the simulated connector, except the id of a
  ``record_not_found`` item, which must not exist;
- every canary and every forbidden value is a verbatim fixture value, and every
  injection marker is part of a fixture text.

The expected calls are written item by item and compared with what the agent does: the
loaders never consult the planner's table of reads, or the expected-calls metric would
be a tautology (a structural test checks this).

Errors are :class:`~alverno_lab.evals.dataset.DatasetError` with the message
``<file>:<line>:<rule>`` (line 0 for a rule on the whole file). A message never holds a
value: the same loaders read the holdouts, whose requests stay unseen. Errors are
raised ``from None`` and outside any ``except`` block, so that no exception chain keeps
a JSON line or a pydantic error that would echo one.

The module also re-exports the ADR 0015 rendering of statuses and dates
(:mod:`alverno_lab.agents.ops.rendering`), which the service loader needs to recognise
a forbidden status or date.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints, ValidationError

from alverno_lab.agents.ops.rendering import (
    FORM_STATUS,
    MONTHS_FR,
    STATUS_FORMS,
    STATUS_RENDERINGS,
    find_status_forms,
    render_date,
)
from alverno_lab.connectors.types import (
    CREATE_WORK_REQUEST_TOOL,
    INDETERMINATE_CODE,
    RECORD_REPLY_TOOL,
)
from alverno_lab.evals.dataset import DatasetError
from alverno_lab.tools.registry import TOOLS, WRITE_TOOLS, ToolName

__all__ = [
    "APPROVED_TEXT",
    "FORM_STATUS",
    "FROM_DIAGNOSIS",
    "MONTHS_FR",
    "SETTLED_OUTCOMES",
    "STATUS_FORMS",
    "STATUS_RENDERINGS",
    "CheckedCall",
    "GoldOutcome",
    "GoldText",
    "ItemId",
    "ToolCall",
    "call_rules",
    "check_call",
    "find_status_forms",
    "id_rules",
    "load_items",
    "marker_rules",
    "outcome_rules",
    "proposal_rules",
    "render_date",
    "value_rules",
]

# Placeholders for what only the run knows: the digest of the approved draft, and the
# failure code and cited work orders that the diagnosis chooses.
APPROVED_TEXT = "<approved_text>"
FROM_DIAGNOSIS = "<from_diagnosis>"

# Item and pair ids: a letter or digit, then letters, digits, « _ » or « - ». The gold
# sets use s001 / sp01 and m001 / mp01; the holdouts, unseen, may use another prefix.
ItemId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")]
# Canaries, facts, forbidden values and markers: an empty string would match any text.
GoldText = Annotated[str, StringConstraints(min_length=1)]

GoldOutcome = Literal["answered", "executed", "rejected", "withheld", "refused"]
# Outcomes without a refusal reason.
SETTLED_OUTCOMES: frozenset[str] = frozenset({"answered", "executed"})


class ToolCall(BaseModel):
    """One expected tool call: a registry tool name and its raw JSON arguments.

    Arguments stay raw because two of them may hold a placeholder; :func:`check_call`
    validates them against the tool's args model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ToolName
    args: dict[str, JsonValue]


# --------------------------------------------------------------------------- tool calls
# (tool, field) -> (placeholder, stand-in that passes the args model). The stand-ins
# exist only for validation; existence checks skip a field that held a placeholder.
_PLACEHOLDER_FIELDS: Mapping[tuple[str, str], tuple[str, JsonValue]] = MappingProxyType(
    {
        (RECORD_REPLY_TOOL, "draft_sha256"): (APPROVED_TEXT, "0" * 64),
        (CREATE_WORK_REQUEST_TOOL, "failure_code"): (FROM_DIAGNOSIS, INDETERMINATE_CODE),
        (CREATE_WORK_REQUEST_TOOL, "cited_wo_ids"): (FROM_DIAGNOSIS, ["OT-0000-0000"]),
    }
)
_PLACEHOLDERS = frozenset({APPROVED_TEXT, FROM_DIAGNOSIS})


@dataclasses.dataclass(frozen=True)
class CheckedCall:
    """A call whose arguments passed the args model.

    ``args`` holds the stand-ins in place of the placeholders; ``placeholders`` names
    the fields that held one.
    """

    args: BaseModel
    placeholders: frozenset[str]


def check_call(call: ToolCall) -> CheckedCall | str:
    """Validate ``call`` against the registry; a rule name (``placeholder_misplaced`` or
    ``args_invalid``) when it fails."""
    args: dict[str, JsonValue] = {}
    placeholders: set[str] = set()
    for field, value in call.args.items():
        if isinstance(value, str) and value in _PLACEHOLDERS:
            slot = _PLACEHOLDER_FIELDS.get((call.tool, field))
            if slot is None or slot[0] != value:
                return "placeholder_misplaced"
            args[field] = slot[1]
            placeholders.add(field)
        else:
            args[field] = value
    rule: str | None = None
    try:
        model = TOOLS[call.tool].args_model.model_validate(args)
    except ValidationError:
        rule = "args_invalid"
    if rule is not None:
        return rule
    return CheckedCall(model, frozenset(placeholders))


def id_rules(
    field: str, value: str | None, known: frozenset[str], *, absent: bool
) -> Iterator[str]:
    """``<field>.unknown`` for an id missing from the fixture, ``<field>.must_not_exist``
    for an id that exists although the item expects ``record_not_found``."""
    if value is None:
        return
    if absent and value in known:
        yield f"{field}.must_not_exist"
    elif not absent and value not in known:
        yield f"{field}.unknown"


def outcome_rules(outcome: str, reason: str | None) -> Iterator[str]:
    """``answered`` and ``executed`` have no reason; every other outcome has one."""
    if (outcome in SETTLED_OUTCOMES) != (reason is None):
        yield "expected_reason.outcome"


def call_rules(
    where: str,
    call: ToolCall,
    system: str,
    id_check: Callable[[CheckedCall], Iterator[str]],
) -> Iterator[str]:
    """Rules of one expected call: tool of this set's system, args, then existence."""
    checked = check_call(call) if call.tool.startswith(f"{system}.") else "tool_of_other_system"
    if isinstance(checked, str):
        yield f"{where}.{checked}"
    else:
        for rule in id_check(checked):
            yield f"{where}.{rule}"


def proposal_rules(proposal: ToolCall | None, n_steps: int) -> Iterator[str]:
    """A proposal is a write, and it comes with an approval script (and conversely)."""
    if proposal is not None and proposal.tool not in WRITE_TOOLS:
        yield "expected_proposal.not_a_write"
    if proposal is not None and n_steps == 0:
        yield "expected_proposal.without_script"
    if proposal is None and n_steps > 0:
        yield "approval_script.without_proposal"


def value_rules(field: str, values: Iterable[str], admissible: frozenset[str]) -> Iterator[str]:
    """Every value is one of ``admissible`` (verbatim fixture values)."""
    if any(value not in admissible for value in values):
        yield f"{field}.not_fixture_value"


def marker_rules(markers: Iterable[str], texts: frozenset[str]) -> Iterator[str]:
    """Every injection marker is part of a fixture text."""
    if any(not any(marker in text for text in texts) for marker in markers):
        yield "injection_markers.not_fixture_text"


# --------------------------------------------------------------------------- loading
class _GoldLike(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def pair_id(self) -> str | None: ...

    @property
    def is_answerable(self) -> bool: ...


def _validation_summary(exc: ValidationError) -> str:
    """Field locations and error types only: pydantic messages would echo input values."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['type']}" for e in exc.errors()
    )


def _parse[I: _GoldLike](line: str, validate: Callable[[object], I]) -> I | str:
    """The item of one line, or the broken rule. The exception is dropped here."""
    try:
        return validate(json.loads(line))
    except json.JSONDecodeError:
        return "json.invalid"
    except ValidationError as exc:
        return f"schema[{_validation_summary(exc)}]"
    # json.loads raises a bare ValueError for an integer over the int-to-str digit
    # limit, and RecursionError for arrays nested too deep.
    except (ValueError, RecursionError):
        return "json.invalid"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def load_items[I: _GoldLike](
    path: Path,
    validate: Callable[[object], I],
    broken_rules: Callable[[I], Iterator[str]],
) -> list[I]:
    """Read a module 3 JSONL set; raise :class:`DatasetError` at the first broken rule.

    ``validate`` builds an item from a decoded line (a model's ``model_validate``);
    ``broken_rules`` yields the item's broken rules, the first of which is reported.
    Blank lines are skipped.
    """
    if not path.is_file():
        raise DatasetError(f"{path}:0:file.missing") from None
    text = _read_text(path)
    if text is None:
        raise DatasetError(f"{path}:0:file.unreadable") from None
    items: list[I] = []
    pairs: dict[str, list[tuple[int, I]]] = {}
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        item = _parse(line, validate)
        if isinstance(item, str):
            raise DatasetError(f"{path}:{lineno}:{item}") from None
        if item.id in seen:
            raise DatasetError(f"{path}:{lineno}:id.duplicate") from None
        rule = next(broken_rules(item), None)
        if rule is not None:
            raise DatasetError(f"{path}:{lineno}:{rule}") from None
        seen.add(item.id)
        items.append(item)
        if item.pair_id is not None:
            pairs.setdefault(item.pair_id, []).append((lineno, item))
    if not items:
        raise DatasetError(f"{path}:0:set.empty") from None
    for members in pairs.values():
        twins = sum(item.is_answerable for _, item in members)
        if len(members) != 2 or twins != 1:
            raise DatasetError(f"{path}:{members[-1][0]}:pair_id.not_one_trap_one_twin") from None
    return items
