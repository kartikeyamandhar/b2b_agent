# Reporting eval: MPR reconciliation

Generated 2026-10-19 07:51 UTC by `make eval`.

Scenario: 500 refund claims (256 payouts through every ledger state, held ones included, 1 replayed delivery event), 40 repair and replacement claims (25 marked delivered), 30 claims answered the month after their first submission, 2 deliveries on a month boundary instant, and 5 deliveries in another program of the same firm (must not count); campaign notices: 46 sent, 5 drafts never sent, and 2 in the other program.

Three counts per computed box: the SQL report (mpr/generate.py), the independent Polars recount over row exports (mpr/recount.py), and the truth the scenario builder kept. Zero tolerance.

| period | corrections (report / recount / truth) | cumulative | units with consumers | email contacts | notified email / mail / text | boxes entered by hand | discrepancies |
|---|---|---|---|---|---|---|---|
| 2026-08 | 19 / 19 / 19 | 19 / 19 / 19 | 53615 / 53615 / 53615 | 161 / 161 / 161 | 6 / 6 / 6; 6 / 6 / 6; 8 / 8 / 8 | none | 0 |
| 2026-09 | 87 / 87 / 87 | 106 / 106 / 106 | 52528 / 52528 / 52528 | 188 / 188 / 188 | 8 / 8 / 8; 3 / 3 / 3; 6 / 6 / 6 | units_with_retailers=1000, incidents_after=2 | 0 |
| 2026-10 | 68 / 68 / 68 | 174 / 174 / 174 | 53460 / 53460 / 53460 | 153 / 153 / 153 | 1 / 1 / 1; 6 / 6 / 6; 2 / 2 / 2 | contacts_email=999 | 0 |

A contacts_email entered by hand replaces the box on the form and is noted in the report; reconciliation compares the computed value.

**Reconciliation: PASS** (0 discrepancies; scenario composition as pre-registered: yes).
