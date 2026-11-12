# Evaluation report — full (ollama)

- Created: 2026-11-12T06:44:10+00:00
- Items: 50 (35 answerable, 15 traps)
- Label: ministral-3:8b
- backend: `ollama`
- chat_model: `ministral-3:8b`
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
| Latency p50 (ms) | 15455.5 |
| Latency p95 (ms) | 25138.8 |
| Citation validity rate | 0.9655 |
| Key-fact recall | 0.8381 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q012 | procedural | key facts missing from answer (0.50) | IT-220, IT-210 |
| q013 | procedural | key facts missing from answer (0.50) | QP-006, IT-150, QP-004 |
| q015 | procedural | key facts missing from answer (0.50) | QP-001, POL-001, QP-010 |
| q016 | procedural | key facts missing from answer (0.00) | IT-301, SPEC-VEL-01, NC-2026-031 |
| q020 | numeric | key facts missing from answer (0.50) | IT-210, SPEC-AGN-01, IT-220 |
| q025 | numeric | key facts missing from answer (0.50) | QP-010, FORM-002, IT-210 |
| q026 | multi_doc | key facts missing from answer (0.50) | SPEC-AGN-01, IT-101, SPEC-VRN-01 |
| q027 | multi_doc | key facts missing from context (0.67) | NC-2026-022, SPEC-STR-01 |
| q028 | multi_doc | key facts missing from answer (0.50) | QP-004, FORM-001, QP-003 |
| q032 | version | key facts missing from answer (0.50) | IT-210, QP-006, SPEC-STR-01 |
