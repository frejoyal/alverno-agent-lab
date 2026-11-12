# Evaluation report — full (ollama)

- Created: 2026-11-12T06:32:23+00:00
- Items: 50 (35 answerable, 15 traps)
- Label: gemma4:26b
- backend: `ollama`
- chat_model: `gemma4:26b`
- corpus_dir: `data/corpus`
- embed_model: `qwen3-embedding:0.6b`
- final_k: `5`
- gate_signal: `cosine`
- no_match_threshold: `0.55`
- reranker: `none`
- rrf_k: `60`
- top_k: `20`

| Metric | Value |
|---|---|
| Retrieval hit@1 | 0.9714 |
| Retrieval hit@3 | 1 |
| Retrieval MRR | 0.9857 |
| Context recall (key facts in retrieved extracts) | 0.9905 |
| Trap refusal accuracy | 1 |
| False answer rate on traps | 0 |
| Over-refusal rate (answerable refused) | 0 |
| Answer/refuse decision accuracy | 1 |
| Error rate (transport failures) | 0 |
| Latency p50 (ms) | 70408.9 |
| Latency p95 (ms) | 99947.5 |
| Citation validity rate | 1 |
| Key-fact recall | 0.9476 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q015 | procedural | key facts missing from answer (0.50) | QP-001, POL-001, QP-010 |
| q020 | numeric | key facts missing from answer (0.50) | IT-210, SPEC-AGN-01, IT-220 |
| q025 | numeric | key facts missing from answer (0.50) | QP-010, FORM-002, IT-210 |
| q027 | multi_doc | key facts missing from context (0.67) | NC-2026-022, SPEC-STR-01 |
