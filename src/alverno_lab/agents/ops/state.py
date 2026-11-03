"""State of the module 3 graphs: JSON primitives only (ADR 0013, « État en primitives JSON »).

:class:`OpsState` is what the in-memory checkpointer stores between an interrupt and its
resume. Every field holds JSON primitives (``str``, ``int``, ``float``, ``bool``,
``None``, lists and string-keyed dicts of them), so the strict serializer never needs
``pickle`` and no pydantic model, tuple or bytes value is ever checkpointed:

- ``records`` holds ``model_dump(mode="json")`` of the order, asset and work-order views
  read; a ``ContactView`` is never stored (the ``compose`` node reads it and keeps only
  the composed text, ADR 0012);
- ``tool_events`` holds the tool name, ``ok`` and the error code of each call, never its
  arguments;
- ``proposed_action`` holds ``ProposedAction.model_dump(mode="json")``: one write, its
  arguments (identifiers, closed codes and a digest) and their SHA-256;
- ``decision`` holds the validated decision of the last resume (its token included,
  until ``apply`` redeems it), or ``{"decision": "reject", "reason": "invalid_decision"}``.

:class:`ProposedAction` describes the single write of one interrupt (« une action par
interruption »). Its ``args_sha256`` uses :func:`alverno_lab.tools.registry.args_sha256`,
the digest that approval tokens, executor records and side-effect records share.
"""

from __future__ import annotations

import hashlib
import math
import operator
import uuid
from typing import Annotated, Literal, Self, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from alverno_lab.connectors.types import ActionId, Sha256Hex, WriteToolName
from alverno_lab.tools.registry import TOOLS, args_sha256

__all__ = [
    "OUTCOMES",
    "OpsState",
    "Outcome",
    "ProposedAction",
    "is_json_value",
    "text_sha256",
]

Outcome = Literal["answered", "refused", "withheld", "executed", "rejected", "error"]
OUTCOMES: frozenset[str] = frozenset(get_args(Outcome))

MAX_SUMMARY_CHARS = 300


class OpsState(TypedDict, total=False):
    """State of an ops graph (3a or 3b); every value is a JSON primitive."""

    request_redacted: str
    flow: str | None
    ids: list[str]
    tool_events: list[dict[str, str | bool | None]]
    records: list[dict[str, JsonValue]]
    quarantine_flags: list[str]
    draft_body: str | None
    draft_citations: list[str]
    # 3b only: the symptom code the router read, and the verified diagnosis (codes,
    # cited work-order ids, next checks, confidence, dropped citations); 3a never sets them.
    symptom: str | None
    diagnosis: dict[str, JsonValue] | None
    # 3b only: the work-order ids the run read (every cited id is one of them); the
    # envelope logs them in ``entity_refs`` after ``ids``. 3a never sets it.
    read_refs: list[str]
    draft_id: str | None
    draft_sha256: str | None
    proposed_action: dict[str, JsonValue] | None
    decision: dict[str, JsonValue] | None
    edit_round: int
    refusal: str | None
    # One of OUTCOMES once the graph has ended; None while it runs or waits.
    outcome: str | None
    handed_off: bool
    trace: Annotated[list[dict[str, JsonValue]], operator.add]


def text_sha256(text: str) -> str:
    """SHA-256 (hex) of a text in UTF-8: the ``draft_sha256`` of a draft body."""
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def is_json_value(value: object) -> bool:
    """Whether ``value`` is made of JSON primitives only, by exact type.

    ``str``, ``int``, finite ``float``, ``bool``, ``None``, and lists and ``str``-keyed
    dicts of them. A tuple, bytes, a pydantic model, a subclass of a primitive (an enum
    member, a ``str`` subclass) or a non-finite float is not.
    """
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        kind = type(item)
        if item is None or kind in (str, int, bool):
            continue
        if isinstance(item, float) and kind is float:
            if not math.isfinite(item):
                return False
            continue
        if isinstance(item, list) and kind is list:
            stack.extend(item)
            continue
        if isinstance(item, dict) and kind is dict:
            if not all(type(key) is str for key in item):
                return False
            stack.extend(item.values())
            continue
        return False
    return True


class ProposedAction(BaseModel):
    """One write presented for approval: the tool, its arguments and their digest.

    The validator re-checks the arguments against the tool's args model and recomputes
    their digest, so an action rebuilt from state whose arguments were changed after it
    was built does not validate. Its errors name a rule, never a value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    action_id: ActionId
    tool: WriteToolName
    # Identifiers, closed codes and a digest; kept out of repr all the same.
    args: dict[str, JsonValue] = Field(repr=False)
    args_sha256: Sha256Hex
    summary_fr: str = Field(default="", max_length=MAX_SUMMARY_CHARS, repr=False)

    @model_validator(mode="after")
    def _consistent(self) -> ProposedAction:
        digest: str | None = None
        dumped: object = None
        try:
            model = TOOLS[self.tool].args_model.model_validate(self.args)
            digest = args_sha256(model)
            dumped = model.model_dump(mode="json")
        # Never chain: an args-model error would show the rejected values.
        except Exception:
            digest = None
        if digest is None or dumped != self.args:
            raise ValueError("proposed_action:invalid_args")
        if digest != self.args_sha256:
            raise ValueError("proposed_action:args_sha256")
        return self

    @classmethod
    def build(cls, tool: WriteToolName, args_model: BaseModel, *, summary_fr: str = "") -> Self:
        """A new action for ``args_model`` with a fresh ``action_id`` (uuid4 hex).

        ``args_model`` must be an instance of exactly the tool's args model; the digest is
        :func:`alverno_lab.tools.registry.args_sha256` of it.
        """
        spec = TOOLS.get(tool)
        if spec is None or spec.kind != "write_internal" or type(args_model) is not spec.args_model:
            raise ValueError("proposed_action:args_model")
        return cls(
            action_id=uuid.uuid4().hex,
            tool=tool,
            args=args_model.model_dump(mode="json"),
            args_sha256=args_sha256(args_model),
            summary_fr=summary_fr,
        )
