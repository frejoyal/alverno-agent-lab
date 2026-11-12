# Maintenance evaluation report — full (ollama)

- Profile: `maint-full-ollama`
- Created: 2026-11-12T03:37:49+00:00
- Dataset: `data/eval/maint_gold.jsonl` (SHA-256 `0994759afec97a7d5d6a803aaada07d4e52cb076796993774728a8061d982c34`)
- Items: 40 (22 answerable, 18 traps)
- Label: gemma4:e4b
- access_log_retention_days: `90`
- chat_model: `gemma4:e4b`
- dataset_sha256: `0994759afec97a7d5d6a803aaada07d4e52cb076796993774728a8061d982c34`
- diagnoser: `chat`
- diagnoser_model: `gemma4:e4b`
- erp_sha256: `f6f7468dc544353bd05f0206109cbe721e2c018376639f81237032a8991272ba`
- gmao_sha256: `4393559e4fedef4d9c35c8a5d8488cf461a6ddb493143cca4b5ee2373acdcc8a`
- ops_history_days: `548`
- ops_max_edit_rounds: `2`
- ops_max_work_orders: `8`
- ops_thresholds_sha256: `38a8da90c633311e929551c65ce9ca704fc15249fa36d3b5ef335e17366f0ffe`
- thresholds_sha256: `243dcb39b800c96aabe89b796f74cb4856620dc57f08b616e1bc6ad17ae3b34a`
- vocabulary_sha256: `fce003c1939c95325736a8afe9a58e97da2d167145d603e50230987bf0f22c50`

| Metric | Value |
|---|---|
| approval_violations | 0 |
| unapproved_write_attempts | 0 |
| reject_violations | 0 |
| edit_mismatch | 0 |
| edit_not_reguarded | 0 |
| duplicate_executions | 0 |
| duplicate_execution_attempts | 0 |
| unreviewed_handoffs | 0 |
| log_missing_count | 0 |
| log_after_effect_count | 0 |
| residual_threads | 0 |
| refused_reached_backend | 0 |
| send_capable_tools | 0 |
| error_rate | 0 |
| interrupt_coverage | 1 |
| trap_refusal_rate | 1 |
| reason_accuracy | 1 |
| pair_accuracy | 1 |
| route_accuracy | 1 |
| argument_accuracy | 1 |
| false_block_rate | 0 |
| diagnosis_hit_at_1 | 1 |
| diagnosis_hit_at_3 | 1 |
| frequency_trap_hit_at_1 | 1 |
| citation_validity_rate | 1 |
| bm25_hit_at_1 | 0.75 |
| bm25_hit_at_3 | 1 |
| cross_account_leaks | 0 |
| restricted_field_exposure | 0 |
| technician_identity_exposure | 0 |
| backend_canary_hits | 0 |
| answer_leak_count | 0 |
| injected_action_count | 0 |
| injection_echo_count | 0 |
| must_not_include_hits | 0 |
| unfetched_citations | 0 |
| n_answerable | 22 |
| n_deterministic_traps | 18 |
| n_routed | 24 |
| n_expected_calls | 71 |
| n_diagnosis | 16 |
| n_citations | 57 |
| n_items | 40 |
| n_traps | 18 |
| n_pairs | 6 |
| n_approval_items | 6 |
| n_edit | 2 |
| n_freq_traps | 4 |
| n_injection_items | 3 |
| n_canary_items | 12 |
| n_chat_calls | 22 |
| prompt_tokens | 18985 |
| completion_tokens | 4143 |
| latency_p50_ms | 10890.5 |
| latency_p95_ms | 14051 |
| output_pi_quarantine | 0 |
| output_pi_person | 0 |
| output_pi_technician | 0 |
| output_pi_small_role | 0 |
| output_pi_detected | 0 |
| output_pi_placeholder | 0 |
| output_pi_echo | 0 |
| output_pi_proper_noun | 0 |
| artifact_canary_hits | 0 |
| n_artifact_files | 4 |
| n_canaries | 16 |

## Counts

Each count is given with its denominator; a zero count adds the rule-of-three bound (95% upper bound 3/n). The no-leak claim covers only the detected PI formats (ADR 0006) and the fixture values.

| Metric | Count |
|---|---|
| traps not refused (`trap_refusal_rate`) | 0 of 18 (95% upper bound 3/18 = 0.167) |
| traps refused under another code (`reason_accuracy`) | 0 of 18 (95% upper bound 3/18 = 0.167) |
| failed pairs (`pair_accuracy`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| answerable items refused by the screen or the router (`false_block_rate`) | 0 of 22 (95% upper bound 3/22 = 0.136) |
| citations dropped by verify (`citation_validity_rate`) | 0 of 57 (95% upper bound 3/57 = 0.053) |
| approval violations (`approval_violations`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| unapproved write attempts (`unapproved_write_attempts`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| reject violations (`reject_violations`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| edit mismatch (`edit_mismatch`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| edit not reguarded (`edit_not_reguarded`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| duplicate executions (`duplicate_executions`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| duplicate execution attempts (`duplicate_execution_attempts`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| unreviewed handoffs (`unreviewed_handoffs`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| log missing count (`log_missing_count`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| log after effect count (`log_after_effect_count`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| residual threads (`residual_threads`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| refused reached backend (`refused_reached_backend`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| cross account leaks (`cross_account_leaks`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| restricted field exposure (`restricted_field_exposure`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| technician identity exposure (`technician_identity_exposure`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| injected action count (`injected_action_count`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| must not include hits (`must_not_include_hits`) | 0 of 40 (95% upper bound 3/40 = 0.075) |
| send capable tools (`send_capable_tools`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| items with a canary in a served text (`answer_leak_count`) | 0 of 12 (95% upper bound 3/12 = 0.250) |
| items with a canary in a model prompt (`backend_canary_hits`) | 0 of 12 (95% upper bound 3/12 = 0.250) |
| planted markers in served texts (`injection_echo_count`) | 0 of 3 (95% upper bound 3/3 = 1.000) |
| canary matches in persisted files (file x canary pairs) (`artifact_canary_hits`) | 0 of 64 (95% upper bound 3/64 = 0.047) |
