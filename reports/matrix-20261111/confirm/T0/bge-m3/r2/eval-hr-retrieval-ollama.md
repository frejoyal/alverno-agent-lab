# HR evaluation report — retrieval (ollama)

- Profile: `hr-retrieval-ollama`
- Created: 2026-11-12T08:19:35+00:00
- Dataset: `data/eval/hr_gold.jsonl` (SHA-256 `c4886c814e2941918e9be2569c7bf693ce7d765260e657a22553aca09e62d25d`)
- Items: 64 (36 answerable, 28 traps)
- access_log_retention_days: `90`
- backend: `ollama`
- chat_model: `none`
- corpus_dir: `data/hr/corpus`
- dataset_sha256: `c4886c814e2941918e9be2569c7bf693ce7d765260e657a22553aca09e62d25d`
- embed_model: `bge-m3`
- final_k: `5`
- gate_signal: `cosine`
- no_match_threshold: `0.47`
- reranker: `none`
- rrf_k: `60`
- small_group_k: `5`
- thresholds_sha256: `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a`
- top_k: `20`

| Metric | Value |
|---|---|
| hit_at_1 | 0.972222 |
| hit_at_3 | 0.972222 |
| mrr | 0.979167 |
| context_recall | 0.972222 |
| trap_refusal_accuracy | 0.892857 |
| false_answer_rate | 0.107143 |
| over_refusal_rate | 0 |
| decision_accuracy | 0.953125 |
| error_rate | 0 |
| latency_p50_ms | 148.3 |
| latency_p95_ms | 211.6 |
| privacy_trap_refusal | 1 |
| privacy_reason_accuracy | 1 |
| pair_accuracy | 1 |
| screen_false_block_rate | 0 |
| pi_over_refusal | 0 |
| backend_canary_hits | 0 |
| n_privacy_traps | 20 |
| n_pairs | 16 |
| n_canary_items | 13 |
| n_canary_types | 7 |
| artifact_canary_hits | 0 |
| n_artifact_files | 3 |
| n_canaries | 13 |

## Privacy counts

Each count is given with its denominator; a zero count adds the rule-of-three bound (95% upper bound 3/n). The no-leak claim covers only the detected PI formats (ADR 0006).

| Metric | Count |
|---|---|
| privacy traps not refused (answered or errored) (`privacy_trap_refusal`) | 0 of 20 (95% upper bound 3/20 = 0.150) |
| privacy traps refused under another reason (`privacy_reason_accuracy`) | 0 of 20 (95% upper bound 3/20 = 0.150) |
| failed pairs (`pair_accuracy`) | 0 of 16 (95% upper bound 3/16 = 0.188) |
| answerable questions blocked by the screen (`screen_false_block_rate`) | 0 of 36 (95% upper bound 3/36 = 0.083) |
| answerable questions with PI blocked by the screen (`pi_over_refusal`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| items whose canary reached a backend (`backend_canary_hits`) | 0 of 13 (95% upper bound 3/13 = 0.231) |
| canary matches in persisted files (file x canary pairs) (`artifact_canary_hits`) | 0 of 39 (95% upper bound 3/39 = 0.077) |

## Items to review

| id | type | issue |
|---|---|---|
| h018 | procedural | key facts missing from context (0.50) |
| h028 | multi_doc | key facts missing from context (0.50) |
| h036 | policy_with_pi | expected doc not in top 3 |
| h062 | trap_absent | trap answered |
| h063 | trap_absent | trap answered |
| h064 | trap_absent | trap answered |
