# Task and model matrix (module 4)

Pre-registered rule ([ADR 0024](../../docs/adr/0024-bench-evaluation-protocol.md), in French): the lightest admissible model, confirmed at k = 3.

| Task | Retained model | Memory (GB) | Tier | Confirmations tried | Strongest (information) |
|---|---|---|---|---|---|
| T1 Quality | `ministral-3:8b` | 7.86 | 12 GB | `ministral-3:8b` | `gemma4:e4b` |
| T2 HR | `gemma4:e4b` | 9.97 | 12 GB | `gemma4:e4b` | `gemma4:e4b` |
| T3a Service | `gemma4:e4b` | 9.97 | 12 GB | `gemma4:e4b` | `gemma4:e4b` |
| T3b Maintenance | `ministral-3:8b` | 7.86 | 12 GB | `ministral-3:8b` | `gemma4:e4b` |
| T5b Management | `llama3.2:3b` | 3.16 | 8 GB | `llama3.2:3b` | `llama3:latest` |
| T0 Retrieval | `bge-m3` | 1.08 | 8 GB | `bge-m3` | `qwen3-embedding:0.6b` |

**Overall tier**: 12 GB of GPU memory (heaviest retained model, tasks T1 to T5b).

## T1 Quality

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | key_fact_recall | citation_validity_rate | trap_refusal_accuracy | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 1 | ✅ | yes | 0 | 0.000 | 0.948 | 1.000 | 1.000 | 6.7 s | 15.6 s | 17.9 | 3.30 | 6.67 | 9.97 |
| `ministral-3:8b` | 1 | ✅ | yes | 0 | 0.000 | 0.867 | 0.967 | 1.000 | 19.4 s | 34.4 s | 8.9 | 5.30 | 2.56 | 7.86 |
| `llama3.2:3b` | 1 | ❌ | no | 0 | 0.000 | 0.733 | 0.964 | 0.867 | 3.8 s | 13.7 s | 142.7 | 3.16 | 0.00 | 3.16 |
| `gemma3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.633 | 0.857 | 1.000 | 2.7 s | 4.6 s | 83.0 | 4.13 | 0.00 | 4.13 |
| `llama3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.824 | 0.943 | 0.933 | 12.2 s | 24.3 s | 26.7 | 5.32 | 0.40 | 5.72 |
| `gemma4:26b` | 1 | ✅ | yes | 0 | 0.000 | 0.948 | 1.000 | 1.000 | 70.4 s | 99.9 s | 7.0 | 5.37 | 13.44 | 18.81 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | key_fact_recall | citation_validity_rate | trap_refusal_accuracy | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ministral-3:8b` | 3 | ✅ | yes | 0 | 0.000 | 0.838 | 0.966 | 1.000 | 14.7 s | 22.7 s | 17.0 | 5.91 | 1.95 | 7.86 |

## T2 HR

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | key_fact_recall | citation_validity_rate | privacy_trap_refusal | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 1 | ✅ | yes | 0 | 0.000 | 0.833 | 1.000 | 1.000 | 6.2 s | 12.3 s | 18.5 | 3.30 | 6.67 | 9.97 |
| `ministral-3:8b` | 1 | ❌ | no | 0 | 0.000 | 0.639 | 1.000 | 1.000 | 24.1 s | 39.2 s | 9.1 | 5.30 | 2.56 | 7.86 |
| `llama3.2:3b` | 1 | ❌ | no | 0 | 0.000 | 0.556 | 0.953 | 1.000 | 3.1 s | 7.1 s | 126.0 | 3.16 | 0.00 | 3.16 |
| `gemma3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.222 | 1.000 | 1.000 | 1.9 s | 3.9 s | 78.4 | 4.13 | 0.00 | 4.13 |
| `llama3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.625 | 1.000 | 1.000 | 11.6 s | 17.3 s | 26.8 | 5.32 | 0.40 | 5.72 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | key_fact_recall | citation_validity_rate | privacy_trap_refusal | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 3 | ✅ | yes | 0 | 0.000 | 0.833 | 1.000 | 1.000 | 5.9 s | 11.8 s | 17.9 | 3.30 | 6.67 | 9.97 |

## T3a Service

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | task_success | key_fact_recall | status_fidelity | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 1 | ✅ | yes | 0 | 0.000 | 1.000 | 1.000 | 1.000 | 0.4 s | 14.3 s | 18.8 | 3.30 | 6.67 | 9.97 |
| `ministral-3:8b` | 1 | ❌ | no | 0 | 0.000 | 0.615 | 0.762 | 0.905 | 0.3 s | 20.3 s | 9.6 | 5.30 | 2.56 | 7.86 |
| `llama3.2:3b` | 1 | ❌ | no | 0 | 0.000 | 0.654 | 0.799 | 0.870 | 0.3 s | 8.1 s | 144.1 | 3.16 | 0.00 | 3.16 |
| `gemma3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.615 | 0.841 | 0.875 | 0.3 s | 3.6 s | 86.6 | 4.13 | 0.00 | 4.13 |
| `llama3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.500 | 0.616 | 0.882 | 0.4 s | 18.0 s | 26.3 | 5.32 | 0.40 | 5.72 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | task_success | key_fact_recall | status_fidelity | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 3 | ✅ | yes | 0 | 0.000 | 1.000 | 1.000 | 1.000 | 0.3 s | 13.7 s | 17.5 | 3.30 | 6.67 | 9.97 |

## T3b Maintenance

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | diagnosis_hit_at_1 | citation_validity_rate | argument_accuracy | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 1 | ✅ | yes | 0 | 0.000 | 1.000 | 1.000 | 1.000 | 10.9 s | 14.1 s | 18.5 | 3.30 | 6.67 | 9.97 |
| `ministral-3:8b` | 1 | ✅ | yes | 0 | 0.000 | 0.938 | 1.000 | 1.000 | 21.4 s | 30.6 s | 10.1 | 5.30 | 2.56 | 7.86 |
| `llama3.2:3b` | 1 | ❌ | no | 0 | 0.000 | 0.625 | 1.000 | 1.000 | 7.1 s | 9.0 s | 122.0 | 3.16 | 0.00 | 3.16 |
| `gemma3:latest` | 1 | ❌ | no | `must_not_include_hits` | 0.000 | 0.750 | 1.000 | 1.000 | 2.9 s | 3.5 s | 87.2 | 4.13 | 0.00 | 4.13 |
| `llama3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.500 | 1.000 | 1.000 | 9.9 s | 12.3 s | 26.7 | 5.32 | 0.40 | 5.72 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | diagnosis_hit_at_1 | citation_validity_rate | argument_accuracy | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ministral-3:8b` | 3 | ✅ | yes | 0 | 0.000 | 0.938 | 1.000 | 1.000 | 24.7 s | 33.3 s | 8.5 | 5.01 | 2.85 | 7.86 |

## T5b Management

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | task_success | count_fidelity | key_fact_recall | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemma4:e4b` | 1 | ✅ | yes | 0 | 0.000 | 0.821 | 1.000 | 0.911 | 5.7 s | 12.2 s | 18.9 | 3.30 | 6.67 | 9.97 |
| `ministral-3:8b` | 1 | ✅ | yes | 0 | 0.000 | 0.786 | 1.000 | 0.857 | 10.5 s | 30.6 s | 8.1 | 5.15 | 2.71 | 7.86 |
| `llama3.2:3b` | 1 | ✅ | yes | 0 | 0.000 | 0.821 | 1.000 | 0.857 | 3.7 s | 11.6 s | 122.2 | 3.16 | 0.00 | 3.16 |
| `gemma3:latest` | 1 | ❌ | no | 0 | 0.000 | 0.786 | 1.000 | 0.804 | 1.9 s | 4.4 s | 81.8 | 4.13 | 0.00 | 4.13 |
| `llama3:latest` | 1 | ✅ | yes | 0 | 0.000 | 0.857 | 1.000 | 0.911 | 6.8 s | 21.0 s | 28.2 | 5.32 | 0.40 | 5.72 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | task_success | count_fidelity | key_fact_recall | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `llama3.2:3b` | 3 | ✅ | yes | 0 | 0.000 | 0.821 | 1.000 | 0.857 | 4.2 s | 12.0 s | 124.9 | 3.16 | 0.00 | 3.16 |

## T0 Retrieval

**Screening (k = 1)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | hit_at_1 | mrr | context_recall | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen3-embedding:0.6b` | 1 | ✅ | yes | 0 | 0.000 | 0.971 | 0.986 | 0.991 | 0.1 s | 0.4 s | — | 1.08 | 0.00 | 1.08 |
| `bge-m3` | 1 | ✅ | yes | 0 | 0.000 | 0.943 | 0.971 | 0.976 | 0.2 s | 0.2 s | — | 1.08 | 0.00 | 1.08 |
| `nomic-embed-text` | 1 | ❌ | no | 0 | 0.000 | 0.914 | 0.952 | 0.909 | 0.0 s | 0.0 s | — | 0.55 | 0.00 | 0.55 |

**Confirmation (k = 3)**

| Model | k | Gate | Admissible | Non-zero counts | Errors | hit_at_1 | mrr | context_recall | p50 | p95 | tokens/s | GPU (GB) | RAM spill (GB) | Total (GB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bge-m3` | 3 | ✅ | yes | 0 | 0.000 | 0.943 | 0.971 | 0.976 | 0.2 s | 0.2 s | — | 1.08 | 0.00 | 1.08 |

## Conditions

- `measured`: 2026-11-12
- `backend`: ollama
- `ollama_version`: 0.24.0
- `num_ctx`: 8192
- `num_predict`: 1024
- `embed_num_ctx`: 2048
- `thresholds_file_sha256`: 539f4588262843ade98b57353379a1537f050d281a7ac789ad073902135c70fa
- `limit`: none

## Hosted reference (informative)

`claude-haiku` (claude-haiku-5-5, command line 2.1.321): 15 items kept out of 28 eligible, 15 prompts sent; 13 left out for a `SPEC-*` extract, 0 blocked by the detector. Outside the recommendation rule ([ADR 0023](../../docs/adr/0023-hosted-reference.md), in French).

| Metric | Value |
|---|---|
| `false_answer_rate` | 0.000 |
| `over_refusal_rate` | 0.000 |
| `error_rate` | 0.000 |
| `latency_p50_ms` | 5.8 s |
| `latency_p95_ms` | 6.8 s |
| `citation_validity_rate` | 1.000 |
| `key_fact_recall` | 0.933 |
| `hosted_pi_sent` | 0 (negative control : 1 ✅) |
| `hosted_trap_sent` | 0 (negative control : 1 ✅) |
| `hosted_spec_sent` | 0 (negative control : 1 ✅) |
| `hosted_calls_default` | 0 (negative control : 1 ✅) |
