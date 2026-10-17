# Adjudication eval

Generated 2026-10-17 00:54 UTC by `make eval`. Pre-registration: evals/adjudication/PREREG.md.

- Golden claims: 300 synthetic claims from 20 golden notices (gold rules), planted violations, expected outcomes from outcome_policy.yaml (decided 2026-10-17). The 60 owner-reviewed and 40 classmate claims arrive in M2.
- Rules-only decision table 1.0.0; pure-code tools; destruction photo presence only (no vision).
- **Kill criterion not evaluable in M1:** there is no agent row yet. It compares macro F1 below with the agent's in M2.
- The first run (tools 1.0.0) flagged 44 legitimate claims: all 82 false duplicate hits were receipt photos matched by pHash, which ignores the text that distinguishes template documents. Tools 1.0.1 match receipts on exact bytes only (decisions log). Both rows are shown; 1.0.1 is the baseline going forward.

| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed to human | mean steps | mean cost USD | p50 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rules_only (tools 1.0.1, receipts sha256 only) | 300 | 0.987 | 0.982 | 0.987 | 1.000 | 1.000 | 1.000 | 0.253 | 7.0 | 0.0000 | 0.0001 |
| rules_only (tools 1.0.0, first run, pHash on receipts) | 300 | 0.843 | 0.850 | 0.920 | 0.621 | 1.000 | 1.000 | 0.393 | 7.0 | 0.0000 | 0.0001 |
| rules_plus_vision | | M2 | | | | | | | | | |
| single_pass, loop_* | | M2 | | | | | | | | | |

Per class:

| class | precision | recall | F1 |
|---|---|---|---|
| approve | 0.9623 | 1.0 | 0.9808 |
| deny | 1.0 | 1.0 | 1.0 |
| needs_info | 1.0 | 0.8974 | 0.9459 |
| flag | 1.0 | 1.0 | 1.0 |

Per planted violation (share of claims carrying it whose outcome matched the expected outcome):

| violation | n | correct | recall |
|---|---|---|---|
| date_code_out_of_range | 17 | 17 | 1.000 |
| destruction_not_shown | 12 | 8 | 0.667 |
| destruction_not_shown:intact | 5 | 1 | 0.200 |
| destruction_not_shown:label_reupload | 4 | 4 | 1.000 |
| destruction_not_shown:missing | 3 | 3 | 1.000 |
| duplicate_address | 8 | 8 | 1.000 |
| duplicate_payout | 24 | 24 | 1.000 |
| duplicate_photo | 14 | 14 | 1.000 |
| duplicate_photo:exact | 6 | 6 | 1.000 |
| duplicate_photo:near | 1 | 1 | 1.000 |
| duplicate_serial | 26 | 26 | 1.000 |
| no_receipt_when_required | 2 | 2 | 1.000 |
| purchase_before_window | 45 | 45 | 1.000 |
| retailer_not_listed | 43 | 43 | 1.000 |
| serial_out_of_range | 16 | 16 | 1.000 |
| wrong_model | 40 | 40 | 1.000 |

Confusion (expected -> predicted counts):

```
{
 "approve": {
  "approve": 102
 },
 "deny": {
  "deny": 87
 },
 "needs_info": {
  "needs_info": 35,
  "approve": 4
 },
 "flag": {
  "flag": 72
 }
}
```
