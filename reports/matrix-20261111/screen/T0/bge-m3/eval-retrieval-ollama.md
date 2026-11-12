# Evaluation report — retrieval (ollama)

- Created: 2026-11-12T03:15:54+00:00
- Items: 50 (35 answerable, 15 traps)
- backend: `ollama`
- corpus_dir: `data/corpus`
- embed_model: `bge-m3`
- final_k: `5`
- gate_signal: `cosine`
- no_match_threshold: `0.55`
- reranker: `none`
- rrf_k: `60`
- top_k: `20`

| Metric | Value |
|---|---|
| Retrieval hit@1 | 0.9429 |
| Retrieval hit@3 | 1 |
| Retrieval MRR | 0.9714 |
| Context recall (key facts in retrieved extracts) | 0.9762 |
| Trap refusal accuracy | 0.7333 |
| False answer rate on traps | 0.2667 |
| Over-refusal rate (answerable refused) | 0 |
| Answer/refuse decision accuracy | 0.92 |
| Error rate (transport failures) | 0 |
| Latency p50 (ms) | 176.9 |
| Latency p95 (ms) | 221.4 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q010 | procedural | key facts missing from context (0.50) | QP-005, FORM-002, SPEC-AGN-01 |
| q027 | multi_doc | key facts missing from context (0.67) | NC-2026-022, SPEC-STR-01 |
| q038 | trap_absent | trap answered | SPEC-VRN-01, NC-2026-014 |
| q039 | trap_absent | trap answered | SPEC-AGN-01, QP-009 |
| q040 | trap_absent | trap answered | QP-001, QP-008, POL-001 |
| q041 | trap_out_of_scope | trap answered | NC-2026-014, SPEC-VRN-01 |
