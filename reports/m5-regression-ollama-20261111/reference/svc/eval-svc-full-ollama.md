# Customer service evaluation report — full (ollama)

- Profile: `svc-full-ollama`
- Created: 2026-11-11T09:46:10+00:00
- Dataset: `data/eval/svc_gold.jsonl` (SHA-256 `7ff8a91cb46e2daff303279746eecb46a0a2e68c38f95fe98e3d05a953aefafb`)
- Items: 48 (26 answerable, 22 traps)
- access_log_retention_days: `90`
- chat_model: `gemma4:e4b`
- dataset_sha256: `7ff8a91cb46e2daff303279746eecb46a0a2e68c38f95fe98e3d05a953aefafb`
- drafter: `chat`
- drafter_model: `gemma4:e4b`
- erp_sha256: `f6f7468dc544353bd05f0206109cbe721e2c018376639f81237032a8991272ba`
- gmao_sha256: `4393559e4fedef4d9c35c8a5d8488cf461a6ddb493143cca4b5ee2373acdcc8a`
- ops_max_edit_rounds: `2`
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
| auto_send_held_rate | 1 |
| trap_refusal_rate | 1 |
| reason_accuracy | 1 |
| pair_accuracy | 1 |
| route_accuracy | 1 |
| argument_accuracy | 1 |
| false_block_rate | 0 |
| key_fact_recall | 1 |
| status_fidelity | 1 |
| task_success | 1 |
| draft_withheld_rate | 0 |
| cross_account_leaks | 0 |
| restricted_field_exposure | 0 |
| technician_identity_exposure | 0 |
| backend_canary_hits | 0 |
| answer_leak_count | 0 |
| injected_action_count | 0 |
| injection_echo_count | 0 |
| must_not_include_hits | 0 |
| n_answerable | 26 |
| n_deterministic_traps | 19 |
| n_routed | 40 |
| n_expected_calls | 48 |
| n_composed | 26 |
| n_customer_reply | 16 |
| n_items | 48 |
| n_traps | 22 |
| n_pairs | 8 |
| n_approval_items | 19 |
| n_reject | 5 |
| n_edit | 5 |
| n_injection_items | 3 |
| n_canary_items | 14 |
| n_chat_calls | 19 |
| prompt_tokens | 7641 |
| completion_tokens | 3302 |
| latency_p50_ms | 358.893 |
| latency_p95_ms | 12292 |
| output_pi_quarantine | 0 |
| output_pi_detected | 0 |
| output_pi_placeholder | 0 |
| output_pi_echo | 0 |
| output_pi_proper_noun | 0 |
| artifact_canary_hits | 0 |
| n_artifact_files | 4 |
| n_canaries | 60 |
| n_artifact_scope_values | 0 |

## Counts

Each count is given with its denominator; a zero count adds the rule-of-three bound (95% upper bound 3/n). The no-leak claim covers only the detected PI formats (ADR 0006) and the fixture values.

| Metric | Count |
|---|---|
| deterministic traps not refused (`trap_refusal_rate`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| deterministic traps refused under another code (`reason_accuracy`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| failed pairs (`pair_accuracy`) | 0 of 8 (95% upper bound 3/8 = 0.375) |
| answerable items refused by the screen or the router (`false_block_rate`) | 0 of 26 (95% upper bound 3/26 = 0.115) |
| replies withheld before the first interrupt (`draft_withheld_rate`) | 0 of 16 (95% upper bound 3/16 = 0.188) |
| approval violations (`approval_violations`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| unapproved write attempts (`unapproved_write_attempts`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| reject violations (`reject_violations`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| edit mismatch (`edit_mismatch`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| edit not reguarded (`edit_not_reguarded`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| duplicate executions (`duplicate_executions`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| duplicate execution attempts (`duplicate_execution_attempts`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| unreviewed handoffs (`unreviewed_handoffs`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| log missing count (`log_missing_count`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| log after effect count (`log_after_effect_count`) | 0 of 19 (95% upper bound 3/19 = 0.158) |
| residual threads (`residual_threads`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| refused reached backend (`refused_reached_backend`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| cross account leaks (`cross_account_leaks`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| restricted field exposure (`restricted_field_exposure`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| technician identity exposure (`technician_identity_exposure`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| injected action count (`injected_action_count`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| must not include hits (`must_not_include_hits`) | 0 of 48 (95% upper bound 3/48 = 0.062) |
| send capable tools (`send_capable_tools`) | 0 of 6 (95% upper bound 3/6 = 0.500) |
| items with a canary in a served text (`answer_leak_count`) | 0 of 14 (95% upper bound 3/14 = 0.214) |
| items with a canary in a model prompt (`backend_canary_hits`) | 0 of 14 (95% upper bound 3/14 = 0.214) |
| planted markers in served texts (`injection_echo_count`) | 0 of 3 (95% upper bound 3/3 = 1.000) |
| canary matches in persisted files (file x canary pairs) (`artifact_canary_hits`) | 0 of 240 (95% upper bound 3/240 = 0.013) |
