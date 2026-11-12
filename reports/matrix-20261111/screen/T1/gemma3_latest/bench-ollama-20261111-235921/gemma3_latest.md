# Evaluation report — full (ollama)

- Created: 2026-11-12T05:01:58+00:00
- Items: 50 (35 answerable, 15 traps)
- Label: gemma3:latest
- backend: `ollama`
- chat_model: `gemma3:latest`
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
| Over-refusal rate (answerable refused) | 0.2 |
| Answer/refuse decision accuracy | 0.86 |
| Error rate (transport failures) | 0 |
| Latency p50 (ms) | 2661.9 |
| Latency p95 (ms) | 4621.8 |
| Citation validity rate | 0.8571 |
| Key-fact recall | 0.6333 |

## Items to review

| id | type | issue | retrieved |
|---|---|---|---|
| q005 | factual | over-refusal (no_valid_citation) | IT-301, SPEC-VEL-01 |
| q008 | factual | over-refusal (no_valid_citation) | POL-001, POL-003, QP-004 |
| q011 | procedural | over-refusal (no_valid_citation) | IT-102, IT-210, SPEC-STR-01 |
| q012 | procedural | key facts missing from answer (0.50) | IT-220, IT-210 |
| q015 | procedural | key facts missing from answer (0.50) | QP-001, POL-001, QP-010 |
| q017 | numeric | key facts missing from answer (0.00) | IT-150, SPEC-AGN-01 |
| q018 | numeric | over-refusal (no_valid_citation) | IT-101, SPEC-AGN-01, SPEC-VRN-01 |
| q020 | numeric | key facts missing from answer (0.50) | IT-210, SPEC-AGN-01, IT-220 |
| q022 | numeric | over-refusal (no_valid_citation) | IT-102, QP-008, IT-210 |
| q023 | numeric | over-refusal (no_valid_citation) | SPEC-VRN-01 |
| q025 | numeric | key facts missing from answer (0.50) | QP-010, FORM-002, IT-210 |
| q026 | multi_doc | key facts missing from answer (0.50) | SPEC-AGN-01, IT-101, SPEC-VRN-01 |
| q027 | multi_doc | key facts missing from context (0.67) | NC-2026-022, SPEC-STR-01 |
| q030 | multi_doc | key facts missing from answer (0.50) | SPEC-AGN-01, IT-150, IT-151 |
| q031 | version | over-refusal (no_valid_citation) | QP-006, QP-006-V2 |
| q032 | version | key facts missing from answer (0.50) | IT-210, QP-006, SPEC-STR-01 |
| q034 | version | key facts missing from answer (0.00) | QP-006, IT-102, QP-006-V2 |
