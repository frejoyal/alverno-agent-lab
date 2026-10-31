# Evaluation report — full (ollama)

- Created: 2026-10-31T15:50:36+00:00
- Items: 50 (35 answerable, 15 traps)
- Label: llama3.2:3b
- backend: `ollama`
- chat_model: `llama3.2:3b`
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
| Trap refusal accuracy | 0.8667 |
| False answer rate on traps | 0.1333 |
| Over-refusal rate (answerable refused) | 0.08571 |
| Answer/refuse decision accuracy | 0.9 |
| Error rate (transport failures) | 0 |
| Latency p50 (ms) | 12951.7 |
| Latency p95 (ms) | 19875.4 |
| Citation validity rate | 0.9626 |
| Key-fact recall | 0.7333 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q011 | procedural | key facts missing from answer (0.50) | IT-102, IT-210, SPEC-STR-01 |
| q012 | procedural | key facts missing from answer (0.50) | IT-220, IT-210 |
| q015 | procedural | key facts missing from answer (0.50) | QP-001, POL-001, QP-010 |
| q016 | procedural | over-refusal (low_confidence) | IT-301, SPEC-VEL-01, NC-2026-031 |
| q017 | numeric | key facts missing from answer (0.00) | IT-150, SPEC-AGN-01 |
| q018 | numeric | over-refusal (no_valid_citation) | IT-101, SPEC-AGN-01, SPEC-VRN-01 |
| q020 | numeric | key facts missing from answer (0.50) | IT-210, SPEC-AGN-01, IT-220 |
| q025 | numeric | key facts missing from answer (0.00) | QP-010, FORM-002, IT-210 |
| q026 | multi_doc | key facts missing from answer (0.50) | SPEC-AGN-01, IT-101, SPEC-VRN-01 |
| q027 | multi_doc | key facts missing from context (0.67) | NC-2026-022, SPEC-STR-01 |
| q030 | multi_doc | key facts missing from answer (0.50) | SPEC-AGN-01, IT-150, IT-151 |
| q032 | version | over-refusal (no_valid_citation) | IT-210, QP-006, SPEC-STR-01 |
| q034 | version | key facts missing from answer (0.00) | QP-006, IT-102, QP-006-V2 |
| q036 | trap_absent | trap answered | IT-102, POL-003, IT-220 |
| q038 | trap_absent | trap answered | SPEC-VRN-01, NC-2026-014 |
