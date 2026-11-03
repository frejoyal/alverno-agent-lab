"""Customer-service benchmark (module 3a, ADR 0015 « Banc d'essai » and « Répétitions »).

``alverno service bench`` runs :func:`~alverno_lab.evals.service_eval.run_service_eval`
in full mode for each chat model: the default model k times (``--repeats``), every other
model once. This module holds what the command publishes:

- :class:`SvcBenchRow`: one model, its k reports as measured and the gate of each
  repetition (safety counts, control rates and preconditions on the repetition itself,
  service bounds on the median of the k values, through
  :func:`~alverno_lab.evals.service_eval.with_service_medians`);
- :func:`svc_bench_table` and :func:`svc_bench_markdown`: one row per model, a short
  legend, the rule-of-three bounds (ADR 0010) and the run note (dataset, fixture,
  threshold and registry digests, k per model, Ollama version);
- :func:`svc_bench_blockers` and :func:`svc_unmet_preconditions`: what keeps the README
  as it is;
- :func:`canary_hit_files` and :func:`ollama_server_version`, shared with the 3b bench
  (:mod:`alverno_lab.evals.ops_bench_common`).

Every cell and note holds metrics, codes, model names and digests: never a request, a
draft, a contact or a canary.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from alverno_lab.evals.ops_bench_common import (
    OPS_SAFETY_COUNTS,
    BenchLang,
    OpsBenchRow,
    OpsRunNote,
    ServiceColumn,
    bench_table,
    canary_hit_files,
    digest_lines,
    ollama_server_version,
    ops_bench_blockers,
    ops_unmet_preconditions,
    rule_of_three_bound,
)
from alverno_lab.evals.service_eval import SvcEvalReport, service_counts, with_service_medians

__all__ = [
    "SVC_PRECONDITIONS",
    "SVC_SAFETY_COUNTS",
    "SvcBenchRow",
    "SvcRunNote",
    "canary_hit_files",
    "ollama_server_version",
    "svc_bench_blockers",
    "svc_bench_markdown",
    "svc_bench_table",
    "svc_unmet_preconditions",
]

# The 22 zero-tolerance counts of ADR 0015, the same as in 3b.
SVC_SAFETY_COUNTS: tuple[str, ...] = OPS_SAFETY_COUNTS
# ADR 0015 « Préconditions exactes » (3a): each one is a ``min`` and a ``max`` bound.
SVC_PRECONDITIONS: tuple[str, ...] = (
    "n_items",
    "n_traps",
    "n_pairs",
    "n_approval_items",
    "n_reject",
    "n_edit",
    "n_injection_items",
    "n_canary_items",
)
# Service metrics: (metric, English label, French label); the median when k > 1.
_SERVICE_COLUMNS: tuple[ServiceColumn, ...] = (
    ("key_fact_recall", "key facts", "faits clés"),
    ("status_fidelity", "status fidelity", "fidélité du statut"),
    ("task_success", "task success", "tâches réussies"),
    ("draft_withheld_rate", "drafts withheld", "brouillons retenus"),
)
# The run note of the 3a bench: dataset, thresholds, profile, Ollama version, registry.
SvcRunNote = OpsRunNote


@dataclasses.dataclass(frozen=True)
class SvcBenchRow(OpsBenchRow[SvcEvalReport]):
    """One benchmarked chat model: its reports as measured, one gate per repetition."""

    def gated(self) -> Sequence[SvcEvalReport]:
        """The reports with their service metrics replaced by the median (ADR 0015)."""
        return with_service_medians(self.reports)


def _note(rows: Sequence[SvcBenchRow], lang: BenchLang, note: SvcRunNote) -> list[str]:
    report = rows[0].reports[0]
    m = report.metrics
    measured = dt.datetime.fromisoformat(report.created_at).astimezone().date().isoformat()
    n_pairs, n_canary = int(m.get("n_pairs", 0)), int(m.get("n_canary_items", 0))
    ks = ", ".join(f"`{row.model}` k = {row.k}" for row in rows)
    traps = rule_of_three_bound(rows, "trap_refusal_rate", lang, service_counts)
    leaks = rule_of_three_bound(rows, "answer_leak_count", lang, service_counts)
    digests = digest_lines(report, note, lang)
    if lang == "fr":
        return [
            f"Mesures prises le {measured} sur {report.n_items} demandes par répétition "
            f"({report.n_answerable} répondables, {report.n_traps} pièges ; {n_pairs} paires ; "
            f"{n_canary} items avec témoins). Porte : profil `{note.profile}` de "
            f"`{note.thresholds}`, sur chaque répétition : chaque compte de sécurité à 0, "
            "chaque taux de contrôle à 1,0 et chaque précondition respectée ; les bornes de "
            "service s'appliquent à la médiane des k répétitions (ADR 0015). Répétitions : "
            f"{ks}. Version d'Ollama : {note.ollama_version}.",
            "",
            "Légende : une marque de porte par répétition ; faits clés, fidélité du statut, "
            "tâches réussies et brouillons retenus à la médiane quand k > 1 ; comptes de "
            f"sécurité non nuls : combien des {len(SVC_SAFETY_COUNTS)} comptes à tolérance "
            "zéro de l'ADR 0015 dépassent 0 sur au moins une répétition ; erreurs : "
            "`error_rate` la plus haute des répétitions ; p50 et p95 : latence par demande, "
            "médiane des répétitions ; jetons : invite et réponse, médiane par répétition.",
            "",
            "Règle de trois (ADR 0010), pire répétition de chaque modèle : pièges "
            f"déterministes non refusés, {traps} ; items dont un texte servi contient un "
            f"témoin, {leaks}. L'affirmation « aucune fuite » ne couvre que les formats "
            "détectés (ADR 0006) et les valeurs des fixtures.",
            "",
            *digests,
            "",
            "Généré par `alverno service bench` ; ne pas modifier à la main.",
        ]
    return [
        f"Measured on {measured} over {report.n_items} requests per repetition "
        f"({report.n_answerable} answerable, {report.n_traps} traps; {n_pairs} pairs; "
        f"{n_canary} items with canaries). Gate: profile `{note.profile}` of "
        f"`{note.thresholds}`, on each repetition: every safety count at 0, every control "
        "rate at 1.0 and every precondition met; the service bounds apply to the median of "
        f"the k repetitions (ADR 0015). Repetitions: {ks}. Ollama version: "
        f"{note.ollama_version}.",
        "",
        "Legend: one gate mark per repetition; key facts, status fidelity, task success and "
        "drafts withheld are medians when k > 1; non-zero safety counts: how many of the "
        f"{len(SVC_SAFETY_COUNTS)} zero-tolerance counts of ADR 0015 are above 0 on at least "
        "one repetition; errors: the highest `error_rate` of the repetitions; p50 and p95: "
        "latency per request, median of the repetitions; tokens: prompt and completion, "
        "median per repetition.",
        "",
        f"Rule of three (ADR 0010), worst repetition of each model: deterministic traps not "
        f"refused, {traps}; items with a canary in a served text, {leaks}. The no-leak claim "
        "covers only the detected formats (ADR 0006) and the fixture values.",
        "",
        *digests,
        "",
        "Generated by `alverno service bench`; do not hand-edit.",
    ]


def svc_bench_table(
    rows: Sequence[SvcBenchRow], lang: BenchLang = "en", *, note: SvcRunNote
) -> str:
    """3a model comparison table and its run note, for the RESULTS-SVC markers."""
    return bench_table(rows, lang, _SERVICE_COLUMNS, lambda: _note(rows, lang, note))


def svc_bench_markdown(
    rows: Sequence[SvcBenchRow], backend: str, lang: BenchLang = "en", *, note: SvcRunNote
) -> str:
    """Standalone 3a bench report: a title plus :func:`svc_bench_table`."""
    title = (
        f"# Banc d'essai du service à la clientèle — mode complet ({backend})"
        if lang == "fr"
        else f"# Customer service benchmark — full mode ({backend})"
    )
    return f"{title}\n\n{svc_bench_table(rows, lang, note=note)}"


def svc_bench_blockers(rows: Sequence[SvcBenchRow]) -> list[str]:
    """Models that block a README update: a transport error or a persisted canary on any
    repetition (a missing metric counts as one)."""
    return ops_bench_blockers(rows)


def svc_unmet_preconditions(rows: Sequence[SvcBenchRow], thresholds: Path) -> list[str]:
    """Preconditions a repetition misses, as ``model r<i>: metric = value (min, max)``.

    Each precondition is both a ``min`` and a ``max`` bound of the report's profile
    (ADR 0015): a truncated or enlarged set fails. An unreadable thresholds file, a missing
    profile, bound or metric counts as unmet (fail closed).
    """
    return ops_unmet_preconditions(rows, thresholds, SVC_PRECONDITIONS)
