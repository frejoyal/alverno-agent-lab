"""Task-by-model matrix of module 4 (docs/adr/0022, docs/adr/0024).

``alverno matrix`` runs the existing bench or evaluation of each task as a subprocess,
once per model (screening, k = 1), and reads the model's memory from Ollama's
``/api/ps`` right after the run. It then confirms the lightest admissible model of each
task at k = 3 and applies the pre-registered recommendation rule. Agents, gates and
thresholds are untouched: the matrix only reads the reports that each command writes.

Nothing here talks to a hosted model; the optional hosted reference lives in
:mod:`alverno_lab.evals.hosted_reference`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import statistics
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

GIB = 1024**3
# ADR 0022: estimated tiers of GPU memory, in GB, and the margin a model needs to fit.
TIERS_GB: tuple[int, ...] = (8, 12, 16, 24)
TIER_MARGIN_GB = 1.0
# ADR 0024: two models within this much total memory are ranked by p95 latency.
TIE_GB = 0.5
CONFIRM_K = 3

CHAT_MODELS: tuple[str, ...] = (
    "gemma4:e4b",
    "ministral-3:8b",
    "llama3.2:3b",
    "gemma3:latest",
    "llama3:latest",
)
# 18 GB, more than the card: measured on T1 only (ADR 0022).
T1_ONLY_MODELS: tuple[str, ...] = ("gemma4:26b",)
EMBED_MODELS: tuple[str, ...] = ("qwen3-embedding:0.6b", "bge-m3", "nomic-embed-text")

# Fixed probe of generation speed (precision of ADR 0022, 2026-11-11): the same French
# prompt and 128 tokens for every model, right after its run.
SPEED_PROMPT = (
    "Rédige en français un paragraphe de cinq phrases qui explique à un nouvel employé "
    "pourquoi on vérifie une pièce usinée avant de l'expédier au client."
)
SPEED_TOKENS = 128

# Canaries of the eight sets (ADR 0024 « Règles d'exécution »).
CANARY_SETS: tuple[str, ...] = (
    "hr_gold.jsonl",
    "hr_holdout.jsonl",
    "svc_gold.jsonl",
    "svc_holdout.jsonl",
    "maint_gold.jsonl",
    "maint_holdout.jsonl",
    "dir_gold.jsonl",
    "dir_holdout.jsonl",
)
# Folders the matrix writes but never publishes (git-ignored): never scanned as reports.
UNPUBLISHED = ("index", "access", "logs", "traces")

Style = Literal["quality", "hr", "ops", "dir", "retrieval"]
Stage = Literal["screen", "confirm"]


@dataclasses.dataclass(frozen=True)
class Task:
    """One task of the matrix and how its existing command is run."""

    id: str
    kind: Literal["chat", "embed"]
    style: Style
    profile: str  # backend-free profile prefix, e.g. "full" -> full-ollama / full-fake
    label_fr: str
    label_en: str
    shown: tuple[str, ...]  # quality metrics published in the matrix
    command: tuple[str, ...] = ()

    def profiles(self, backend: str) -> list[str]:
        if self.style == "retrieval":
            return [f"retrieval-{backend}", f"hr-retrieval-{backend}"]
        return [f"{self.profile}-{backend}"]


TASKS: dict[str, Task] = {
    task.id: task
    for task in (
        Task(
            "T1",
            "chat",
            "quality",
            "full",
            "Qualité",
            "Quality",
            ("key_fact_recall", "citation_validity_rate", "trap_refusal_accuracy"),
            ("bench",),
        ),
        Task(
            "T2",
            "chat",
            "hr",
            "hr-full",
            "RH",
            "HR",
            ("key_fact_recall", "citation_validity_rate", "privacy_trap_refusal"),
            ("hr", "bench"),
        ),
        Task(
            "T3a",
            "chat",
            "ops",
            "svc-full",
            "Service",
            "Service",
            ("task_success", "key_fact_recall", "status_fidelity"),
            ("service", "bench"),
        ),
        Task(
            "T3b",
            "chat",
            "ops",
            "maint-full",
            "Entretien",
            "Maintenance",
            ("diagnosis_hit_at_1", "citation_validity_rate", "argument_accuracy"),
            ("maint", "bench"),
        ),
        Task(
            "T5b",
            "chat",
            "dir",
            "dir-full",
            "Direction",
            "Management",
            ("task_success", "count_fidelity", "key_fact_recall"),
            ("direction", "bench"),
        ),
        Task(
            "T0",
            "embed",
            "retrieval",
            "retrieval",
            "Recherche",
            "Retrieval",
            ("hit_at_1", "mrr", "context_recall"),
        ),
    )
}
CHAT_TASKS = ("T1", "T2", "T3a", "T3b", "T5b")


def models_for(
    task: Task, chat: Sequence[str], embed: Sequence[str], heavy: Sequence[str]
) -> list[str]:
    """The models of ``task`` (ADR 0022 « Modèles »): heavy models on T1 only."""
    if task.kind == "embed":
        return list(embed)
    return list(chat) + (list(heavy) if task.id == "T1" else [])


def screening_order(
    tasks: Sequence[str], chat: Sequence[str], embed: Sequence[str], heavy: Sequence[str]
) -> list[tuple[str, str]]:
    """Execution order of the screening: the order changes no result, only the time.

    Retrieval first (minutes), then every task of one chat model before the next model
    (no reload of the card between tasks), and the heavy models last: they cannot
    change a decision unless every lighter model fails.
    """
    pairs = [("T0", model) for model in embed if "T0" in tasks]
    for model in [*chat, *heavy]:
        for task_id in tasks:
            task = TASKS[task_id]
            if task.kind == "chat" and model in models_for(task, chat, embed, heavy):
                pairs.append((task_id, model))
    return pairs


def stem(model: str) -> str:
    return re.sub(r"[^\w.-]+", "_", model)


# --------------------------------------------------------------------------- hardware
@dataclasses.dataclass(frozen=True)
class Hardware:
    """Memory of one loaded model, read from ``/api/ps`` (ADR 0022)."""

    vram_gb: float
    ram_spill_gb: float
    context_length: int | None

    @property
    def total_gb(self) -> float:
        return self.vram_gb + self.ram_spill_gb


def hardware_from_ps(payload: Mapping[str, Any], model: str) -> Hardware | None:
    """The entry of ``model`` in an ``/api/ps`` answer, or None when it is not loaded."""
    for entry in payload.get("models") or []:
        if not isinstance(entry, Mapping):
            continue
        names = {str(entry.get("name", "")), str(entry.get("model", ""))}
        if model not in names and f"{model}:latest" not in names:
            continue
        size = int(entry.get("size") or 0)
        vram = int(entry.get("size_vram") or 0)
        context = entry.get("context_length")
        return Hardware(
            vram_gb=round(vram / GIB, 2),
            ram_spill_gb=round(max(size - vram, 0) / GIB, 2),
            context_length=int(context) if isinstance(context, int) else None,
        )
    return None


def loaded_models(payload: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(e.get("name", "")) for e in payload.get("models") or [] if isinstance(e, Mapping)
    )


def tokens_per_s(payload: Mapping[str, Any]) -> float | None:
    """Generation speed of one ``/api/generate`` answer (``eval_count`` / ``eval_duration``)."""
    count = payload.get("eval_count")
    duration = payload.get("eval_duration")
    if not isinstance(count, int) or not isinstance(duration, int) or duration <= 0:
        return None
    return round(count / (duration / 1e9), 1)


class OllamaProbe:
    """Reads ``/api/ps`` and measures generation speed on the local server."""

    def __init__(
        self, base_url: str, num_ctx: int, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        # Local server only (checked by each command): no proxy.
        return httpx.Client(
            base_url=self.base_url, timeout=timeout, transport=self.transport, trust_env=False
        )

    def ps(self) -> dict[str, Any]:
        with self._client(10.0) as client:
            response = client.get("/api/ps")
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {}

    def speed(self, model: str) -> float | None:
        body = {
            "model": model,
            "prompt": SPEED_PROMPT,
            "stream": False,
            "options": {
                "num_predict": SPEED_TOKENS,
                "num_ctx": self.num_ctx,
                "temperature": 0,
                "seed": 0,
            },
        }
        with self._client(600.0) as client:
            response = client.post("/api/generate", json=body)
            response.raise_for_status()
            data = response.json()
        return tokens_per_s(data) if isinstance(data, dict) else None


# --------------------------------------------------------------------------- results
@dataclasses.dataclass
class Run:
    """One screening or confirmation of a (task, model) pair."""

    task: str
    model: str
    stage: Stage
    k: int
    gates: list[bool | None]
    error_rates: list[float]
    exit_codes: list[int]
    safety_nonzero: list[str]
    metrics: dict[str, float]
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    vram_gb: float | None = None
    ram_spill_gb: float | None = None
    context_length: int | None = None
    tokens_per_s: float | None = None
    loaded_before: list[str] = dataclasses.field(default_factory=list)
    folder: str = ""
    note: str = ""

    @property
    def total_gb(self) -> float | None:
        if self.vram_gb is None or self.ram_spill_gb is None:
            return None
        return round(self.vram_gb + self.ram_spill_gb, 2)

    @property
    def admissible(self) -> bool:
        """ADR 0024 « Admissibilité »: every gate passes, no safety count, no error."""
        return (
            bool(self.gates)
            and all(g is True for g in self.gates)
            and all(code == 0 for code in self.exit_codes)
            and all(rate == 0 for rate in self.error_rates)
            and not self.safety_nonzero
        )

    def to_json(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["total_gb"] = self.total_gb
        data["admissible"] = self.admissible
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Run:
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


def safety_counts(thresholds: Path, profile: str) -> list[str]:
    """Counts bounded at 0 in ``profile``: integer ``max`` bounds of 0, not preconditions."""
    raw = yaml.safe_load(thresholds.read_text(encoding="utf-8")) or {}
    bounds = (raw.get(profile) or {}).get("max") or {}
    return sorted(
        key
        for key, value in bounds.items()
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value == 0
        and not key.startswith("n_")
    )


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def summarize(
    task: Task,
    model: str,
    stage: Stage,
    reports: Sequence[Mapping[str, float]],
    gates: Sequence[bool | None],
    exit_codes: Sequence[int],
    counts: Sequence[str],
    folder: str,
) -> Run:
    """A :class:`Run` from the metrics of its reports (medians over the repetitions)."""
    keys = [*task.shown, "latency_p50_ms", "latency_p95_ms"]
    medians = {key: _median([float(r[key]) for r in reports if key in r]) for key in keys}
    return Run(
        task=task.id,
        model=model,
        stage=stage,
        k=len(gates),
        gates=list(gates),
        error_rates=[float(r.get("error_rate", 0.0)) for r in reports],
        exit_codes=list(exit_codes),
        safety_nonzero=sorted({c for c in counts for r in reports if float(r.get(c, 0)) > 0}),
        metrics={k: round(v, 4) for k, v in medians.items() if v is not None and k in task.shown},
        latency_p50_ms=medians["latency_p50_ms"],
        latency_p95_ms=medians["latency_p95_ms"],
        folder=folder,
    )


# --------------------------------------------------------------------------- rule
def rank(runs: Sequence[Run]) -> list[Run]:
    """Admissible runs, lightest first (ADR 0024 « Classement »).

    Sorted by total memory; a run within ``TIE_GB`` of the first run of its group joins
    that group, and each group is ordered by p95 latency.
    """
    eligible = sorted(
        (r for r in runs if r.admissible and r.total_gb is not None),
        key=lambda r: (r.total_gb or 0.0, r.latency_p95_ms or 0.0, r.model),
    )
    ordered: list[Run] = []
    group: list[Run] = []
    for run in eligible:
        if group and (run.total_gb or 0.0) - (group[0].total_gb or 0.0) > TIE_GB:
            ordered += sorted(group, key=lambda r: (r.latency_p95_ms or 0.0, r.model))
            group = []
        group.append(run)
    ordered += sorted(group, key=lambda r: (r.latency_p95_ms or 0.0, r.model))
    return ordered


def tier(total_gb: float | None) -> int | None:
    """Smallest tier that holds the model with the margin; None above the last tier."""
    if total_gb is None:
        return None
    for size in TIERS_GB:
        if total_gb + TIER_MARGIN_GB <= size:
            return size
    return None


def strongest(task: Task, runs: Sequence[Run]) -> Run | None:
    """The best screening run on the task's first published metric, for information."""
    pool = [r for r in runs if r.admissible] or list(runs)
    if not pool:
        return None
    return max(pool, key=lambda r: tuple(r.metrics.get(k, -1.0) for k in task.shown))


@dataclasses.dataclass
class Decision:
    task: str
    retained: str | None
    tier_gb: int | None
    total_gb: float | None
    tried: list[str]
    strongest: str | None

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- execution
Runner = Callable[[Sequence[str], Mapping[str, str], Path], int]


def subprocess_runner(argv: Sequence[str], env: Mapping[str, str], log: Path) -> int:
    """Run ``alverno <argv>`` in a fresh interpreter; output goes to ``log``."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        done = subprocess.run(
            [sys.executable, "-m", "alverno_lab.cli", *argv],
            env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"},
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return done.returncode


@dataclasses.dataclass
class Matrix:
    """Runs the matrix into ``out``; each finished pair is kept, so a run can resume."""

    out: Path
    thresholds: Path
    fake: bool = False
    limit: int | None = None
    runner: Runner = subprocess_runner
    probe: OllamaProbe | None = None
    echo: Callable[[str], None] = print

    @property
    def backend(self) -> str:
        return "fake" if self.fake else "ollama"

    def _common(self) -> list[str]:
        args = ["--thresholds", str(self.thresholds)]
        if self.fake:
            args.append("--fake")
        if self.limit is not None:
            args += ["--limit", str(self.limit)]
        return args

    def _result_path(self, stage: Stage, task: Task, model: str) -> Path:
        return self.out / stage / task.id / stem(model) / "result.json"

    def load(self, stage: Stage, task: Task, model: str) -> Run | None:
        path = self._result_path(stage, task, model)
        if not path.is_file():
            return None
        return Run.from_json(json.loads(path.read_text(encoding="utf-8")))

    def run(self, stage: Stage, task: Task, model: str) -> Run:
        """The pair's result: read back when present, measured otherwise."""
        done = self.load(stage, task, model)
        if done is not None:
            return done
        folder = self._result_path(stage, task, model).parent
        log = self.out / "logs" / f"{stage}-{task.id}-{stem(model)}.log"
        before = loaded_models(self.probe.ps()) if self.probe else []
        k = 1 if stage == "screen" else CONFIRM_K
        self.echo(f"[{stage}] {task.id} / {model} (k = {k})")
        if task.style == "retrieval":
            result = self._retrieval(task, model, stage, folder, log, k)
        elif task.style in ("ops", "dir"):
            result = self._repeating_bench(task, model, stage, folder, log, k)
        else:
            result = self._single_bench(task, model, stage, folder, log, k)
        result.loaded_before = before
        if self.probe is not None:
            hardware = hardware_from_ps(self.probe.ps(), model)
            if hardware is not None:
                result.vram_gb = hardware.vram_gb
                result.ram_spill_gb = hardware.ram_spill_gb
                result.context_length = hardware.context_length
            else:
                result.note = (result.note + "; " if result.note else "") + "not in /api/ps"
            if task.kind == "chat":
                result.tokens_per_s = self.probe.speed(model)
        path = self._result_path(stage, task, model)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return result

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.out).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _bench_dir(folder: Path) -> Path | None:
        found = sorted(p for p in folder.glob("bench-*") if p.is_dir())
        return found[-1] if found else None

    def _single_bench(
        self, task: Task, model: str, stage: Stage, folder: Path, log: Path, k: int
    ) -> Run:
        """T1 and T2: their benches have no repetitions, so k runs are k invocations.

        Each invocation is gated on its own (precision of ADR 0024, 2026-11-11).
        """
        reports: list[Mapping[str, float]] = []
        gates: list[bool | None] = []
        codes: list[int] = []
        notes: list[str] = []
        for index in range(1, k + 1):
            where = folder if k == 1 else folder / f"r{index}"
            argv = [*task.command, "--models", model, "--out", str(where), *self._common()]
            env = {"ALVERNO_CHAT_MODEL": model}
            code = self.runner(argv, env, log.with_suffix(f".r{index}.log"))
            codes.append(code)
            bench = self._bench_dir(where)
            summary = bench / "summary.json" if bench else None
            if summary is None or not summary.is_file():
                gates.append(None)
                notes.append(f"no summary (exit {code})")
                continue
            entry = next(
                (e for e in json.loads(summary.read_text(encoding="utf-8")) if e["model"] == model),
                None,
            )
            if entry is None:
                gates.append(None)
                continue
            gates.append(entry.get("gate_passed"))
            reports.append(entry["report"]["metrics"])
        profile = task.profiles(self.backend)[0]
        run = summarize(
            task,
            model,
            stage,
            reports,
            gates,
            codes,
            safety_counts(self.thresholds, profile),
            self._rel(folder),
        )
        run.note = "; ".join(notes)
        return run

    def _repeating_bench(
        self, task: Task, model: str, stage: Stage, folder: Path, log: Path, k: int
    ) -> Run:
        """T3a, T3b and T5b: the module's bench repeats its default model k times and
        gates each repetition on the medians of the service metrics."""
        argv = [
            *task.command,
            "--models",
            model,
            "--repeats",
            str(k),
            "--out",
            str(folder),
            *self._common(),
        ]
        code = self.runner(argv, {"ALVERNO_CHAT_MODEL": model}, log)
        bench = self._bench_dir(folder)
        profile = task.profiles(self.backend)[0]
        counts = safety_counts(self.thresholds, profile)
        if bench is None:
            run = summarize(task, model, stage, [], [None], [code], counts, self._rel(folder))
            run.note = f"no bench folder (exit {code})"
            return run
        if task.style == "ops":
            summary = json.loads((bench / "summary.json").read_text(encoding="utf-8"))
            entry = next(e for e in summary["models"] if e["model"] == model)
            gates = list(entry["gates"])
            reports = [r["metrics"] for r in entry["reports"]]
        else:
            from alverno_lab.evals.direction_bench import gate_row
            from alverno_lab.evals.direction_eval import DirEvalReport

            base = stem(model)
            files = sorted(
                p
                for p in bench.glob(f"{base}*.json")
                if re.fullmatch(rf"{re.escape(base)}(-r\d+)?\.json", p.name)
            )
            parsed = [
                DirEvalReport.model_validate_json(p.read_text(encoding="utf-8")) for p in files
            ]
            gates = list(gate_row(model, parsed, self.thresholds).gates) or [None]
            reports = [r.metrics for r in parsed]
        return summarize(task, model, stage, reports, gates, [code], counts, self._rel(folder))

    def _retrieval(
        self, task: Task, model: str, stage: Stage, folder: Path, log: Path, k: int
    ) -> Run:
        """T0: both retrieval sets with the embedder's own index in the report folder.

        The lab's index (``.cache/``) is never touched (ADR 0022 « Index des plongements »).
        """
        from alverno_lab.evals.thresholds import check_thresholds

        index = folder / "index"
        env = {
            "ALVERNO_EMBED_MODEL": model,
            "ALVERNO_INDEX_PATH": str(index / "quality" / "index.npz"),
            "ALVERNO_HR_INDEX_PATH": str(index / "hr" / "index.npz"),
        }
        fake = ["--fake"] if self.fake else []
        codes = [
            self.runner(["index", *fake], env, log.with_suffix(".index.log")),
            self.runner(["hr", "index", *fake], env, log.with_suffix(".hr-index.log")),
        ]
        merged: list[dict[str, float]] = []
        gates: list[bool | None] = []
        for index_run in range(1, k + 1):
            where = folder if k == 1 else folder / f"r{index_run}"
            per_run: dict[str, float] = {}
            run_gates: list[bool | None] = []
            for argv, profile in (
                (["eval", "--mode", "retrieval"], f"retrieval-{self.backend}"),
                (["hr", "eval", "--mode", "retrieval"], f"hr-retrieval-{self.backend}"),
            ):
                cmd = [*argv, "--no-gate", "--out", str(where), *self._common()]
                codes.append(self.runner(cmd, env, log.with_suffix(f".{profile}.r{index_run}.log")))
                path = where / f"eval-{profile}.json"
                if not path.is_file():
                    run_gates.append(None)
                    continue
                metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
                result = check_thresholds(_Gated(profile, metrics), self.thresholds)
                run_gates.append(result.passed)
                prefix = "" if profile.startswith("retrieval") else "hr_"
                per_run.update({f"{prefix}{key}": value for key, value in metrics.items()})
                if prefix:
                    # The published metrics are those of the quality set; the HR set
                    # gates the run and its error rate counts.
                    per_run["error_rate"] = max(
                        per_run.get("error_rate", 0.0), float(metrics.get("error_rate", 0.0))
                    )
            gates.append(None if None in run_gates else all(run_gates))
            merged.append(per_run)
        # The HR set's privacy counts are kept under their ``hr_`` names.
        counts = [
            *safety_counts(self.thresholds, f"retrieval-{self.backend}"),
            *(f"hr_{c}" for c in safety_counts(self.thresholds, f"hr-retrieval-{self.backend}")),
        ]
        return summarize(task, model, stage, merged, gates, codes, counts, self._rel(folder))


@dataclasses.dataclass(frozen=True)
class _Gated:
    profile: str
    metrics: Mapping[str, float]


def decide(
    matrix: Matrix,
    task: Task,
    screened: Sequence[Run],
    confirm: bool = True,
) -> tuple[Decision, list[Run]]:
    """Apply the rule of ADR 0024 to one task; returns the decision and its confirmations."""
    tried: list[str] = []
    confirmations: list[Run] = []
    retained: Run | None = None
    for candidate in rank(screened):
        if not confirm:
            break
        tried.append(candidate.model)
        check = matrix.run("confirm", task, candidate.model)
        confirmations.append(check)
        if check.admissible:
            retained = candidate
            break
    best = strongest(task, screened)
    total = retained.total_gb if retained else None
    return (
        Decision(
            task=task.id,
            retained=retained.model if retained else None,
            tier_gb=tier(total) if retained else None,
            total_gb=total,
            tried=tried,
            strongest=best.model if best else None,
        ),
        confirmations,
    )


def overall_tier(decisions: Sequence[Decision]) -> int | None:
    """Tier of the heaviest retained chat model (ADR 0024 « Palier de matériel »)."""
    chosen = [d for d in decisions if d.task in CHAT_TASKS and d.retained is not None]
    if not chosen or any(d.tier_gb is None for d in chosen):
        return None
    return max(d.tier_gb or 0 for d in chosen)


# --------------------------------------------------------------------------- rendering
Lang = Literal["fr", "en"]
# matrix.md lives in reports/matrix-<date>/.
ADR_0024 = "../../docs/adr/0024-bench-evaluation-protocol.md"
ADR_0023 = "../../docs/adr/0023-hosted-reference.md"
_YES = {"fr": "oui", "en": "yes"}
_NO = {"fr": "non", "en": "no"}
_DASH = "—"


def _num(value: float | None, lang: Lang, digits: int = 2) -> str:
    if value is None:
        return _DASH
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "fr" else text


def _seconds(ms: float | None, lang: Lang) -> str:
    return _DASH if ms is None else f"{_num(ms / 1000, lang, 1)} s"


def _gate_mark(run: Run) -> str:
    if not run.gates or any(g is None for g in run.gates):
        return _DASH
    return "✅" if all(run.gates) else "❌"


def runs_table(task: Task, runs: Sequence[Run], lang: Lang) -> str:
    head = {
        "fr": [
            "Modèle",
            "k",
            "Porte",
            "Admissible",
            "Comptes non nuls",
            "Erreurs",
            *task.shown,
            "p50",
            "p95",
            "jetons/s",
            "Carte (Go)",
            "Mémoire vive (Go)",
            "Total (Go)",
        ],
        "en": [
            "Model",
            "k",
            "Gate",
            "Admissible",
            "Non-zero counts",
            "Errors",
            *task.shown,
            "p50",
            "p95",
            "tokens/s",
            "GPU (GB)",
            "RAM spill (GB)",
            "Total (GB)",
        ],
    }[lang]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for run in runs:
        cells = [
            f"`{run.model}`",
            str(run.k),
            _gate_mark(run),
            _YES[lang] if run.admissible else _NO[lang],
            ", ".join(f"`{c}`" for c in run.safety_nonzero) or "0",
            _num(max(run.error_rates, default=0.0), lang, 3),
            *(_num(run.metrics.get(key), lang, 3) for key in task.shown),
            _seconds(run.latency_p50_ms, lang),
            _seconds(run.latency_p95_ms, lang),
            _num(run.tokens_per_s, lang, 1),
            _num(run.vram_gb, lang),
            _num(run.ram_spill_gb, lang),
            _num(run.total_gb, lang),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def decisions_table(decisions: Sequence[Decision], lang: Lang) -> str:
    none = {
        "fr": "aucun modèle local recommandé sur ce poste",
        "en": "no local model recommended on this workstation",
    }
    above = {"fr": "au-delà de 24 Go", "en": "above 24 GB"}
    head = {
        "fr": [
            "Tâche",
            "Modèle retenu",
            "Mémoire (Go)",
            "Palier",
            "Confirmations tentées",
            "Plus fort (information)",
        ],
        "en": [
            "Task",
            "Retained model",
            "Memory (GB)",
            "Tier",
            "Confirmations tried",
            "Strongest (information)",
        ],
    }[lang]
    unit = "Go" if lang == "fr" else "GB"
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for d in decisions:
        task = TASKS[d.task]
        label = task.label_fr if lang == "fr" else task.label_en
        if d.retained is None:
            retained, tier_cell = none[lang], _DASH
        else:
            retained = f"`{d.retained}`"
            tier_cell = f"{d.tier_gb} {unit}" if d.tier_gb else above[lang]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{d.task} {label}",
                    retained,
                    _num(d.total_gb, lang),
                    tier_cell,
                    ", ".join(f"`{m}`" for m in d.tried) or _DASH,
                    f"`{d.strongest}`" if d.strongest else _DASH,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def overall_line(decisions: Sequence[Decision], lang: Lang) -> str:
    size = overall_tier(decisions)
    if lang == "fr":
        if size is None:
            return (
                "**Palier d'ensemble** : aucun palier ne couvre toutes les tâches "
                "conversationnelles."
            )
        return (
            f"**Palier d'ensemble** : {size} Go de mémoire graphique (modèle retenu le plus "
            "lourd, tâches T1 à T5b)."
        )
    if size is None:
        return "**Overall tier**: no tier covers every chat task."
    return f"**Overall tier**: {size} GB of GPU memory (heaviest retained model, tasks T1 to T5b)."


def matrix_markdown(
    screened: Mapping[str, Sequence[Run]],
    confirmations: Mapping[str, Sequence[Run]],
    decisions: Sequence[Decision],
    note: Mapping[str, str],
    lang: Lang,
) -> str:
    title = {
        "fr": "# Matrice des tâches et des modèles (module 4)",
        "en": "# Task and model matrix (module 4)",
    }[lang]
    parts = [title, ""]
    intro = {
        "fr": f"Règle préenregistrée ([ADR 0024]({ADR_0024})) : le modèle admissible le plus "
        "léger, confirmé à k = 3.",
        "en": f"Pre-registered rule ([ADR 0024]({ADR_0024}), in French): the lightest admissible "
        "model, confirmed at k = 3.",
    }[lang]
    parts += [intro, "", decisions_table(decisions, lang), "", overall_line(decisions, lang), ""]
    for task_id, runs in screened.items():
        task = TASKS[task_id]
        label = task.label_fr if lang == "fr" else task.label_en
        parts += [f"## {task_id} {label}", ""]
        parts += [{"fr": "**Criblage (k = 1)**", "en": "**Screening (k = 1)**"}[lang], ""]
        parts += [runs_table(task, runs, lang), ""]
        if confirmations.get(task_id):
            parts += [
                {"fr": "**Confirmation (k = 3)**", "en": "**Confirmation (k = 3)**"}[lang],
                "",
            ]
            parts += [runs_table(task, confirmations[task_id], lang), ""]
    parts += [{"fr": "## Conditions", "en": "## Conditions"}[lang], ""]
    parts += [
        f"- `{key}` : {value}" if lang == "fr" else f"- `{key}`: {value}"
        for key, value in note.items()
    ]
    return "\n".join(parts).rstrip() + "\n"


def reference_section(data: Mapping[str, Any], lang: Lang) -> str:
    """The hosted reference of ADR 0023: informative, outside the recommendation rule."""
    counts = data.get("counts") or {}
    controls = data.get("controls") or {}
    metrics = data.get("metrics") or {}
    if lang == "fr":
        lines = [
            "## Référence hébergée (informative)",
            "",
            f"`{data.get('reference')}` ({data.get('model')}, interface "
            f"{data.get('cli_version')}) : {data.get('n_sent_items')} items retenus sur "
            f"{data.get('n_eligible')} admissibles, {data.get('calls')} invites envoyées ; "
            f"{data.get('spec_skipped')} écartés pour "
            f"un extrait `SPEC-*`, {data.get('pi_blocked')} bloqués par le détecteur. "
            f"Hors de la règle de recommandation ([ADR 0023]({ADR_0023})).",
            "",
            "| Métrique | Valeur |",
            "|---|---|",
        ]
    else:
        lines = [
            "## Hosted reference (informative)",
            "",
            f"`{data.get('reference')}` ({data.get('model')}, command line "
            f"{data.get('cli_version')}): {data.get('n_sent_items')} items kept out of "
            f"{data.get('n_eligible')} eligible, {data.get('calls')} prompts sent; "
            f"{data.get('spec_skipped')} left out for a "
            f"`SPEC-*` extract, {data.get('pi_blocked')} blocked by the detector. Outside the "
            f"recommendation rule ([ADR 0023]({ADR_0023}), in French).",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
    for key, value in metrics.items():
        shown = _seconds(value, lang) if key.startswith("latency") else _num(value, lang, 3)
        lines.append(f"| `{key}` | {shown} |")
    for key, value in counts.items():
        control = controls.get(key, {})
        planned = control.get("failed_as_planned")
        mark = "✅" if planned else "❌"
        word = {"fr": "contrôle négatif", "en": "negative control"}[lang]
        lines.append(f"| `{key}` | {value} ({word} : {control.get('count')} {mark}) |")
    return "\n".join(lines) + "\n"


def publishable_files(out: Path) -> list[Path]:
    """Files of ``out`` that could be published: the git-ignored folders are left out."""
    files = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(out).parts
        if any(part in UNPUBLISHED or part.endswith(".quarantine") for part in parts[:-1]):
            continue
        files.append(path)
    return files
