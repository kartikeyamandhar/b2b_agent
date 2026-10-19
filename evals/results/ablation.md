# Adjudication ablation

Generated 2026-10-19 07:51 UTC by `make eval`. Pre-registration: evals/adjudication/PREREG.md (Revision 2). LLM_MODEL_ID=claude-haiku-5-5, LLM_MODE=replay.

- Every row that reads photos gets the same claim policy (outcome_policy.yaml with photo_rules): the rules_plus_vision decision table in code, the agent rows in their instructions. rules_only cannot read photos.
- Counted steps are model calls plus rejected tool calls (the cap counts these); rows are trace rows written. Cost is nominal (recorded usage times the price table), the same in live and replay. Seconds use the replay virtual clock (recorded live latency).
- loop_full_cheap_model is not applicable: one model is priced, and it is already the cheapest vision model.

**golden300: 300 synthetic claims (kill-criterion block)**

| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed | capped | counted steps | rows | mean cost USD | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules_only | 300 | 0.987 | **0.982** | 0.987 | 1.000 | 1.000 | 1.000 | 0.253 | 0.000 | 0.0 | 7.0 | 0.00000 | 0.00 |
| rules_plus_vision | 300 | 0.943 | **0.933** | 0.943 | 1.000 | 1.000 | 0.709 | 0.240 | 0.000 | 1.2 | 8.2 | 0.00020 | 1.30 |
| single_pass | 300 | 0.930 | **0.919** | 0.937 | 1.000 | 1.000 | 0.686 | 0.260 | 0.010 | 2.4 | 10.0 | 0.00091 | 5.40 |
| loop_full | 300 | 0.927 | **0.916** | 0.927 | 0.947 | 1.000 | 0.684 | 0.257 | 0.000 | 4.1 | 11.6 | 0.00159 | 6.75 |
| loop_no_retrieval | 300 | 0.923 | **0.908** | 0.930 | 1.000 | 1.000 | 0.643 | 0.247 | 0.000 | 4.0 | 11.4 | 0.00151 | 6.80 |
| loop_keyword_only | 300 | 0.927 | **0.915** | 0.927 | 0.960 | 1.000 | 0.679 | 0.257 | 0.000 | 4.1 | 11.6 | 0.00159 | 6.81 |

**probe60: assistant-designed capability probe (targets rules_only blind spots by construction)**

| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed | capped | counted steps | rows | mean cost USD | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules_only | 60 | 0.167 | **0.095** | 0.167 | n/a | n/a | 0.000 | 0.117 | 0.000 | 0.0 | 7.0 | 0.00000 | 0.00 |
| rules_plus_vision | 60 | 0.667 | **0.726** | 0.667 | n/a | n/a | 0.500 | 0.017 | 0.000 | 1.9 | 8.9 | 0.00031 | 2.58 |
| single_pass | 60 | 0.583 | **0.663** | 0.583 | n/a | n/a | 0.429 | 0.067 | 0.000 | 3.1 | 9.9 | 0.00111 | 6.47 |
| loop_full | 60 | 0.633 | **0.703** | 0.633 | 0.000 | n/a | 0.486 | 0.033 | 0.000 | 4.8 | 12.6 | 0.00180 | 8.47 |
| loop_no_retrieval | 60 | 0.633 | **0.699** | 0.633 | n/a | n/a | 0.474 | 0.017 | 0.000 | 4.8 | 12.5 | 0.00172 | 8.46 |
| loop_keyword_only | 60 | 0.617 | **0.691** | 0.617 | 0.000 | n/a | 0.472 | 0.050 | 0.000 | 4.8 | 12.6 | 0.00181 | 8.50 |

Correct by category:

| category | n | rules_only | rules_plus_vision | single_pass | loop_full | loop_no_retrieval | loop_keyword_only |
|---|---|---|---|---|---|---|---|
| clean | 10 | 10 | 4 | 3 | 3 | 3 | 3 |
| destruction_photo_intact | 10 | 0 | 9 | 9 | 9 | 9 | 9 |
| label_confirms_missing_typed_model | 10 | 0 | 4 | 4 | 4 | 4 | 4 |
| label_confirms_typed_typo | 10 | 0 | 3 | 3 | 3 | 3 | 3 |
| label_shows_other_model | 10 | 0 | 10 | 10 | 10 | 10 | 10 |
| label_unreadable | 10 | 0 | 10 | 6 | 9 | 9 | 8 |

**standin40: agent-written stand-in for the classmate set (shares the generator's renderer)**

| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed | capped | counted steps | rows | mean cost USD | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules_only | 40 | 0.525 | **0.583** | 0.575 | 1.000 | 0.750 | 0.250 | 0.250 | 0.000 | 0.0 | 7.0 | 0.00000 | 0.00 |
| rules_plus_vision | 40 | 0.625 | **0.668** | 0.750 | 1.000 | 0.750 | 0.300 | 0.150 | 0.000 | 1.5 | 8.5 | 0.00024 | 1.48 |
| single_pass | 40 | 0.675 | **0.710** | 0.750 | 1.000 | 0.750 | 0.308 | 0.350 | 0.025 | 2.8 | 10.3 | 0.00111 | 6.51 |
| loop_full | 40 | 0.750 | **0.774** | 0.800 | 1.000 | 0.750 | 0.400 | 0.275 | 0.000 | 4.5 | 11.5 | 0.00168 | 7.35 |
| loop_no_retrieval | 40 | 0.700 | **0.718** | 0.775 | 1.000 | 0.750 | 0.308 | 0.300 | 0.000 | 4.3 | 11.2 | 0.00155 | 7.46 |
| loop_keyword_only | 40 | 0.750 | **0.774** | 0.800 | 1.000 | 0.750 | 0.400 | 0.275 | 0.000 | 4.5 | 11.5 | 0.00168 | 7.34 |

Correct by category:

| category | n | rules_only | rules_plus_vision | single_pass | loop_full | loop_no_retrieval | loop_keyword_only |
|---|---|---|---|---|---|---|---|
| expect_approve | 16 | 6 | 6 | 9 | 9 | 9 | 9 |
| expect_deny | 9 | 5 | 7 | 8 | 9 | 9 | 9 |
| expect_flag | 8 | 6 | 6 | 6 | 6 | 6 | 6 |
| expect_needs_info | 7 | 4 | 6 | 4 | 6 | 4 | 6 |

**Kill criterion (golden300):** loop_full macro F1 0.916 minus rules_only 0.982 = -0.066. Within 0.05: the criterion fires, so the agent's value is in fraud detection, photo reading, and human routing (see probe60 and standin40), and the write-up leads with those numbers.
