"""Maintenance benchmark (module 3b, ADR 0015 « Banc d'essai » and « Répétitions »).

``alverno maint bench`` runs :func:`~alverno_lab.evals.maint_eval.run_maint_eval` in full
mode for each chat model: the default model k times (``--repeats``), every other model
once. It mirrors the 3a bench (:mod:`alverno_lab.evals.service_bench`) and shares its
rows, cells and checks (:mod:`alverno_lab.evals.ops_bench_common`). This module holds
what is proper to 3b:

- :class:`MaintBenchRow`: one model, its k reports as measured and the gate of each
  repetition (safety counts, control rates and preconditions on the repetition itself,
  the ``maint-full-*`` service bounds on the median of the k values, through
  :func:`~alverno_lab.evals.maint_eval.with_maint_medians`);
- :func:`maint_bench_table` and :func:`maint_bench_markdown`: one row per model (diagnosis
  hit@1 and hit@3, frequency traps hit@1, valid citations), a short legend, the BM25
  reference, the rule-of-three bounds (ADR 0010) and the run note (dataset, fixture,
  threshold and registry digests, k per model, Ollama version);
- :func:`maint_bench_blockers` and :func:`maint_unmet_preconditions`: what keeps the
  README as it is;
- :func:`maint_file_canaries`: what the run folder must never hold.

Every cell and note holds metrics, codes, model names and digests: never a request, a
diagnosis, a remark, a technician value or a canary.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from alverno_lab.config import Settings
from alverno_lab.connectors.sim import SimGmao
from alverno_lab.evals.canaries import load_canaries
from alverno_lab.evals.maint import load_maint_gold
from alverno_lab.evals.maint_eval import MaintEvalReport, maint_counts, with_maint_medians
from alverno_lab.evals.ops_bench_common import (
    OPS_SAFETY_COUNTS,
    BenchLang,
    OpsBenchRow,
    OpsRunNote,
    ServiceColumn,
    bench_table,
    digest_lines,
    num,
    ops_bench_blockers,
    ops_unmet_preconditions,
    rule_of_three_bound,
)
from alverno_lab.ops_pipeline import load_ops_context, ops_quarantine
from alverno_lab.privacy import Canary

__all__ = [
    "MAINT_PRECONDITIONS",
    "MAINT_SAFETY_COUNTS",
    "MaintBenchRow",
    "maint_bench_blockers",
    "maint_bench_markdown",
    "maint_bench_table",
    "maint_file_canaries",
    "maint_unmet_preconditions",
]

# The 22 zero-tolerance counts of ADR 0015, the same as in 3a.
MAINT_SAFETY_COUNTS: tuple[str, ...] = OPS_SAFETY_COUNTS
# ADR 0015 « Préconditions exactes » (3b): each one is a ``min`` and a ``max`` bound.
MAINT_PRECONDITIONS: tuple[str, ...] = (
    "n_items",
    "n_traps",
    "n_pairs",
    "n_approval_items",
    "n_edit",
    "n_freq_traps",
    "n_injection_items",
    "n_canary_items",
)
# Service metrics of ``maint-full-ollama``: (metric, English label, French label); the
# median when k > 1.
_SERVICE_COLUMNS: tuple[ServiceColumn, ...] = (
    ("diagnosis_hit_at_1", "diagnosis hit@1", "diagnostic hit@1"),
    ("diagnosis_hit_at_3", "diagnosis hit@3", "diagnostic hit@3"),
    ("frequency_trap_hit_at_1", "frequency traps hit@1", "pièges de fréquence hit@1"),
    ("citation_validity_rate", "valid citations", "citations valides"),
)


@dataclasses.dataclass(frozen=True)
class MaintBenchRow(OpsBenchRow[MaintEvalReport]):
    """One benchmarked chat model: its reports as measured, one gate per repetition."""

    def gated(self) -> Sequence[MaintEvalReport]:
        """The reports with their service metrics replaced by the median (ADR 0015)."""
        return with_maint_medians(self.reports)


def maint_file_canaries(settings: Settings, dataset: Path | None = None) -> list[Canary]:
    """What a run folder must never hold: the canaries of ``dataset`` (``load_canaries``),
    as :func:`~alverno_lab.evals.maint_eval.run_maint_eval` searches them.

    ``dataset`` defaults to ``settings.maint_eval_path``. The items are first read against
    the simulated GMAO of ``settings``, so a broken set or fixture stops the bench before
    any run (a :class:`~alverno_lab.evals.dataset.DatasetError` or a
    :class:`~alverno_lab.connectors.types.ConnectorError`).
    """
    source = dataset if dataset is not None else settings.maint_eval_path
    ctx = load_ops_context(settings, "maint")
    gmao = SimGmao.from_yaml(settings.ops_gmao_path, quarantine=ops_quarantine(ctx))
    load_maint_gold(source, gmao)
    return list(dict.fromkeys(loaded.canary for loaded in load_canaries(source)))


def _note(rows: Sequence[MaintBenchRow], lang: BenchLang, note: OpsRunNote) -> list[str]:
    report = rows[0].reports[0]
    m = report.metrics
    measured = dt.datetime.fromisoformat(report.created_at).astimezone().date().isoformat()
    n_pairs, n_canary = int(m.get("n_pairs", 0)), int(m.get("n_canary_items", 0))
    n_freq = int(m.get("n_freq_traps", 0))
    ks = ", ".join(f"`{row.model}` k = {row.k}" for row in rows)
    traps = rule_of_three_bound(rows, "trap_refusal_rate", lang, maint_counts)
    leaks = rule_of_three_bound(rows, "answer_leak_count", lang, maint_counts)
    bm25_1, bm25_3 = (num(m.get(f"bm25_hit_at_{top}", 0.0), lang) for top in (1, 3))
    digests = digest_lines(report, note, lang)
    if lang == "fr":
        return [
            f"Mesures prises le {measured} sur {report.n_items} demandes par répétition "
            f"({report.n_answerable} répondables, {report.n_traps} pièges ; {n_pairs} paires ; "
            f"{n_freq} pièges de fréquence ; {n_canary} items avec témoins). Porte : profil "
            f"`{note.profile}` de `{note.thresholds}`, sur chaque répétition : chaque compte "
            "de sécurité à 0, chaque taux de contrôle à 1,0 et chaque précondition "
            "respectée ; les bornes de service s'appliquent à la médiane des k répétitions "
            f"(ADR 0015). Répétitions : {ks}. Version d'Ollama : {note.ollama_version}.",
            "",
            "Légende : une marque de porte par répétition ; diagnostic hit@1 et hit@3 (code "
            "attendu au premier rang, ou parmi les 3 premiers, après `verify`), pièges de "
            "fréquence hit@1 et citations valides à la médiane quand k > 1 ; comptes de "
            f"sécurité non nuls : combien des {len(MAINT_SAFETY_COUNTS)} comptes à tolérance "
            "zéro de l'ADR 0015 dépassent 0 sur au moins une répétition ; erreurs : "
            "`error_rate` la plus haute des répétitions ; p50 et p95 : latence par demande, "
            "médiane des répétitions ; jetons : invite et réponse, médiane par répétition.",
            "",
            "Référence sans borne, calculée sur le jeu sans aucun modèle : le classement "
            f"BM25 de l'historique (`rank_failure_codes`) donne hit@1 {bm25_1} et hit@3 "
            f"{bm25_3} ; les pièges de fréquence sont conçus pour le faire échouer.",
            "",
            "Règle de trois (ADR 0010), pire répétition de chaque modèle : pièges "
            f"déterministes non refusés, {traps} ; items dont un texte servi contient un "
            f"témoin, {leaks}. L'affirmation « aucune fuite » ne couvre que les formats "
            "détectés (ADR 0006) et les valeurs des fixtures.",
            "",
            *digests,
            "",
            "Généré par `alverno maint bench` ; ne pas modifier à la main.",
        ]
    return [
        f"Measured on {measured} over {report.n_items} requests per repetition "
        f"({report.n_answerable} answerable, {report.n_traps} traps; {n_pairs} pairs; "
        f"{n_freq} frequency traps; {n_canary} items with canaries). Gate: profile "
        f"`{note.profile}` of `{note.thresholds}`, on each repetition: every safety count "
        "at 0, every control rate at 1.0 and every precondition met; the service bounds "
        f"apply to the median of the k repetitions (ADR 0015). Repetitions: {ks}. Ollama "
        f"version: {note.ollama_version}.",
        "",
        "Legend: one gate mark per repetition; diagnosis hit@1 and hit@3 (expected code "
        "first, or among the first 3, after `verify`), frequency traps hit@1 and valid "
        "citations are medians when k > 1; non-zero safety counts: how many of the "
        f"{len(MAINT_SAFETY_COUNTS)} zero-tolerance counts of ADR 0015 are above 0 on at "
        "least one repetition; errors: the highest `error_rate` of the repetitions; p50 and "
        "p95: latency per request, median of the repetitions; tokens: prompt and "
        "completion, median per repetition.",
        "",
        "Reference without a bound, computed on the set without any model: the BM25 "
        f"ranking of the history (`rank_failure_codes`) gives hit@1 {bm25_1} and hit@3 "
        f"{bm25_3}; the frequency traps are built to make it fail.",
        "",
        f"Rule of three (ADR 0010), worst repetition of each model: deterministic traps not "
        f"refused, {traps}; items with a canary in a served text, {leaks}. The no-leak claim "
        "covers only the detected formats (ADR 0006) and the fixture values.",
        "",
        *digests,
        "",
        "Generated by `alverno maint bench`; do not hand-edit.",
    ]


def maint_bench_table(
    rows: Sequence[MaintBenchRow], lang: BenchLang = "en", *, note: OpsRunNote
) -> str:
    """3b model comparison table and its run note, for the RESULTS-MAINT markers."""
    return bench_table(rows, lang, _SERVICE_COLUMNS, lambda: _note(rows, lang, note))


def maint_bench_markdown(
    rows: Sequence[MaintBenchRow], backend: str, lang: BenchLang = "en", *, note: OpsRunNote
) -> str:
    """Standalone 3b bench report: a title plus :func:`maint_bench_table`."""
    title = (
        f"# Banc d'essai de l'entretien — mode complet ({backend})"
        if lang == "fr"
        else f"# Maintenance benchmark — full mode ({backend})"
    )
    return f"{title}\n\n{maint_bench_table(rows, lang, note=note)}"


def maint_bench_blockers(rows: Sequence[MaintBenchRow]) -> list[str]:
    """Models that block a README update: a transport error or a persisted canary on any
    repetition (a missing metric counts as one)."""
    return ops_bench_blockers(rows)


def maint_unmet_preconditions(rows: Sequence[MaintBenchRow], thresholds: Path) -> list[str]:
    """Preconditions a repetition misses, as ``model r<i>: metric = value (min, max)``.

    Each precondition is both a ``min`` and a ``max`` bound of the report's profile
    (ADR 0015): a truncated or enlarged set fails. An unreadable thresholds file, a missing
    profile, bound or metric counts as unmet (fail closed).
    """
    return ops_unmet_preconditions(rows, thresholds, MAINT_PRECONDITIONS)
