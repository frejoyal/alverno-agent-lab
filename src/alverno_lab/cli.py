"""Command-line interface: ``alverno index | ask | eval | bench``."""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import typer
from pydantic import ValidationError

from alverno_lab.agents.quality.graph import (
    LegacyTracingError,
    QualityAgent,
    write_trace_jsonl,
)
from alverno_lab.config import RemoteOllamaError, Settings
from alverno_lab.corpus import CorpusError
from alverno_lab.evals.dataset import DatasetError, load_dataset
from alverno_lab.evals.runner import EvalReport, run_eval, write_report
from alverno_lab.evals.thresholds import ThresholdError, check_thresholds
from alverno_lab.index import IndexStoreError
from alverno_lab.ollama import OllamaError
from alverno_lab.pipeline import Backend, build_agent, build_index, build_retriever
from alverno_lab.retrieval import HybridRetriever, RerankerUnavailableError

app = typer.Typer(
    help="Alverno agent lab: local-first Quality agent (hybrid RAG + LangGraph + evals).",
    no_args_is_help=True,
    add_completion=False,
)


class MarkerError(ValueError):
    """A results marker of the ``bench --readme`` file is missing, repeated or misplaced."""


USER_ERRORS = (
    OllamaError,
    IndexStoreError,
    CorpusError,
    DatasetError,
    ThresholdError,
    RerankerUnavailableError,
    MarkerError,
    LegacyTracingError,
    RemoteOllamaError,
)


@app.callback()
def _main() -> None:
    """Force UTF-8 output so French answers render correctly on Windows consoles and pipes."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


FakeOpt = Annotated[
    bool, typer.Option("--fake", help="Hash embedder + fake LLM: offline, deterministic.")
]
CorpusOpt = Annotated[Path | None, typer.Option("--corpus", help="Corpus directory.")]
DatasetOpt = Annotated[Path | None, typer.Option("--dataset", help="Gold set JSONL.")]
OutOpt = Annotated[Path | None, typer.Option("--out", help="Report directory.")]
LimitOpt = Annotated[int | None, typer.Option("--limit", min=1, help="Evaluate first N items.")]
ThresholdsOpt = Annotated[Path | None, typer.Option("--thresholds", help="Thresholds YAML.")]


# Overrides are heterogeneous Settings field values (paths, strings).
def _settings(**overrides: Any) -> Settings:
    """Settings from env/.env, with CLI overrides (None means 'not given')."""
    try:
        return Settings(**{k: v for k, v in overrides.items() if v is not None})
    except ValidationError as exc:
        typer.secho(f"Error: invalid configuration: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _guard[R](action: Callable[[], R]) -> R:
    """Run ``action``; turn expected failures into a clear message and exit code 2."""
    try:
        return action()
    except USER_ERRORS as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _config(settings: Settings, backend: Backend, chat_model: str | None) -> dict[str, str]:
    config = {
        "backend": backend.name,
        "embed_model": backend.embed_model,
        "reranker": backend.reranker,
        "gate_signal": backend.gate_signal,
        "no_match_threshold": str(backend.no_match_threshold),
        "top_k": str(settings.top_k),
        "final_k": str(settings.final_k),
        "rrf_k": str(settings.rrf_k),
        "corpus_dir": settings.corpus_dir.as_posix(),
    }
    if chat_model:
        config["chat_model"] = chat_model
    return config


@app.command()
def index(fake: FakeOpt = False, corpus: CorpusOpt = None) -> None:
    """Build the vector index (saved to ALVERNO_INDEX_PATH in real mode).

    BM25 is not saved: ask, eval and bench rebuild it in memory.
    """
    settings = _settings(corpus_dir=corpus)
    built = _guard(lambda: build_index(settings, fake=fake))
    where = "in memory (fake mode, not saved)" if fake else str(settings.index_path)
    typer.echo(
        f"Indexed {built.manifest.n_chunks} chunks with {built.manifest.embed_model} "
        f"(dim {built.manifest.dim}) -> {where}"
    )


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question in French.")],
    fake: FakeOpt = False,
    corpus: CorpusOpt = None,
    model: Annotated[str | None, typer.Option("--model", help="Chat model.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full JSON answer.")] = False,
    trace: Annotated[Path | None, typer.Option("--trace", help="Append trace to JSONL.")] = None,
) -> None:
    """Ask the Quality agent one question."""
    settings = _settings(corpus_dir=corpus)
    agent = _guard(lambda: build_agent(settings, fake=fake, model=model))
    result = _guard(lambda: agent.ask(question))
    if trace is not None:
        write_trace_jsonl(
            result.trace, trace, question=question, refusal_reason=result.refusal_reason
        )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        return
    typer.echo(result.answer)
    typer.echo("")
    if result.refused:
        typer.echo(f"[refused: {result.refusal_reason}]")
    else:
        typer.echo(f"Sources: {', '.join(result.citations)}")
        typer.echo(f"Confidence: {result.confidence:.2f}")
    if result.dropped_citations:
        typer.echo(f"Removed invalid citations: {', '.join(result.dropped_citations)}")
    typer.echo(f"Latency: {result.latency_ms:.0f} ms")


def _gate(report: EvalReport, thresholds: Path) -> int:
    result = check_thresholds(report, thresholds)
    typer.echo(f"Release gate [{result.profile}]: {'PASS' if result.passed else 'FAIL'}")
    for line in result.checks:
        mark = "FAIL" if line in result.failures else "ok"
        typer.echo(f"  {mark:4} {line}")
    for line in result.failures:
        if line not in result.checks:
            typer.echo(f"  FAIL {line}")
    return result.exit_code


@app.command("eval")
def eval_command(
    mode: Annotated[
        str, typer.Option("--mode", help="retrieval or full.", case_sensitive=False)
    ] = "retrieval",
    fake: FakeOpt = False,
    corpus: CorpusOpt = None,
    dataset: DatasetOpt = None,
    out: OutOpt = None,
    model: Annotated[str | None, typer.Option("--model", help="Chat model (full).")] = None,
    thresholds: ThresholdsOpt = None,
    gate: Annotated[bool, typer.Option("--gate/--no-gate", help="Apply release gate.")] = True,
    limit: LimitOpt = None,
) -> None:
    """Run the gold set and write JSON + Markdown reports; fail on threshold regression."""
    mode_value = mode.lower()
    if mode_value not in ("retrieval", "full"):
        raise typer.BadParameter("mode must be 'retrieval' or 'full'", param_hint="--mode")
    eval_mode: Literal["retrieval", "full"] = "full" if mode_value == "full" else "retrieval"
    settings = _settings(
        corpus_dir=corpus, eval_path=dataset, reports_dir=out, thresholds_path=thresholds
    )
    items = _guard(lambda: load_dataset(settings.eval_path))[:limit]
    retriever, backend = _guard(lambda: build_retriever(settings, fake=fake))
    chat_model: str | None = None
    target: QualityAgent | HybridRetriever = retriever
    if eval_mode == "full":
        agent = _guard(lambda: build_agent(settings, fake=fake, model=model, retriever=retriever))
        chat_model = agent.chat.model_name
        target = agent
    report = _guard(
        lambda: run_eval(
            target,
            items,
            eval_mode,
            backend=backend.name,
            config=_config(settings, backend, chat_model),
        )
    )
    json_path, md_path = write_report(report, settings.reports_dir)
    typer.echo(f"Evaluated {report.n_items} items ({report.mode}, {report.backend}).")
    for key, value in report.metrics.items():
        typer.echo(f"  {key:24} {value}")
    typer.echo(f"Reports: {json_path}, {md_path}")
    if gate:
        code = _guard(lambda: _gate(report, settings.thresholds_path))
        if code:
            raise typer.Exit(code=code)


BenchLang = Literal["en", "fr"]

# Model-dependent columns: (metric, English label, French label, unit).
BENCH_COLUMNS: list[tuple[str, str, str, str]] = [
    ("trap_refusal_accuracy", "trap refusal", "pièges refusés", "rate"),
    ("false_answer_rate", "false answers", "fausses réponses", "rate"),
    ("over_refusal_rate", "over-refusal", "refus injustifiés", "rate"),
    ("citation_validity_rate", "valid citations", "citations valides", "rate"),
    ("key_fact_recall", "key facts", "faits clés", "rate"),
    ("latency_p50_ms", "p50", "p50", "seconds"),
    ("latency_p95_ms", "p95", "p95", "seconds"),
    ("error_rate", "errors", "erreurs", "rate"),
]
# Retrieval runs before the LLM, so these are the same for every model of a bench.
BENCH_RETRIEVAL: list[tuple[str, str, str]] = [
    ("hit_at_1", "hit@1", "hit@1"),
    ("mrr", "MRR", "MRR"),
    ("context_recall", "context recall", "rappel du contexte"),
]
GATE_MARKS = {True: "✅", False: "❌", None: "—"}


class BenchRow(NamedTuple):
    """One benchmarked model."""

    model: str
    report: EvalReport
    tokens: int
    gate_passed: bool | None


def _number(value: float, lang: BenchLang, digits: int = 3) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "fr" else text


def _cell(value: float, unit: str, lang: BenchLang) -> str:
    if unit == "seconds":
        return f"{_number(value / 1000, lang, 1)} s"
    return _number(value, lang)


def _thousands(value: int, lang: BenchLang) -> str:
    return f"{value:,}".replace(",", "\u00a0" if lang == "fr" else ",")


def _shown_path(path: Path) -> str:
    """``path`` relative to the working directory, POSIX style, when it lies below it."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def bench_table(
    rows: Sequence[BenchRow], backend: Backend, lang: BenchLang = "en", *, thresholds: Path
) -> str:
    """Model comparison table and its measurement note, ready for the README markers."""
    fr = lang == "fr"
    labels = [fr_label if fr else en_label for _, en_label, fr_label, _ in BENCH_COLUMNS]
    first = ["Modèle", "porte", *labels, "jetons"] if fr else ["Model", "gate", *labels, "tokens"]
    lines = ["| " + " | ".join(first) + " |", "|---" * len(first) + "|"]
    for row in rows:
        cells = [
            _cell(row.report.metrics.get(key, 0.0), unit, lang) for key, _, _, unit in BENCH_COLUMNS
        ]
        mark = GATE_MARKS[row.gate_passed]
        tokens = _thousands(row.tokens, lang)
        lines.append(f"| `{row.model}` | {mark} | " + " | ".join(cells) + f" | {tokens} |")
    if not rows:
        return "\n".join(lines) + "\n"
    report = rows[0].report
    retrieval = " · ".join(
        f"{fr_label if fr else en_label} {_number(report.metrics.get(key, 0.0), lang)}"
        for key, en_label, fr_label in BENCH_RETRIEVAL
    )
    no_match = _number(backend.no_match_threshold, lang, 2)
    profile = f"full-{backend.name}"
    gate_file = _shown_path(thresholds)
    # created_at is UTC; show the workstation's date, the one in the run folder name.
    measured = dt.datetime.fromisoformat(report.created_at).astimezone().date().isoformat()
    if fr:
        note = (
            f"Mesures prises le {measured} sur {report.n_items} questions par modèle "
            f"({report.n_answerable} répondables, {report.n_traps} pièges). Recherche, "
            f"identique pour tous les modèles : {retrieval}. Plongements "
            f"`{backend.embed_model}`, reranker `{backend.reranker}`, barrière "
            f"`{backend.gate_signal}` < {no_match}. Porte : profil `{profile}` de "
            f"`{gate_file}`. Généré par `alverno bench` ; ne pas modifier à la main."
        )
    else:
        note = (
            f"Measured on {measured} over {report.n_items} questions per model "
            f"({report.n_answerable} answerable, {report.n_traps} traps). Retrieval, the "
            f"same for every model: {retrieval}. Embeddings `{backend.embed_model}`, "
            f"reranker `{backend.reranker}`, gate `{backend.gate_signal}` < {no_match}. "
            f"Gate: profile `{profile}` of `{gate_file}`. Generated by "
            "`alverno bench`; do not hand-edit."
        )
    return "\n".join([*lines, "", note]) + "\n"


def bench_markdown(
    rows: Sequence[BenchRow], backend: Backend, lang: BenchLang = "en", *, thresholds: Path
) -> str:
    """Standalone bench report: a title plus :func:`bench_table`."""
    title = (
        f"# Banc d'essai — mode complet ({backend.name})"
        if lang == "fr"
        else f"# Benchmark — full mode ({backend.name})"
    )
    return f"{title}\n\n{bench_table(rows, backend, lang, thresholds=thresholds)}"


def replace_marked_block(text: str, name: str, block: str) -> str:
    """Put ``block`` between ``<!-- NAME -->`` and ``<!-- /NAME -->``.

    Markers count only on a line of their own, so a sentence that quotes one is left
    alone. The opening marker must appear exactly once and the closing one at most once:
    a missing closing marker is added after the block, so a README that only has the
    opening marker is updated the same way on the first run and on every later one.
    """
    start, end = f"<!-- {name} -->", f"<!-- /{name} -->"
    lines = text.split("\n")
    bare = [line.strip() for line in lines]
    if bare.count(start) != 1 or bare.count(end) > 1:
        raise MarkerError(
            f"{start} must appear exactly once on a line of its own ({end} at most once)"
        )
    i = bare.index(start)
    j = bare.index(end) if end in bare else i
    if j < i:
        raise MarkerError(f"{end} comes before {start}")
    return "\n".join([*lines[:i], start, "", block.strip(), "", end, *lines[j + 1 :]])


def _gate_passed(report: EvalReport, thresholds: Path) -> bool | None:
    try:
        return check_thresholds(report, thresholds).passed
    except ThresholdError as exc:
        typer.secho(f"Warning: release gate not applied: {exc}", fg=typer.colors.YELLOW, err=True)
        return None


def _write_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform: bench files are published."""
    path.write_text(text, encoding="utf-8", newline="\n")


@app.command()
def bench(
    models: Annotated[str, typer.Option("--models", help="Comma-separated chat models.")],
    fake: FakeOpt = False,
    corpus: CorpusOpt = None,
    dataset: DatasetOpt = None,
    out: OutOpt = None,
    thresholds: ThresholdsOpt = None,
    limit: LimitOpt = None,
    readme: Annotated[
        Path | None,
        typer.Option(
            "--readme",
            exists=True,
            dir_okay=False,
            help="Also update the RESULTS:FR and RESULTS:EN blocks.",
        ),
    ] = None,
) -> None:
    """Run the full evaluation for each chat model and write comparison tables.

    A transport error (error_rate > 0) invalidates the run: exit code 1, README left as is.
    """
    # Each model runs once, even if --models repeats it.
    names = list(dict.fromkeys(m.strip() for m in models.split(",") if m.strip()))
    if not names:
        raise typer.BadParameter("give one or more models", param_hint="--models")
    settings = _settings(
        corpus_dir=corpus, eval_path=dataset, reports_dir=out, thresholds_path=thresholds
    )
    items = _guard(lambda: load_dataset(settings.eval_path))[:limit]
    retriever, backend = _guard(lambda: build_retriever(settings, fake=fake))
    if readme is not None:
        text = readme.read_text(encoding="utf-8")
        for marker in ("RESULTS:FR", "RESULTS:EN"):
            _guard(partial(replace_marked_block, text, marker, ""))
    run_dir = settings.reports_dir / f"bench-{backend.name}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    rows: list[BenchRow] = []
    # Names that differ only outside [\w.-] ("a:b", "a/b") or only by case (one file on
    # Windows and macOS) get distinct file stems, and no model report may overwrite the
    # summary files.
    stems = {"summary", "summary.fr"}
    for name in names:
        typer.echo(f"Benchmarking {name} on {len(items)} questions...")
        agent = _guard(partial(build_agent, settings, fake=fake, model=name, retriever=retriever))
        report = _guard(
            partial(
                run_eval,
                agent,
                items,
                "full",
                backend=backend.name,
                label=name,
                config=_config(settings, backend, name),
            )
        )
        tokens = agent.chat.stats.prompt_tokens + agent.chat.stats.completion_tokens
        rows.append(BenchRow(name, report, tokens, _gate_passed(report, settings.thresholds_path)))
        stem = re.sub(r"[^\w.-]+", "_", name)
        while stem.casefold() in stems:
            stem += "_"
        stems.add(stem.casefold())
        # Written as soon as the model is done, so a later failure loses nothing.
        write_report(report, run_dir, stem=stem)
    run_dir.mkdir(parents=True, exist_ok=True)
    gate_file = settings.thresholds_path
    md_path = run_dir / "summary.md"
    _write_lf(md_path, bench_markdown(rows, backend, thresholds=gate_file))
    _write_lf(run_dir / "summary.fr.md", bench_markdown(rows, backend, "fr", thresholds=gate_file))
    payload = [
        {
            "model": r.model,
            "tokens": r.tokens,
            "gate_passed": r.gate_passed,
            "report": r.report.model_dump(),
        }
        for r in rows
    ]
    _write_lf(run_dir / "summary.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    failed = [r.model for r in rows if r.report.metrics.get("error_rate", 0.0) > 0]
    if readme is not None and not failed:
        text = readme.read_text(encoding="utf-8")
        for marker, table in (
            ("RESULTS:FR", bench_table(rows, backend, "fr", thresholds=gate_file)),
            ("RESULTS:EN", bench_table(rows, backend, "en", thresholds=gate_file)),
        ):
            # Checked before the run, but the README may have been edited since.
            text = _guard(partial(replace_marked_block, text, marker, table))
        _write_lf(readme, text)
        typer.echo(f"Updated {readme}")
    typer.echo(md_path.read_text(encoding="utf-8"))
    typer.echo(f"Reports: {run_dir}")
    if failed:
        untouched = f"; {readme} not updated" if readme is not None else ""
        typer.secho(
            f"Error: transport errors for {', '.join(failed)} (error_rate > 0): "
            f"the measurement is invalid{untouched}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
