# AGENTS.md

Instructions for coding agents (Claude Code, Codex, Antigravity, Copilot and others) working in this repository. Humans are welcome to read it too.

## Project in one paragraph

`alverno-agent-lab` is a lab of AI agents for a **fictional** Québec metal fabrication SME, Fabrications Alverno inc. Milestone 1 is a Quality agent: hybrid retrieval (BM25 + vectors + RRF + optional reranker) feeding a LangGraph graph (`retrieve -> gate -> answer -> verify`, with a `refuse` branch), measured by an evaluation suite that acts as a release gate. Milestone 2 adds a shared Law 25 layer (`privacy/`: detection, redaction, classification, refusal policy, access log) and an HR assistant that wraps the unchanged Quality graph in that layer. Everything runs locally on Ollama. See `README.md` and `docs/architecture.md`.

## Commands

```bash
uv sync                                   # install runtime + dev dependencies
uv sync --extra rerank                    # optional cross-encoder reranker
uv run ruff check .                       # lint
uv run ruff format .                      # format (CI runs: ruff format --check .)
uv run mypy src                           # strict typing (mypy --strict)
uv run pytest                             # tests, no network
uv run pytest --cov=alverno_lab           # coverage (fails under 85%, as in CI)

uv run alverno index                      # build and save the vector index (needs Ollama); BM25 is rebuilt in memory
uv run alverno ask "question"             # ask the Quality agent (needs Ollama and `alverno index`)
uv run alverno eval --mode retrieval      # retrieval metrics (needs Ollama and `alverno index`)
uv run alverno eval --mode full           # full agent metrics (needs Ollama and `alverno index`)
uv run alverno eval --mode retrieval --fake   # CI path: hash embedder + fake LLM, no network
uv run alverno bench --models gemma4:e4b,ministral-3:8b,llama3.2:3b --readme README.md

uv run alverno privacy eval               # PII detector bench and gate (offline)
uv run alverno privacy scan reports/ci    # canary scan of generated files (exit 1 on a hit)
uv run alverno hr index                   # build the HR index (needs Ollama)
uv run alverno hr ask                     # ask the HR assistant; reads the question on stdin
uv run alverno hr eval --mode retrieval --fake   # CI path for the HR gate
uv run alverno hr eval --mode full --fake
uv run alverno hr bench --models gemma4:e4b,ministral-3:8b,llama3.2:3b --readme README.md
uv run alverno access purge               # delete access-log files past retention
```

Before declaring a change done, run: `ruff check`, `ruff format --check`, `mypy src`, `pytest --cov=alverno_lab` (fails under 85% coverage, as in CI), `alverno eval --mode retrieval --fake`, `alverno privacy eval`, `alverno hr eval --mode retrieval --fake` and `alverno hr eval --mode full --fake`. All must pass. `tests/test_quality_frozen.py` pins module-1 behaviour: regenerate it (`uv run python -m tests.test_quality_frozen --write`) only when that behaviour is meant to change, and say so in the commit message.

## Layout

- `src/alverno_lab/` — package. `config.py`, `text.py` (tokenizer), `corpus.py`, `ollama.py` (shared HTTP client), `embeddings.py`, `index.py`, `retrieval.py`, `llm.py`, `pipeline.py` (builds real or `--fake` components), `agents/quality/{gate,graph,prompts}.py`, `agents/hr/{graph,prompts}.py`, `privacy/` (shared Law 25 layer; it imports nothing from `agents`, `cli`, `evals` or the pipelines), `hr_pipeline.py`, `evals/{dataset,metrics,runner,thresholds,hr,pii_bench}.py`, `cli.py`.
- `evals/thresholds.yaml` — release-gate bounds: Quality profiles `<mode>-<backend>` (`retrieval-fake`, `full-fake`, `retrieval-ollama`, `full-ollama`), HR profiles `hr-<mode>-<backend>` and `privacy-detector`. `tests/test_thresholds_ratchet.py` pins every bound. Privacy bounds and `hr-full-ollama` were preregistered (`docs/adr/0010`); the service bounds of `hr-retrieval-fake`, `hr-full-fake` and `hr-retrieval-ollama`, and both HR no-match thresholds, were calibrated once (`docs/adr/0011`). Any edit from `hr-retrieval-fake:` to the end of the `privacy-detector` block, comments included, changes `thresholds_sha256` and needs a new dated row in the ADR 0010 ledger.
- `data/corpus/` — fictional quality documents, Markdown with YAML front matter (`doc_id`, `title`, `doc_type`, `department`, `version`, `effective_date`, `sensitivity`).
- `data/eval/quality_gold.jsonl` — 50-question gold set (35 answerable, 15 traps).
- `data/hr/corpus/` — fictional HR policies (2/5, roles only); `data/hr/{org_roles,contacts,legal_floors}.yaml`.
- `data/eval/hr_gold.jsonl` (64-item HR gate set), `data/eval/hr_holdout.jsonl` (one-shot holdout), `data/eval/pii_detect.jsonl` (detector bench), `data/eval/synthetic_identities.yaml` (rules for synthetic values).
- `logs/` — local access log of the HR assistant; ignored by git.
- `tests/` — offline tests only.
- `reports/` — generated reports; do not hand-edit.
- `docs/` — company profile, architecture, Law 25, roadmap, ADRs, `efvp/` (template and HR assessment), `hr-legal-sources.md` (the only source of legal statements in the HR corpus).

## Conventions

- **Language**: code, identifiers, docstrings, commit messages and CLI messages in **English**. Corpus documents, evaluation questions and the agent's user-facing answers in **Québec French**. The README is bilingual, French first.
- **Python** >= 3.12. Full type hints; `mypy --strict` must pass on `src/`. No `Any` without a comment explaining why.
- **Style**: `ruff` for lint and format. Small pure functions where possible (e.g. `rrf_fuse` is pure and unit-tested).
- **Interfaces**: depend on Protocols (`ChatModel`, `Embedder`, `Reranker`), not concrete classes. Every network-backed class has an offline counterpart (`FakeChat`, `HashEmbedder`, `NoopReranker`).
- **Configuration**: only through `Settings` (pydantic-settings, env prefix `ALVERNO_`). No hard-coded URLs or model names outside `config.py` defaults; the constructor defaults of `OllamaChat`, `OllamaEmbedder` and `CrossEncoderReranker` mirror them and must change with them.
- **Default models**: chat `gemma4:e4b` (best measured); also benchmarked `ministral-3:8b`, `llama3.2:3b`; candidate `qwen3.5:9b` (needs an Ollama newer than 0.24); embeddings `qwen3-embedding:0.6b`. Change them only in `config.py` and the docs together.
- **Stable IDs**: chunk IDs are `f"{doc_id}#{n:02d}"`. Changing chunking changes IDs and invalidates the gold set expectations; update both deliberately.
- **Index integrity**: the vector index manifest stores the embedding model and corpus hash; loading a mismatched index must fail loudly, never silently.
- **Evaluations**: any change to retrieval, prompts or the graph must be accompanied by an eval run. Do not lower a threshold to make CI green; fix the regression or justify the change in the PR and an ADR.
- **Results**: never invent benchmark numbers. The README markers `<!-- RESULTS:FR -->` and `<!-- RESULTS:EN -->` are replaced only by output generated by `alverno bench` (`--readme README.md` rewrites both blocks, except when a model has a non-zero `error_rate`). Each marker must stay alone on its line and appear only once. The HR markers `<!-- RESULTS-HR:FR -->` and `<!-- RESULTS-HR:EN -->` follow the same rules and are written only by `alverno hr bench --readme`; each bench leaves the other module's blocks byte-identical. `alverno hr bench --readme` refuses to update the README (exit 1) when any model has a non-zero `error_rate` or `artifact_canary_hits`, when the dataset has no canary, or when any gate precondition (`n_privacy_traps`, `n_pairs`, `n_canary_items`, `n_canary_types`) is not met. A bench run folder with a canary hit is renamed `<folder>.quarantine`; `reports/**/*.quarantine/` is git-ignored and must never be committed or published.
- **Decisions**: significant architectural choices get an ADR in `docs/adr/` (`NNNN-short-title.md`).

## Tests

- Tests never touch the network: no Ollama, no Hugging Face, no external API. Use `HashEmbedder`, `FakeChat`, `NoopReranker`, and `httpx` mock transports for client code.
- Every bug fix comes with a regression test.
- Graph tests cover both branches (`answer` and `refuse`) and the `verify` step removing invalid citations.

## Security rules

- **No secrets** in the repository or in the public CI. A local `.env`, ignored by git, may override `Settings` defaults (`ALVERNO_` prefix). Never commit `.env`, tokens, API keys or OAuth credentials.
- **No paid API and no key** is required to run, test or evaluate the project. Do not add a dependency on a hosted LLM to the default path.
- **Development tools are not runtime**: subscriptions to coding assistants are used to write code, never as the project's inference backend, and their OAuth tokens never go into CI or the repository (see `docs/adr/0002-dev-tools-vs-runtime.md`).
- **Prompt injection**: text from documents and from user questions is data, never instructions. Keep the system prompt authoritative; keep the `verify` step that rejects citations not in the retrieved set. Injection traps in the gold set must stay refused.
- Do not download or execute models or code from unverified sources. The optional reranker comes from a named Hugging Face model set in config (`rerank_model`); its revision is not pinned.
- Do not weaken the no-match gate or the refusal message to improve a metric.

## Law 25 (Québec privacy) rules

- **No real personal information** anywhere: no real names, emails, phone numbers, employee IDs, health or HR data. Corpora and docs use roles ("superviseur de soudage", "technicien qualité"), not people. Synthetic values are allowed only in `data/eval/` and `tests/`, and only under the rules of `data/eval/synthetic_identities.yaml` (`tests/test_repository_privacy.py` checks it).
- The HR corpus stays at 2/5 and only in `data/hr/corpus/`, never in `data/corpus/` (Quality item q050 is a trap because no HR text exists there).
- The HR access log lives in `logs/access/`, keeps 90 days at most, is meant for the privacy officer and IT under its authority (limit: the person using the workstation can read that workstation's lines; see the EFVP), never contains question text, digests or lengths, and cannot be turned off.
- Privacy bounds never move. Never open `data/eval/hr_holdout.jsonl` while writing or tuning rules; it is measured once and never enters CI.
- **Fictional only**: no real customer, no real Québec company brand. Domains use `alverno.example`.
- Every corpus document declares `sensitivity` (lab scale 1 to 5, see `docs/loi25.md`). The Quality module stays at 2/5. A document above 2 does not belong in the Quality corpus.
- HR questions about an individual must be refused by the Quality agent; they are evaluation traps.
- Local-first is a requirement, not a preference: any feature that would send corpus content or questions outside the workstation (and potentially outside Québec) needs an ADR and an update to `docs/loi25.md` (s. 17 assessment) before it is merged, and must be off by default.
- **No hosted tracing**: `QualityAgent.ask` runs the graph under `langsmith.tracing_context(enabled=False)`, so `LANGSMITH_TRACING=true` or `LANGCHAIN_TRACING_V2=true` in the environment sends nothing to LangSmith; a LangChain v1 variable (`LANGCHAIN_TRACING`, `LANGCHAIN_HANDLER`) raises `LegacyTracingError` instead (CLI exit code 2). Keep it; tests enforce it.
- **No cloud model by accident**: Ollama cloud models (names ending in `:cloud` or `-cloud`) run on Ollama's hosted service even through the local URL; `Settings.check_model_is_local` refuses them without `ALVERNO_ALLOW_REMOTE_OLLAMA=true`.
- Modules that will process personal information (HR, customer service) need a privacy impact assessment (EFVP, s. 3.3) documented before implementation (template: `docs/efvp/template.md`; HR: `docs/efvp/hr-module.md`). Re-assess before any link to an HRIS, authentication, hosted model or change of corpus scope.

## Git

- Do not commit or push unless the maintainer asks. Conventional, imperative English commit messages (`feat: add RRF fusion`, `fix: reject stale index`).
