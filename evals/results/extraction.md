# Extraction eval

Generated 2026-10-17 00:54 UTC by `make eval`. Pre-registration: evals/extraction/PREREG.md.

- Golden set: 40 notices, **model-labeled, unaudited** (labeled blind in session 1 before either extractor existed; the owner audits them in M2).
- Baseline: rules/baseline.py 1.0.0. It was developed on the 15 baseline-test notices, which are inside the golden set, so the 25 held-out notices are the fair baseline number.
- Graph: rules/graph.py 1.0.0, LLM_MODEL_ID=claude-haiku-5-5, prompt sha256 7dc29f49063ad6b0, LLM_MODE=replay.
- Scores are points (0 to 100): exact match for scalars, set F1 for lists (both empty counts as 1.0). Headline is the unweighted mean of the 11 fields. Grounded is the share of evidence spans whose quote equals the text at its offsets.

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline, all | 40 | 74.2 | 97.5 | 90.0 | 87.3 | 100.0 | 100.0 | 100.0 | 97.5 | 100.0 | 100.0 | 100.0 | **95.1** | 100.0 |
| baseline, 25 held-out | 25 | 66.7 | 96.0 | 84.0 | 79.7 | 100.0 | 100.0 | 100.0 | 96.0 | 100.0 | 100.0 | 100.0 | **92.9** | 100.0 |
| graph, all | 40 | 84.2 | 81.2 | 80.0 | 87.5 | 97.5 | 72.5 | 100.0 | 97.5 | 100.0 | 65.0 | 87.5 | **86.6** | 100.0 |
| graph, 25 held-out | 25 | 77.6 | 84.0 | 80.0 | 84.0 | 100.0 | 68.0 | 100.0 | 96.0 | 100.0 | 68.0 | 88.0 | **86.0** | 100.0 |

Graph runs: {'done': 40}, 40 model calls, 0.037476 USD as recorded in the live run (replaying the recordings costs nothing).

**Kill criterion (provisional):** Graph minus baseline headline = -8.5 points. Kill criterion met (5.0 or less): provisional on the unaudited M1 set; the 100-notice audited set in M2 decides.

**Diagnostic, not kill-deciding:** the same recorded responses scored before verify_spans removes values it cannot find verbatim in the notice. The gap between this row and `graph, all` is the cost of the verification rule, not of extraction. Values dropped by verification, by field: {'destruction_instruction': 13, 'sold_to': 11, 'model_label_location': 5, 'serial': 3, 'contact': 2, 'sold_from': 1}.

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| graph draft before verify_spans, all | 40 | 84.2 | 85.0 | 80.0 | 87.5 | 100.0 | 100.0 | 100.0 | 97.5 | 100.0 | 92.5 | 87.5 | **92.2** | 0.0 |

Worst-case cost of one live recording run of the graph on these 40 notices (conservative token estimate, 2 calls each): 0.1132 USD.
