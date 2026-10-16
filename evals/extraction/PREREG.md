# Extraction eval pre-registration (M1)

Committed before either extractor exists and before any label is written. Changing anything below after the first eval run needs a decisions-log entry, and the changed run is reported as a separate row.

## Sample

- Pool: CPSC API records with recall_date from 2024-01-01 to 2026-10-16 (data/raw/cpsc, see data/raw/SOURCES.md).
- Size: 40 notices for M1 (extended to 100 owner-audited notices in M2; the 40 stay in the set).
- Category: a keyword rule on the lowercased title, first match wins, in this order: children, furniture, battery_power, appliance_electrical, outdoor_sports, clothing_textile, other. The keyword lists are in apps/api/src/recallops/evals/sampling.py (CATEGORY_KEYWORDS) and are part of this registration. Products[].Type is blank on 1,570 of 1,657 archive records, so it cannot stratify 2024 and 2025.
- Identifier-rich stratum: notices whose source_text mentions a serial number, a date code, a lot number, or a manufacture date range. 10 of the 40 come from this stratum, allocated across recall years by largest remainder, so serial and date-code checks have data (only 4 of 31 notices in the 2026-10-16 ideation sample had them).
- The other 30 come from the remaining pool, allocated across categories by largest remainder on pool counts, drawn within each cell with Polars `sample(seed=...)`.
- Seed: 20261016. No redraws, no skips. A notice that is hard to label is labeled with UNSURE notes, not replaced.

## Subsets (owner decision, session 1)

- 15 baseline unit-test notices: the first 15 golden notices in the seeded permutation `sample(fraction=1.0, shuffle=True, seed=20261017)` of the 40 source ids sorted ascending.
- 20 synthetic-source notices: the 20 golden notices with the most labeled identification values (models + serial ranges + date codes + sold_at entries), ties broken by the same permutation. Only notices with at least one labeled model are eligible.
- Because the 15 test notices are inside the golden set, every table reports the baseline on all 40 and separately on the 25 notices outside the 15.

## Labels

- Produced blind in session 1, before any extractor code existed, by three labeling sub-agents that saw only the notice text and evals/extraction/LABELING_GUIDE.md. Status: model-labeled, unaudited. The owner audits them in M2 with evals/extraction/label.py.
- Every label value carries a verbatim quote; offsets into source_text are computed by code, not by the labeler.

## Metrics

Normalization for string comparison: Unicode casefold, collapse whitespace, strip surrounding punctuation. Models additionally ignore hyphens and spaces when matching (TL-1302Z equals TL1302Z) but the printed string is what gets stored.

| Field | Type | Metric |
|---|---|---|
| models | list | set F1 |
| serial_ranges | list | set F1 on (prefix, start, end) |
| date_codes | list | set F1 on (start, end) |
| sold_at | list | set F1 on retailer names |
| sold_from | scalar | exact match (None equals None) |
| sold_to | scalar | exact match |
| price_range_cents | scalar | exact match on (min_cents, max_cents) |
| units_affected | scalar | exact match |
| remedy_type | scalar | exact match |
| has_destruction_instruction | scalar | exact match (instruction present or absent) |
| registration_url | scalar | exact match after stripping scheme and trailing slash |

- Set F1 when gold and prediction are both empty counts as 1.0 for that notice; when exactly one is empty it is 0.0.
- Per-field score: mean over notices. Headline field accuracy: unweighted mean of the 11 per-field scores, in points (0 to 100).
- Span-grounded rate: share of predicted non-empty values whose evidence quote occurs verbatim in source_text.
- Kill rule (CLAUDE.md): if model-based headline minus baseline headline is 5.0 points or less, the model-based extractor is demoted to a fallback. On the M1 set the verdict is provisional; the M2 100-notice audited set decides.
- LLM_MODEL_ID and the extraction prompt hash used for the kill-deciding run are recorded in the results table header before the run.
