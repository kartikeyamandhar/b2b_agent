# Reporting eval: MPR reconciliation

Generated 2026-10-19 05:39 UTC by `make eval`. Scenario: 500 claims, 261 payouts, 193 delivered refunds across August to October 2026.

The SQL report (mpr/generate.py) against the independent Polars recount over row exports (mpr/recount.py), zero tolerance.

| period | corrections (report / recount) | cumulative (report / recount) | email contacts (report / recount) | discrepancies |
|---|---|---|---|---|
| 2026-08 | 18 / 18 | 18 / 18 | 148 / 148 | 0 |
| 2026-09 | 66 / 66 | 84 / 84 | 168 / 168 | 0 |
| 2026-10 | 60 / 60 | 144 / 144 | 143 / 143 | 0 |

**Reconciliation: PASS** (0 discrepancies).
