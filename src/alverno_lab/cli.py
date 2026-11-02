"""Command-line interface: ``alverno index | ask | eval | bench | hr | privacy | access``."""

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

from alverno_lab.agents.hr.graph import HrAnswer, write_hr_trace_jsonl
from alverno_lab.agents.hr.prompts import notice
from alverno_lab.agents.quality.graph import (
    LegacyTracingError,
    QualityAgent,
    write_trace_jsonl,
)
from alverno_lab.config import RemoteOllamaError, Settings
from alverno_lab.corpus import CorpusError
from alverno_lab.evals.canaries import UNTYPED_LABEL, LoadedCanary, load_canaries
from alverno_lab.evals.dataset import DatasetError, load_dataset
from alverno_lab.evals.hr import (
    HrBenchRow,
    bench_blockers,
    gold_canaries,
    hr_bench_markdown,
    hr_bench_table,
    load_hr_gold,
    run_hr_eval,
    unmet_preconditions,
)
from alverno_lab.evals.pii_bench import (
    file_sha256,
    load_detector_set,
    run_detector_bench,
    write_detector_report,
)
from alverno_lab.evals.runner import EvalReport, run_eval, write_report
from alverno_lab.evals.thresholds import GateReport, ThresholdError, check_thresholds
from alverno_lab.hr_pipeline import (
    build_hr_agent,
    build_hr_index,
    build_hr_retriever,
    check_hr_corpus,
    check_hr_local,
    hr_settings,
)
from alverno_lab.index import IndexStoreError
from alverno_lab.ollama import OllamaError
from alverno_lab.pipeline import Backend, build_agent, build_index, build_retriever
from alverno_lab.privacy import AccessLog, AccessLogError, PrivacyConfigError, scan_paths
from alverno_lab.privacy.ops_access_log import OpsAccessLogRecord
from alverno_lab.retrieval import HybridRetriever, RerankerUnavailableError

# Tracebacks never show local variables: they could hold a question or a detected value
# (Law 25). Typer's default is already off; it is pinned on every app.
app = typer.Typer(
    help="Alverno agent lab: local-first Quality agent (hybrid RAG + LangGraph + evals).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
privacy_app = typer.Typer(
    help="Law 25 layer: PII detector bench and canary scan of artifacts.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
app.add_typer(privacy_app, name="privacy")
access_app = typer.Typer(
    help="Access logs (Law 25): retention purge of the HR, service and maintenance files.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
app.add_typer(access_app, name="access")
hr_app = typer.Typer(
    help="HR assistant (module 2): answers on the general HR policies, never on a person.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
app.add_typer(hr_app, name="hr")


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
    PrivacyConfigError,
    AccessLogError,
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


# Errors whose message never holds a question, an answer or a model output: their text
# is printed. Any other error of an HR command (OllamaError: its message may quote a
# server response) is reported by its class name only.
_HR_SAFE_MESSAGES = (
    IndexStoreError,
    CorpusError,
    DatasetError,
    ThresholdError,
    RerankerUnavailableError,
    MarkerError,
    LegacyTracingError,
    RemoteOllamaError,
    PrivacyConfigError,
    AccessLogError,
)


def _hr_guard[R](action: Callable[[], R]) -> R:
    """:func:`_guard` for HR commands: exit 2, and no message that could quote a question."""
    try:
        return action()
    except USER_ERRORS as exc:
        detail = f": {exc}" if isinstance(exc, _HR_SAFE_MESSAGES) else ""
        typer.secho(f"Error: {type(exc).__name__}{detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None


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


def _gate(report: GateReport, thresholds: Path) -> int:
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


# The repository of this package (src layout); the working directory is checked too.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _scan_refusal(paths: Sequence[Path]) -> str | None:
    """Why ``privacy scan`` must not read ``paths``, if it must not.

    A scan reads every file: a path that is, contains or lies inside a repository's
    ``data/`` (where the unseen holdout lives, ADR 0010) or that contains its ``.env`` is
    refused before anything is read. The repository is the one of this package and the
    working directory, when they hold a ``data/`` folder or a ``.env`` file.
    """
    roots = dict.fromkeys(r.resolve() for r in (_PACKAGE_ROOT, Path.cwd()))
    for path in paths:
        target = path.resolve()
        for root in roots:
            data, env = root / "data", root / ".env"
            if data.is_dir() and (
                target == data or target in data.parents or data in target.parents
            ):
                return f"{_shown_path(path)} is, contains or lies inside {_shown_path(data)}"
            if env.exists() and (target == env or target in env.parents):
                return f"{_shown_path(path)} contains {_shown_path(env)}"
    return None


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


def _gate_passed(report: GateReport, thresholds: Path) -> bool | None:
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


@hr_app.command("index")
def hr_index(fake: FakeOpt = False, corpus: CorpusOpt = None) -> None:
    """Check the HR corpus (privacy rules), then build its vector index.

    Real mode saves ALVERNO_HR_INDEX_PATH; the Quality index is never touched.
    """
    settings = _settings(hr_corpus_dir=corpus)
    built = _hr_guard(lambda: build_hr_index(settings, fake=fake))
    where = "in memory (fake mode, not saved)" if fake else str(settings.hr_index_path)
    typer.echo(
        f"Indexed {built.manifest.n_chunks} HR chunks with {built.manifest.embed_model} "
        f"(dim {built.manifest.dim}) -> {where}"
    )


def _read_question() -> str:
    """The question from stdin: one line on a terminal, the whole input from a pipe."""
    stream = sys.stdin
    if isinstance(stream, io.TextIOWrapper):
        # Pipes on Windows default to the ANSI code page; a bad byte never raises (its
        # error message would quote the input).
        stream.reconfigure(encoding="utf-8", errors="replace")
    if stream.isatty():
        typer.echo("Question : ", err=True, nl=False)
        return stream.readline()
    return stream.read()


def _print_hr_answer(result: HrAnswer) -> None:
    typer.echo(result.answer)
    typer.echo("")
    if result.refused:
        typer.echo(f"[refused: {result.refusal_reason}]")
    else:
        typer.echo(f"Sources: {', '.join(result.citations)}")
        typer.echo(f"Confidence: {result.confidence:.2f}")
    if result.n_dropped_citations:
        typer.echo(f"Removed invalid citations: {result.n_dropped_citations}")
    typer.echo(f"Latency: {result.latency_ms:.0f} ms")


@hr_app.command("ask")
def hr_ask(
    # A list, so that a question typed without quotes is not rejected by Click, whose
    # error message would print its extra words on stderr.
    question: Annotated[
        list[str] | None,
        typer.Argument(
            help="Question in French; omit it to type it on stdin (recommended).",
            show_default=False,
        ),
    ] = None,
    fake: FakeOpt = False,
    model: Annotated[str | None, typer.Option("--model", help="Chat model.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full JSON answer.")] = False,
    trace: Annotated[
        Path | None, typer.Option("--trace", help="Append the scrubbed trace to JSONL.")
    ] = None,
) -> None:
    """Ask the HR assistant one question, read on stdin when QUESTION is omitted.

    The transparency notice is printed on stderr before the question is read. Exit 0
    when answered or refused; exit 2 on a user error, an unavailable access log, a legacy
    tracing variable, a remote Ollama opt-in or a cloud model.
    """
    settings = _settings()
    _hr_guard(lambda: check_hr_local(settings, model=model, fake=fake))
    agent = _hr_guard(lambda: build_hr_agent(settings, fake=fake, model=model))
    if question:
        typer.secho(
            "Warning: a question given as an argument stays in the shell history and is "
            "read before the notice; omit it to type the question on stdin.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(notice(settings.access_log_retention_days), err=True)
    text = " ".join(question) if question else _read_question()
    result = _hr_guard(lambda: agent.ask(text))
    if trace is not None:
        write_hr_trace_jsonl(result, trace)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        _print_hr_answer(result)
    if result.refusal_reason == "audit_unavailable":
        typer.secho(
            "Error: the access log is unavailable (write or retention purge failed);"
            " no answer was served.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)


def _eval_mode(mode: str) -> Literal["retrieval", "full"]:
    mode_value = mode.lower()
    if mode_value not in ("retrieval", "full"):
        raise typer.BadParameter("mode must be 'retrieval' or 'full'", param_hint="--mode")
    return "full" if mode_value == "full" else "retrieval"


def _hr_access_log(settings: Settings, run_dir: Path) -> AccessLog:
    """The access log of an evaluation run: ``<run_dir>/access``, with the configured retention."""
    return AccessLog(run_dir / "access", retention_days=settings.access_log_retention_days)


@hr_app.command("eval")
def hr_eval(
    mode: Annotated[
        str, typer.Option("--mode", help="retrieval or full.", case_sensitive=False)
    ] = "retrieval",
    fake: FakeOpt = False,
    dataset: Annotated[Path | None, typer.Option("--dataset", help="HR gold set JSONL.")] = None,
    out: OutOpt = None,
    model: Annotated[str | None, typer.Option("--model", help="Chat model (full).")] = None,
    thresholds: ThresholdsOpt = None,
    gate: Annotated[bool, typer.Option("--gate/--no-gate", help="Apply release gate.")] = True,
    limit: LimitOpt = None,
) -> None:
    """Run the HR gold set (profile hr-<mode>-<backend>) and write its reports.

    Writes ``<out>/eval-hr-<mode>-<backend>.{json,md}``, the run's access log in
    ``<out>/access/`` (full mode only: retrieval mode serves nothing) and its scrubbed
    traces in ``<out>/traces/``. Exit 1 when the gate fails, 2 on a user error
    (including an unwritable access log).
    """
    eval_mode = _eval_mode(mode)
    settings = _settings(hr_eval_path=dataset, reports_dir=out, thresholds_path=thresholds)
    _hr_guard(lambda: check_hr_local(settings, model=model, fake=fake))
    items = _hr_guard(lambda: load_hr_gold(settings.hr_eval_path))[:limit]
    retriever, backend = _hr_guard(lambda: build_hr_retriever(settings, fake=fake))
    log = _hr_guard(lambda: _hr_access_log(settings, settings.reports_dir))
    agent = _hr_guard(
        lambda: build_hr_agent(
            settings, fake=fake, model=model, retriever=retriever, access_log=log
        )
    )
    report = _hr_guard(
        lambda: run_hr_eval(
            agent,
            items,
            eval_mode,
            backend=backend.name,
            run_dir=settings.reports_dir,
            config=_config(hr_settings(settings), backend, None),
            dataset=settings.hr_eval_path,
            thresholds=settings.thresholds_path,
        )
    )
    typer.echo(f"Evaluated {report.n_items} HR items ({report.mode}, {report.backend}).")
    for key, value in report.metrics.items():
        typer.echo(f"  {key:26} {value}")
    stem = settings.reports_dir / f"eval-{report.profile}"
    typer.echo(f"Reports: {stem}.json, {stem}.md")
    if eval_mode == "full":
        typer.echo(f"Access log: {log.directory}")
    else:
        typer.echo("Access log: none (retrieval mode serves nothing, ADR 0011)")
    if gate:
        code = _hr_guard(lambda: _gate(report, settings.thresholds_path))
        if code:
            raise typer.Exit(code=code)


HR_MARKERS = ("RESULTS-HR:FR", "RESULTS-HR:EN")


@hr_app.command("bench")
def hr_bench(
    models: Annotated[str, typer.Option("--models", help="Comma-separated chat models.")],
    fake: FakeOpt = False,
    dataset: Annotated[Path | None, typer.Option("--dataset", help="HR gold set JSONL.")] = None,
    out: OutOpt = None,
    thresholds: ThresholdsOpt = None,
    limit: LimitOpt = None,
    readme: Annotated[
        Path | None,
        typer.Option(
            "--readme",
            exists=True,
            dir_okay=False,
            help="Also update the RESULTS-HR:FR and RESULTS-HR:EN blocks.",
        ),
    ] = None,
) -> None:
    """Run the full HR evaluation for each chat model and write comparison tables.

    The README is rewritten (RESULTS-HR blocks only) when every model has error_rate 0
    and artifact_canary_hits 0 and meets every gate precondition (n_privacy_traps,
    n_pairs, n_canary_items, n_canary_types), the dataset has canaries to look for and
    the run folder holds none; otherwise exit 1 and the README is left as is. A run folder with a
    canary hit is renamed ``<folder>.quarantine`` (git-ignored).
    """
    names = list(dict.fromkeys(m.strip() for m in models.split(",") if m.strip()))
    if not names:
        raise typer.BadParameter("give one or more models", param_hint="--models")
    settings = _settings(hr_eval_path=dataset, reports_dir=out, thresholds_path=thresholds)
    for name in names:
        _hr_guard(partial(check_hr_local, settings, model=name, fake=fake))
    items = _hr_guard(lambda: load_hr_gold(settings.hr_eval_path))[:limit]
    retriever, backend = _hr_guard(lambda: build_hr_retriever(settings, fake=fake))
    if readme is not None:
        text = readme.read_text(encoding="utf-8")
        for marker in HR_MARKERS:
            _hr_guard(partial(replace_marked_block, text, marker, ""))
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    run_dir = settings.reports_dir / f"bench-hr-{backend.name}-{stamp}"
    log = _hr_guard(lambda: _hr_access_log(settings, run_dir))
    gate_file = settings.thresholds_path
    rows: list[HrBenchRow] = []
    stems = {"summary", "summary.fr"}
    for name in names:
        typer.echo(f"Benchmarking {name} on {len(items)} HR questions...")
        agent = _hr_guard(
            partial(
                build_hr_agent, settings, fake=fake, model=name, retriever=retriever, access_log=log
            )
        )
        stem = re.sub(r"[^\w.-]+", "_", name)
        while stem.casefold() in stems:
            stem += "_"
        stems.add(stem.casefold())
        report = _hr_guard(
            partial(
                run_hr_eval,
                agent,
                items,
                "full",
                backend=backend.name,
                run_dir=run_dir,
                label=name,
                config=_config(hr_settings(settings), backend, None),
                dataset=settings.hr_eval_path,
                thresholds=gate_file,
                stem=stem,
            )
        )
        tokens = agent.chat.stats.prompt_tokens + agent.chat.stats.completion_tokens
        rows.append(HrBenchRow(name, report, tokens, _gate_passed(report, gate_file)))
    shown = _shown_path(gate_file)
    md_path = run_dir / "summary.md"
    _write_lf(md_path, hr_bench_markdown(rows, backend, thresholds=shown))
    _write_lf(run_dir / "summary.fr.md", hr_bench_markdown(rows, backend, "fr", thresholds=shown))
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
    # Last check on everything the run wrote, summaries included.
    canaries = gold_canaries(items)
    hits = scan_paths([run_dir], canaries)
    if hits:
        # The folder is not git-ignored: move it aside so that `git add reports/` cannot
        # publish it (`reports/**/*.quarantine/` is ignored).
        quarantine = run_dir.with_name(f"{run_dir.name}.quarantine")
        run_dir.rename(quarantine)
        hits = [(quarantine / path.relative_to(run_dir), pi_type) for path, pi_type in hits]
        run_dir, md_path = quarantine, quarantine / md_path.name
    for path, pi_type in hits:
        typer.echo(f"{_shown_path(path)}: {pi_type.value}")
    blocked = bench_blockers(rows)
    # A run with no canary to look for, or short of any gate precondition (traps, pairs,
    # canary items and types), would publish a privacy claim that checked too little.
    unmet = unmet_preconditions(rows, gate_file)
    unchecked = not canaries or bool(unmet)
    if readme is not None and not blocked and not hits and not unchecked:
        text = readme.read_text(encoding="utf-8")
        for marker, table in (
            ("RESULTS-HR:FR", hr_bench_table(rows, backend, "fr", thresholds=shown)),
            ("RESULTS-HR:EN", hr_bench_table(rows, backend, "en", thresholds=shown)),
        ):
            # Checked before the run, but the README may have been edited since.
            text = _hr_guard(partial(replace_marked_block, text, marker, table))
        _write_lf(readme, text)
        typer.echo(f"Updated {readme}")
    typer.echo(md_path.read_text(encoding="utf-8"))
    typer.echo(f"Reports: {run_dir}")
    if blocked or hits:
        untouched = f"; {readme} not updated" if readme is not None else ""
        why = (
            f"transport errors or persisted canaries for {', '.join(blocked)}"
            if blocked
            else f"{len(hits)} canary hit(s) in the run folder"
        )
        moved = f"; reports moved to {_shown_path(run_dir)}" if hits else ""
        typer.secho(
            f"Error: {why}: the measurement is invalid{untouched}{moved}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if readme is not None and unchecked:
        why = "no canary to check" if not canaries else "gate preconditions not met"
        for line in unmet:
            typer.echo(f"  FAIL {line}", err=True)
        typer.secho(f"Error: {why}: {readme} not updated.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


@privacy_app.command("eval")
def privacy_eval(
    dataset: Annotated[Path | None, typer.Option("--dataset", help="Detector bench JSONL.")] = None,
    out: OutOpt = None,
    thresholds: ThresholdsOpt = None,
    gate: Annotated[bool, typer.Option("--gate/--no-gate", help="Apply release gate.")] = True,
) -> None:
    """Run the PII detector bench (profile privacy-detector) and write its reports.

    The HR corpus privacy check runs first and fails closed (exit 2).
    """
    settings = _settings(pii_eval_path=dataset, reports_dir=out, thresholds_path=thresholds)
    _, ctx = _guard(lambda: check_hr_corpus(settings))
    items = _guard(lambda: load_detector_set(settings.pii_eval_path))
    report = run_detector_bench(
        items,
        ctx,
        dataset=_shown_path(settings.pii_eval_path),
        sha256=file_sha256(settings.pii_eval_path),
    )
    json_path, md_path = write_detector_report(report, settings.reports_dir)
    typer.echo(f"Evaluated {report.n_items} detector items ({report.n_negatives} negatives).")
    for key, value in report.metrics.items():
        typer.echo(f"  {key:24} {value}")
    typer.echo(f"Reports: {json_path}, {md_path}")
    if gate:
        code = _guard(lambda: _gate(report, settings.thresholds_path))
        if code:
            raise typer.Exit(code=code)


@privacy_app.command("scan")
def privacy_scan(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to scan.", exists=True)],
    dataset: Annotated[
        list[Path] | None,
        typer.Option(
            "--dataset",
            help="Set whose canaries are searched for: jeu de référence ou réservé (RH, "
            "service ou entretien); repeat it to combine sets (default: ALVERNO_HR_EVAL_PATH).",
        ),
    ] = None,
) -> None:
    """Search files for the canaries of one or more sets (HR, service, maintenance); exit 1
    on any hit.

    Canaries are read by ``load_canaries`` (only the ``id`` and ``canaries`` keys). Each
    hit is printed as ``path: label``, the label being the canary type or ``texte`` for an
    untyped value; the value itself is never printed. A path that is or contains the
    repository's ``data/`` folder or ``.env`` is refused (exit 2) before anything is read;
    ``.env`` files elsewhere are skipped.
    """
    refusal = _scan_refusal(paths)
    if refusal is not None:
        typer.secho(
            f"Error: {refusal}; scan only report folders (data/ holds the unseen holdout).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    settings = _settings()
    sources = list(dict.fromkeys(dataset or [settings.hr_eval_path]))
    canaries = list(
        dict.fromkeys(c for source in sources for c in _guard(partial(load_canaries, source)))
    )
    if not canaries:
        # A scan with nothing to look for would pass vacuously (fail closed instead).
        typer.secho(
            "Error: the dataset has no canary to search for.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)
    hits = _labelled_hits(paths, canaries)
    for path, label in hits:
        typer.echo(f"{_shown_path(path)}: {label}")
    if hits:
        typer.secho(
            f"Error: {len(hits)} canary hit(s); do not publish these files.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"No canary found ({len(canaries)} canaries).")


def _labelled_hits(
    paths: Sequence[Path], canaries: Sequence[LoadedCanary]
) -> list[tuple[Path, str]]:
    """Every (file, label) hit: typed canaries first, then untyped values.

    Typed canaries are scanned together, exactly as before module 3: an HR set gives the
    same lines, in the same order (files as walked, types sorted within a file), and the
    same count. Untyped values, carried as ``adresse`` canaries, are scanned apart and
    reported as ``texte`` after them, never under the ``adresse`` type.
    """
    typed = [c.canary for c in canaries if c.label != UNTYPED_LABEL]
    untyped = [c.canary for c in canaries if c.label == UNTYPED_LABEL]
    hits = [(path, pi_type.value) for path, pi_type in scan_paths(paths, typed)]
    hits.extend((path, UNTYPED_LABEL) for path, _ in scan_paths(paths, untyped))
    return hits


@access_app.command("purge")
def access_purge(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List the expired day files without deleting.")
    ] = False,
) -> None:
    """Delete access log day files older than the retention period (90 days at most).

    Three logs share the folder, each purged by its own instance: HR (``hr-``), customer
    service (``service-``) and maintenance (``maint-``), with the same retention. The HR
    line keeps its format; the other prefixes add one line each.
    """
    settings = _settings()
    directory, days = settings.access_log_dir, settings.access_log_retention_days
    hr_log = _guard(lambda: AccessLog(directory, retention_days=days))
    service_log = _guard(
        lambda: AccessLog(
            directory, prefix="service", record_type=OpsAccessLogRecord, retention_days=days
        )
    )
    maint_log = _guard(
        lambda: AccessLog(
            directory, prefix="maint", record_type=OpsAccessLogRecord, retention_days=days
        )
    )
    verb = "Would remove" if dry_run else "Removed"
    shown = _shown_path(directory)
    for log, kind in ((hr_log, ""), (service_log, "service "), (maint_log, "maint ")):
        paths = _guard(partial(log.purge, dry_run=dry_run))
        for path in paths:
            typer.echo(f"{verb} {_shown_path(path)}")
        typer.echo(f"{verb} {len(paths)} {kind}day file(s) older than {days} days in {shown}.")


if __name__ == "__main__":  # pragma: no cover
    app()
