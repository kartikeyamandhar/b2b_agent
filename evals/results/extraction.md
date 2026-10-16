# Extraction eval

Generated 2026-10-16 22:24 UTC by `make eval`. Pre-registration: evals/extraction/PREREG.md.

- Golden set: 40 notices, **model-labeled, unaudited** (labeled blind in session 1 before either extractor existed; the owner audits them in M2).
- Baseline: rules/baseline.py 1.0.0. It was developed on the 15 baseline-test notices, which are inside the golden set, so the 25 held-out notices are the fair baseline number.
- Graph: rules/graph.py 1.0.0, LLM_MODEL_ID=claude-haiku-5-5, prompt sha256 7dc29f49063ad6b0, LLM_MODE=replay.
- Scores are points (0 to 100): exact match for scalars, set F1 for lists (both empty counts as 1.0). Headline is the unweighted mean of the 11 fields. Grounded is the share of evidence spans whose quote equals the text at its offsets.

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline, all | 40 | 74.2 | 97.5 | 90.0 | 87.3 | 100.0 | 100.0 | 100.0 | 97.5 | 100.0 | 100.0 | 100.0 | **95.1** | 100.0 |
| baseline, 25 held-out | 25 | 66.7 | 96.0 | 84.0 | 79.7 | 100.0 | 100.0 | 100.0 | 96.0 | 100.0 | 100.0 | 100.0 | **92.9** | 100.0 |
| graph | 0 | not run | not run | not run | not run | not run | not run | not run | not run | not run | not run | not run | not run | not run |

**Graph not run:** no successful extraction ({'failed': 40}); replay recordings missing. Model calls: 0, spend: 0.000000 USD.

**Kill criterion:** not evaluable in M1. It needs one approved live recording run of the graph (estimated below) or the M2 run.

Worst-case cost of one live recording run of the graph on these 40 notices (conservative token estimate, 2 calls each): 0.1132 USD.
