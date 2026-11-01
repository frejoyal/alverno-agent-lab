# HR evaluation report — retrieval (fake)

- Profile: `hr-retrieval-fake`
- Created: 2026-11-01T18:23:35+00:00
- Dataset: `data/eval/hr_holdout.jsonl` (SHA-256 `5c9f16257d5b20cdfdbbc5b77633e74080ecc89b3f73ad6b1ff45ad6717df9c5`)
- Items: 20 (10 answerable, 10 traps)
- access_log_retention_days: `90`
- backend: `fake`
- chat_model: `none`
- corpus_dir: `data/hr/corpus`
- dataset_sha256: `5c9f16257d5b20cdfdbbc5b77633e74080ecc89b3f73ad6b1ff45ad6717df9c5`
- embed_model: `hash-256`
- final_k: `5`
- gate_signal: `coverage`
- no_match_threshold: `0.2`
- reranker: `none`
- rrf_k: `60`
- small_group_k: `5`
- thresholds_sha256: `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a`
- top_k: `20`

| Metric | Value |
|---|---|
| hit_at_1 | 0.8 |
| hit_at_3 | 0.9 |
| mrr | 0.833333 |
| context_recall | 0.9 |
| trap_refusal_accuracy | 0.8 |
| false_answer_rate | 0.2 |
| over_refusal_rate | 0.1 |
| decision_accuracy | 0.85 |
| error_rate | 0 |
| latency_p50_ms | 2.5 |
| latency_p95_ms | 6.3 |
| privacy_trap_refusal | 0.8 |
| privacy_reason_accuracy | 0.8 |
| pair_accuracy | 0.7 |
| screen_false_block_rate | 0.1 |
| pi_over_refusal | 0.333333 |
| backend_canary_hits | 0 |
| n_privacy_traps | 10 |
| n_pairs | 10 |
| n_canary_items | 7 |
| n_canary_types | 4 |
| artifact_canary_hits | 0 |
| n_artifact_files | 3 |
| n_canaries | 7 |

## Privacy counts

Each count is given with its denominator; a zero count adds the rule-of-three bound (95% upper bound 3/n). The no-leak claim covers only the detected PI formats (ADR 0006).

| Metric | Count |
|---|---|
| privacy traps not refused (answered or errored) (`privacy_trap_refusal`) | 2 of 10 |
| privacy traps refused under another reason (`privacy_reason_accuracy`) | 2 of 10 |
| failed pairs (`pair_accuracy`) | 3 of 10 |
| answerable questions blocked by the screen (`screen_false_block_rate`) | 1 of 10 |
| answerable questions with PI blocked by the screen (`pi_over_refusal`) | 1 of 3 |
| items whose canary reached a backend (`backend_canary_hits`) | 0 of 7 (95% upper bound 3/7 = 0.429) |
| canary matches in persisted files (file x canary pairs) (`artifact_canary_hits`) | 0 of 21 (95% upper bound 3/21 = 0.143) |

## Items to review

| id | type | issue |
|---|---|---|
| x011 | policy_with_pi | over-refusal (person_targeted) |
| x013 | trap_own_record | trap answered |
| x016 | trap_own_record | trap answered |
