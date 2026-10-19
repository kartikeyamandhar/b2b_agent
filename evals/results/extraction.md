# Extraction eval

Generated 2026-10-19 07:50 UTC by `make eval`. Pre-registration: evals/extraction/PREREG.md (Revisions 1 and 2).

- Golden set: 100 notices, **model-labeled, assistant-audited (owner delegated)**; golden_notices.json sha256 e701caae2d5efde9. The 60 extension notices were labeled blind by sub-agents and audited before either extractor ran on them; the 40 M1 notices were labeled and audited by the main session, which had seen both extractors' M1 results.
- Baseline: rules/baseline.py 1.0.0, developed only on the 15 baseline-test notices.
- Graph: rules/graph.py 1.0.0, LLM_MODEL_ID=claude-haiku-5-5, prompt sha256 7dc29f49063ad6b0, LLM_MODE=replay.
- Scores are points (0 to 100): exact match for scalars, set F1 for lists (both empty counts as 1.0). Headline is the unweighted mean of the 11 fields. Grounded is the share of evidence spans whose quote equals the text at its offsets.

**Decides: the 60 blind extension notices**

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 60 | 74.3 | 94.0 | 85.6 | 87.4 | 96.7 | 96.7 | 91.7 | 100.0 | 100.0 | 91.7 | 91.7 | **91.8** | 100.0 |
| graph | 60 | 78.7 | 94.2 | 81.7 | 89.0 | 98.3 | 68.3 | 100.0 | 100.0 | 96.7 | 70.0 | 90.0 | **87.9** | 100.0 |
| graph draft before verify_spans (diagnostic) | 60 | 78.7 | 94.2 | 81.7 | 89.0 | 100.0 | 100.0 | 100.0 | 100.0 | 98.3 | 88.3 | 90.0 | **92.7** | 0.2 |

**Context: all 100 notices**

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 100 | 74.3 | 95.4 | 87.3 | 85.0 | 98.0 | 98.0 | 95.0 | 99.0 | 100.0 | 94.0 | 95.0 | **92.8** | 100.0 |
| graph | 100 | 81.2 | 89.0 | 81.0 | 87.1 | 98.0 | 70.0 | 100.0 | 99.0 | 98.0 | 69.0 | 89.0 | **87.4** | 100.0 |

**Context: the 85 notices outside the baseline-test 15**

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 85 | 72.1 | 94.6 | 85.1 | 84.7 | 97.6 | 97.6 | 94.1 | 98.8 | 100.0 | 92.9 | 94.1 | **92.0** | 100.0 |
| graph | 85 | 78.7 | 91.2 | 81.2 | 87.2 | 98.8 | 68.2 | 100.0 | 98.8 | 97.6 | 70.6 | 89.4 | **87.4** | 100.0 |

**Context: the 40 M1 notices, audited labels**

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 40 | 74.2 | 97.5 | 90.0 | 81.5 | 100.0 | 100.0 | 100.0 | 97.5 | 100.0 | 97.5 | 100.0 | **94.4** | 100.0 |
| graph | 40 | 84.9 | 81.2 | 80.0 | 84.2 | 97.5 | 72.5 | 100.0 | 97.5 | 100.0 | 67.5 | 87.5 | **86.6** | 100.0 |

**Context: the 40 M1 notices, pre-audit labels (audit drift check)**

| extractor | n | models | serial_ranges | date_codes | sold_at | sold_from | sold_to | price_range_cents | units_affected | remedy_type | has_destruction_instruction | registration_url | headline | grounded |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 40 | 74.2 | 97.5 | 90.0 | 87.3 | 100.0 | 100.0 | 100.0 | 97.5 | 100.0 | 100.0 | 100.0 | **95.1** | 100.0 |
| graph | 40 | 84.2 | 81.2 | 80.0 | 87.5 | 97.5 | 72.5 | 100.0 | 97.5 | 100.0 | 65.0 | 87.5 | **86.6** | 100.0 |

**Kill criterion (decides, PREREG Revision 2):** on the 60 blind extension notices, graph minus baseline headline = -3.9 points. Kill criterion met (5.0 or less): the model-based extractor is demoted to a fallback.

Graph runs: {'done': 100}, 100 model calls, 0.092751 USD nominal as recorded (replaying costs nothing).

Values dropped by verify_spans, by field: {'destruction_instruction': 34, 'sold_to': 30, 'model_label_location': 14, 'serial': 3, 'contact': 2, 'sold_from': 2, 'remedy_type': 1}.

Worst-case cost of one live recording run of the graph on all 100 notices (conservative token estimate, 2 calls each): 0.2832 USD.
