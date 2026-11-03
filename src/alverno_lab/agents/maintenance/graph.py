"""The 3b graph: diagnosis and work request (ADR 0012 « Graphes », ADR 0013).

::

    route → fetch → gate → quarantine → diagnose → verify → guard →
        [diagnosis]    END
        [work_request] propose → approve ⇄ reguard → apply → handoff → END
    any refusal → END (``outcome`` and ``refusal`` set)

:func:`build_maint_graph` returns the builder an
:class:`~alverno_lab.agents.ops.runtime.OpsEnvelope` takes. The code routes, plans the
reads, fills every argument and decides what may execute; the diagnoser (an injected
:class:`~alverno_lab.agents.maintenance.diagnosis.Diagnoser`) only ranks failure codes
and cites work orders.

- ``route``: :func:`~alverno_lab.agents.maintenance.route.route_maintenance` on the
  redacted request and the GMAO's closed symptom labels (``asset_not_identified``).
- ``fetch``: through the executor, in this order, ``gmao.get_asset``, then (only when the
  asset exists) ``gmao.list_work_orders(asset, symptom, since_days, limit)`` and
  ``gmao.failure_catalog``. A connector failure ends in ``error`` with
  ``connector_unavailable``. The views go to ``records`` as JSON, and the ids of the
  work orders read to ``read_refs`` (the envelope logs them in ``entity_refs`` after the
  asset, ADR 0014; every cited id is one of them).
- ``gate``: an unknown asset is refused ``record_not_found``; fewer than 2 work orders,
  ``insufficient_history``.
- ``quarantine``: the remarks' quarantine flags, for the access log.
- ``diagnose``: one diagnoser call on the asset, the symptom, the history and the
  catalogue (never the request). A diagnoser failure is an ``error`` without reason
  (``diagnosis_failed``), except a model that cannot be reached
  (:class:`~alverno_lab.agents.ops.runtime.ModelUnavailableError`), which escapes to the
  envelope like a connector failure.
- ``verify``: :func:`~alverno_lab.agents.maintenance.diagnosis.verify_diagnosis`; the
  trace keeps the verified codes, the valid citations and the dropped count.
- ``guard`` (strict): writes the answer
  (:func:`~alverno_lab.agents.maintenance.diagnosis.diagnosis_text`), then withholds it
  ``output_pi`` when the quarantine rules flag it (rule ``quarantine``), when the
  detector finds a name or a matricule anywhere (``person``), when the quarantine's
  identifier rules find initials or a login (``technician``), when a sentence ties a
  small group of roles to a duration, a fault or a result (``small_role``), or when
  ``check_output`` fails with the history lines (closed fields only, never the remark),
  the catalogue and the asset as cited extracts (its rule's name). A diagnosis then ends
  ``answered``.
- ``propose`` (``work_request``): ``gmao.create_work_request(asset_id, symptom_code,
  failure_code = first verified code, cited_wo_ids = verified citations)`` and a new
  ``draft_id``. No symptom read gives ``invalid_args``; no valid citation, ``ungrounded``
  (both withheld).
- The approval tail (:func:`~alverno_lab.agents.ops.approval.add_approval_nodes`) with the
  3b edit check (:func:`make_check_edit`, ADR 0013): the edited arguments are merged over
  the presented ones and validated by ``CreateWorkRequestArgs`` (closed codes), the asset
  must stay the same and every cited id must be a fetched work order of this asset; else
  ``invalid_args``.

Every node appends one scrubbed trace entry; no node writes the access log.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable, Mapping

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import JsonValue

from alverno_lab.agents.maintenance.diagnosis import (
    Diagnoser,
    Diagnosis,
    DiagnosisCase,
    VerifiedDiagnosis,
    catalog_line,
    diagnosis_text,
    history_line,
    verify_diagnosis,
)
from alverno_lab.agents.maintenance.route import find_asset_ids, route_maintenance
from alverno_lab.agents.ops.approval import (
    APPROVE_NODE,
    MAX_EDIT_ROUNDS,
    ApprovalDecision,
    EditCheck,
    EditChecker,
    OpsNode,
    ToolCaller,
    add_approval_nodes,
)
from alverno_lab.agents.ops.guards import (
    person_spans,
    quarantine_stop,
    small_role_sentence,
    technician_ref,
)
from alverno_lab.agents.ops.runtime import ModelUnavailableError, OpsGraph
from alverno_lab.agents.ops.scrub import scrub_entry
from alverno_lab.agents.ops.state import OpsState, ProposedAction
from alverno_lab.connectors.gmao import GmaoConnector
from alverno_lab.connectors.types import (
    CREATE_WORK_REQUEST_TOOL,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WORK_ORDER_LIMIT,
    INDETERMINATE_CODE,
    MAX_CITED_WORK_ORDERS,
    SYMPTOM_CODES,
    AssetView,
    FailureCodeView,
    SymptomCode,
    WorkOrderView,
)
from alverno_lab.privacy.guard import check_output
from alverno_lab.privacy.types import PrivacyContext
from alverno_lab.tools.registry import CreateWorkRequestArgs

__all__ = [
    "MAINT_NODES",
    "MIN_WORK_ORDERS",
    "build_maint_graph",
    "guard_rule",
    "make_check_edit",
    "work_request_args",
    "work_request_summary",
]

GET_ASSET = "gmao.get_asset"
LIST_WORK_ORDERS = "gmao.list_work_orders"
FAILURE_CATALOG = "gmao.failure_catalog"
MIN_WORK_ORDERS = 2
MAINT_NODES: tuple[str, ...] = (
    "route",
    "fetch",
    "gate",
    "quarantine",
    "diagnose",
    "verify",
    "guard",
    "propose",
)
# Both connector codes of the executor become the « connector_unavailable » refusal.
_CONNECTOR_ERRORS: frozenset[str] = frozenset({"connector_error", "connector_unavailable"})


# --------------------------------------------------------------------------- helpers
def _trace(node: str, detail: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    return [scrub_entry(node, detail)]


def _stop(node: str, outcome: str, refusal: str | None, **detail: object) -> OpsState:
    """End the request at ``node``: the outcome, its refusal code and the trace entry."""
    update: OpsState = {
        "outcome": outcome,
        "refusal": refusal,
        "trace": _trace(node, {**detail, "refusal": refusal}),
    }
    if outcome == "withheld":
        # A withheld text is never kept: the checkpoint holds no copy of it.
        update["draft_body"] = None
    return update


def _records(state: OpsState) -> list[dict[str, JsonValue]]:
    return [record for record in state.get("records") or [] if isinstance(record, dict)]


def _asset(state: OpsState) -> AssetView | None:
    """The in-scope asset view, rebuilt from ``records``; None when it cannot be."""
    ids = state.get("ids") or []
    if len(ids) != 1:
        return None
    for record in _records(state):
        if record.get("asset_id") == ids[0] and "name" in record:
            try:
                return AssetView.model_validate(record)
            # Never raise or chain: a broken record fails the request closed.
            except Exception:
                return None
    return None


def _work_orders(state: OpsState) -> tuple[WorkOrderView, ...] | None:
    """The fetched work orders of the in-scope asset; None when one is broken."""
    ids = state.get("ids") or []
    views: list[WorkOrderView] = []
    for record in _records(state):
        if "wo_id" not in record:
            continue
        try:
            view = WorkOrderView.model_validate(record)
        except Exception:
            return None
        if view.asset_id not in ids:
            return None
        views.append(view)
    return tuple(views)


def _catalog(state: OpsState) -> tuple[FailureCodeView, ...] | None:
    entries: list[FailureCodeView] = []
    for record in _records(state):
        if "category" not in record:
            continue
        try:
            entries.append(FailureCodeView.model_validate(record))
        except Exception:
            return None
    return tuple(entries)


def _symptom(state: OpsState) -> SymptomCode | None:
    value = state.get("symptom")
    if type(value) is str and value in SYMPTOM_CODES:
        return value  # type: ignore[return-value]  # checked against the Literal's values
    return None


def _case(state: OpsState, labels: Mapping[SymptomCode, str]) -> DiagnosisCase | None:
    asset = _asset(state)
    work_orders = _work_orders(state)
    catalog = _catalog(state)
    if asset is None or work_orders is None or catalog is None:
        return None
    symptom = _symptom(state)
    label = labels.get(symptom) if symptom is not None else None
    return DiagnosisCase(
        asset=asset,
        symptom=symptom,
        symptom_label=label,
        work_orders=work_orders,
        catalog=catalog,
    )


def _extracts(case: DiagnosisCase) -> list[str]:
    """The cited extracts of the guard: the asset, the symptom, the history, the catalogue."""
    asset = case.asset
    head = " | ".join([asset.asset_id, asset.name, asset.kind, asset.line])
    symptom = " | ".join(part for part in (case.symptom, case.symptom_label) if part)
    return [
        head,
        symptom,
        # Closed fields only: a remark is no source for a served word.
        *(history_line(wo, with_remark=False) for wo in case.work_orders),
        *(catalog_line(entry) for entry in case.catalog),
    ]


def guard_rule(text: str, case: DiagnosisCase, request: str, ctx: PrivacyContext) -> str | None:
    """The strict 3b guard on a served text: the first rule that fires, or None."""
    if quarantine_stop(text, ctx):
        return "quarantine"
    if person_spans(text, ctx):
        return "person"
    if technician_ref(text, ctx):
        return "technician"
    if small_role_sentence(text, ctx):
        return "small_role"
    verdict = check_output(
        text,
        redacted_question=request,
        raw_values=(),
        cited_extracts=_extracts(case),
        ctx=ctx,
    )
    return None if verdict.ok else verdict.rule or "detected"


def work_request_summary(args: CreateWorkRequestArgs) -> str:
    """The operator's one-line description of a proposed work request (codes and ids)."""
    return (
        f"Demande de travail corrective pour {args.asset_id} : symptôme {args.symptom_code}, "
        f"code {args.failure_code}, bons cités {', '.join(args.cited_wo_ids)}"
    )


def work_request_args(
    asset_id: str, symptom: SymptomCode, verified: VerifiedDiagnosis
) -> CreateWorkRequestArgs:
    """The ADR 0012 arguments drawn from a verified diagnosis (first code, citations)."""
    failure = verified.codes[0] if verified.codes else INDETERMINATE_CODE
    return CreateWorkRequestArgs.model_validate(
        {
            "asset_id": asset_id,
            "symptom_code": symptom,
            "failure_code": failure,
            "cited_wo_ids": list(verified.cited_wo_ids[:MAX_CITED_WORK_ORDERS]),
        }
    )


def _action(args: CreateWorkRequestArgs) -> ProposedAction:
    return ProposedAction.build(
        CREATE_WORK_REQUEST_TOOL, args, summary_fr=work_request_summary(args)
    )


def make_check_edit() -> EditChecker:
    """The 3b edit check of ``reguard`` (ADR 0013: args model, closed codes, ids read)."""

    def check_edit(state: OpsState, decision: ApprovalDecision) -> EditCheck:
        edited = decision.edited_args
        presented = state.get("proposed_action") or {}
        previous = presented.get("args") if isinstance(presented, dict) else None
        work_orders = _work_orders(state)
        if edited is None or not isinstance(previous, dict) or work_orders is None:
            return EditCheck(ok=False, reason="invalid_args")
        merged = {**previous, **edited}
        try:
            args = CreateWorkRequestArgs.model_validate(merged)
        # Never raise or chain: the error would describe the edited values.
        except Exception:
            return EditCheck(ok=False, reason="invalid_args")
        fetched = {wo.wo_id for wo in work_orders if wo.asset_id == args.asset_id}
        if args.asset_id != previous.get("asset_id") or not set(args.cited_wo_ids) <= fetched:
            return EditCheck(ok=False, reason="invalid_args")
        return EditCheck(ok=True, proposed_action=_action(args).model_dump(mode="json"))

    return check_edit


def _after(target: str) -> Callable[[OpsState], str]:
    def route(state: OpsState) -> str:
        return END if state.get("outcome") is not None else target

    return route


def _after_guard(state: OpsState) -> str:
    if state.get("outcome") is None and state.get("flow") == "work_request":
        return "propose"
    return END


# --------------------------------------------------------------------------- graph
def build_maint_graph(
    *,
    executor: ToolCaller,
    gmao: GmaoConnector,
    ctx: PrivacyContext,
    diagnoser: Diagnoser,
    history_days: int = DEFAULT_HISTORY_DAYS,
    max_work_orders: int = DEFAULT_WORK_ORDER_LIMIT,
    max_edit_rounds: int = MAX_EDIT_ROUNDS,
) -> Callable[[BaseCheckpointSaver[str]], OpsGraph]:
    """The builder of the 3b graph, for :class:`~alverno_lab.agents.ops.runtime.OpsEnvelope`.

    ``executor`` runs the reads and the write (with the token); ``gmao`` is read by code
    for the closed symptom labels only; ``diagnoser`` ranks the codes (BM25 in tools
    mode); ``history_days`` and ``max_work_orders`` are ``ops_history_days`` and
    ``ops_max_work_orders``; ``max_edit_rounds`` must equal the envelope's.
    """
    labels_cache: dict[SymptomCode, str] = {}

    def labels() -> Mapping[SymptomCode, str]:
        if not labels_cache:
            labels_cache.update(gmao.symptom_labels())
        return labels_cache

    def route(state: OpsState) -> OpsState:
        request = state.get("request_redacted") or ""
        result = route_maintenance(request, labels())
        if result.refusal is not None:
            count = len(find_asset_ids(request))
            return _stop("route", "refused", result.refusal, id_count=count)
        ids = [str(result.asset_id)]
        return {
            "flow": result.flow,
            "ids": ids,
            "symptom": result.symptom,
            "trace": _trace(
                "route",
                {"flow": result.flow, "ids": ids, "id_count": 1, "symptom": result.symptom},
            ),
        }

    def fetch(state: OpsState) -> OpsState:
        ids = list(state.get("ids") or [])
        asset_id = ids[0] if ids else ""
        events = list(state.get("tool_events") or [])
        records: list[dict[str, JsonValue]] = []
        plan: list[tuple[str, dict[str, object]]] = [
            (GET_ASSET, {"asset_id": asset_id}),
            (
                LIST_WORK_ORDERS,
                {
                    "asset_id": asset_id,
                    "symptom_code": _symptom(state),
                    "since_days": history_days,
                    "limit": max_work_orders,
                },
            ),
            (FAILURE_CATALOG, {}),
        ]
        for tool, args in plan:
            result = executor.call(tool, args)
            events.append({"tool": result.tool, "ok": result.ok, "error": result.error})
            detail: dict[str, object] = {"tool": tool, "ok": result.ok, "error": result.error}
            if not result.ok:
                refusal = "connector_unavailable" if result.error in _CONNECTOR_ERRORS else None
                return {"tool_events": events, **_stop("fetch", "error", refusal, **detail)}
            data = result.data
            if tool == GET_ASSET:
                if data is None:
                    # Unknown asset: no other read; the gate refuses it.
                    break
                if not isinstance(data, AssetView):
                    return {"tool_events": events, **_stop("fetch", "error", None, **detail)}
                records.append(data.model_dump(mode="json"))
                continue
            rows = data if isinstance(data, tuple) else ()
            records.extend(row.model_dump(mode="json") for row in rows)
        read = [str(r["wo_id"]) for r in records if isinstance(r.get("wo_id"), str)]
        return {
            "tool_events": events,
            "records": records,
            "read_refs": read,
            "trace": _trace("fetch", {"ok": True, "ids": ids, "record_count": len(records)}),
        }

    def gate(state: OpsState) -> OpsState:
        if _asset(state) is None:
            return _stop("gate", "refused", "record_not_found", ok=False)
        work_orders = _work_orders(state)
        if work_orders is None:
            return _stop("gate", "error", None, ok=False)
        if len(work_orders) < MIN_WORK_ORDERS:
            return _stop(
                "gate", "refused", "insufficient_history", ok=False, record_count=len(work_orders)
            )
        return {"trace": _trace("gate", {"ok": True, "record_count": len(work_orders)})}

    def quarantine(state: OpsState) -> OpsState:
        flags: list[str] = []
        for record in _records(state):
            remark = record.get("remark")
            raw = remark.get("flags") if isinstance(remark, dict) else None
            for flag in raw if isinstance(raw, list) else []:
                if isinstance(flag, str) and flag not in flags:
                    flags.append(flag)
        return {
            "quarantine_flags": flags,
            "trace": _trace("quarantine", {"flags": flags, "flag_count": len(flags)}),
        }

    def diagnose(state: OpsState) -> OpsState:
        case = _case(state, labels())
        if case is None:
            return _stop("diagnose", "error", None, ok=False, error="no_case")
        produced: object = None
        unavailable = False
        try:
            produced = diagnoser.diagnose(case)
        except ModelUnavailableError:
            unavailable = True
        # Never raise or chain: a diagnoser's error may describe the history.
        except Exception:
            produced = None
        if unavailable:
            # Like a connector failure, it escapes to the envelope (logged
            # error/connector_unavailable), raised here without a chain.
            raise ModelUnavailableError("diagnose:model_unavailable")
        if not isinstance(produced, Diagnosis):
            return _stop("diagnose", "error", None, ok=False, error="diagnosis_failed")
        return {
            "diagnosis": produced.model_dump(mode="json"),
            "trace": _trace(
                "diagnose",
                {
                    "ok": True,
                    "code_count": len(produced.codes),
                    "citation_count": len(produced.cited_wo_ids),
                },
            ),
        }

    def verify(state: OpsState) -> OpsState:
        work_orders = _work_orders(state)
        raw = state.get("diagnosis")
        diagnosis: Diagnosis | None = None
        try:
            diagnosis = Diagnosis.model_validate(raw)
        except Exception:
            diagnosis = None
        if work_orders is None or diagnosis is None:
            return _stop("verify", "error", None, ok=False)
        verified = verify_diagnosis(diagnosis, work_orders)
        return {
            "diagnosis": verified.as_state(),
            "draft_citations": list(verified.cited_wo_ids),
            "trace": _trace(
                "verify",
                {
                    "ok": True,
                    "codes": list(verified.codes),
                    "ids": list(verified.cited_wo_ids),
                    "dropped_count": verified.dropped,
                    "citation_count": len(verified.cited_wo_ids),
                },
            ),
        }

    def guard(state: OpsState) -> OpsState:
        case = _case(state, labels())
        verified = VerifiedDiagnosis.from_state(state.get("diagnosis"))
        if case is None or verified is None:
            return _stop("guard", "error", None, ok=False)
        text = diagnosis_text(
            case.asset,
            case.symptom,
            case.symptom_label,
            verified,
            case.work_orders,
            case.catalog,
        )
        rule = guard_rule(text, case, state.get("request_redacted") or "", ctx)
        if rule is not None:
            # The rule's name (a code, never a value) is kept for the evaluation's counts.
            return _stop("guard", "withheld", "output_pi", ok=False, rule=rule)
        update: OpsState = {"draft_body": text, "trace": _trace("guard", {"ok": True})}
        if state.get("flow") != "work_request":
            update["outcome"] = "answered"
            update["refusal"] = None
        return update

    def propose(state: OpsState) -> OpsState:
        asset = _asset(state)
        symptom = _symptom(state)
        verified = VerifiedDiagnosis.from_state(state.get("diagnosis"))
        if asset is None or verified is None:
            return _stop("propose", "error", None)
        if symptom is None:
            # The write needs a closed symptom code; none was read in the request.
            return _stop("propose", "withheld", "invalid_args")
        if not verified.cited_wo_ids:
            # A work request cites at least one work order read for this asset.
            return _stop("propose", "withheld", "ungrounded")
        args = work_request_args(asset.asset_id, symptom, verified)
        action = _action(args)
        return {
            "draft_id": uuid.uuid4().hex,
            "proposed_action": action.model_dump(mode="json"),
            "edit_round": 0,
            "trace": _trace(
                "propose",
                {
                    "tool": action.tool,
                    "edit_round": 0,
                    "citation_count": len(args.cited_wo_ids),
                    "ids": [asset.asset_id, *args.cited_wo_ids],
                },
            ),
        }

    builder: StateGraph[OpsState, None, OpsState, OpsState] = StateGraph(OpsState)
    nodes: dict[str, OpsNode] = {
        "route": route,
        "fetch": fetch,
        "gate": gate,
        "quarantine": quarantine,
        "diagnose": diagnose,
        "verify": verify,
        "guard": guard,
        "propose": propose,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "route")
    chain = ("route", "fetch", "gate", "quarantine", "diagnose", "verify", "guard")
    for here, there in itertools.pairwise(chain):
        builder.add_conditional_edges(here, _after(there), {there: there, END: END})
    builder.add_conditional_edges("guard", _after_guard, {"propose": "propose", END: END})
    builder.add_conditional_edges(
        "propose", _after(APPROVE_NODE), {APPROVE_NODE: APPROVE_NODE, END: END}
    )
    add_approval_nodes(
        builder,
        executor=executor,
        check_edit=make_check_edit(),
        max_edit_rounds=max_edit_rounds,
    )

    def build(saver: BaseCheckpointSaver[str]) -> OpsGraph:
        return builder.compile(checkpointer=saver)

    return build
