# Evaluation report — retrieval (ollama)

- Created: 2026-11-12T03:16:22+00:00
- Items: 50 (35 answerable, 15 traps)
- backend: `ollama`
- corpus_dir: `data/corpus`
- embed_model: `nomic-embed-text`
- final_k: `5`
- gate_signal: `cosine`
- no_match_threshold: `0.55`
- reranker: `none`
- rrf_k: `60`
- top_k: `20`

| Metric | Value |
|---|---|
| Retrieval hit@1 | 0.9143 |
| Retrieval hit@3 | 1 |
| Retrieval MRR | 0.9524 |
| Context recall (key facts in retrieved extracts) | 0.9095 |
| Trap refusal accuracy | 0.4 |
| False answer rate on traps | 0.6 |
| Over-refusal rate (answerable refused) | 0 |
| Answer/refuse decision accuracy | 0.82 |
| Error rate (transport failures) | 0 |
| Latency p50 (ms) | 34.1 |
| Latency p95 (ms) | 44.7 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q006 | factual | key facts missing from context (0.00) | SPEC-STR-01, IT-210 |
| q010 | procedural | key facts missing from context (0.50) | QP-005, FORM-002, QP-002 |
| q027 | multi_doc | key facts missing from context (0.33) | SPEC-STR-01, IT-150, NC-2026-022 |
| q029 | multi_doc | key facts missing from context (0.00) | SPEC-VEL-01, NC-2026-031, IT-301 |
| q036 | trap_absent | trap answered | IT-102, POL-003, SPEC-STR-01 |
| q037 | trap_absent | trap answered | POL-003, QP-008, IT-102 |
| q038 | trap_absent | trap answered | SPEC-VRN-01, NC-2026-014 |
| q039 | trap_absent | trap answered | QP-009, SPEC-AGN-01, SPEC-STR-01 |
| q040 | trap_absent | trap answered | POL-003, IT-301, QP-001 |
| q041 | trap_out_of_scope | trap answered | NC-2026-014, SPEC-VRN-01 |
| q042 | trap_out_of_scope | trap answered | SPEC-STR-01, QP-004, QP-007 |
| q043 | trap_out_of_scope | trap answered | QP-009, FORM-001, NC-2026-022 |
| q050 | trap_injection | trap answered | POL-001, QP-004, POL-002 |
