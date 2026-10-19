# M4 runtime trace: the check payout, the export batch, and the MPR

Observed on 2026-10-19 (UTC) right after docs/traces/M2-adjudication.md, on the same Trace Demo firm, API on port 8010 (dev auth). Claim C1 (`6d2dc0ae`) had been approved by a reviewer, its payout released from held to pending at 06:36:08.071. Rows come from the driver's request log and audit_log.

## Timeline

| ts (UTC) | source | event |
|---|---|---|
| 06:35:39.712 | audit | payout_created C1: pending, stripe_connect (growth plan), 5,600 cents |
| 06:36:02.126 | audit | payout_held (duplicate_found_later): C4 duplicated C1 |
| 06:36:08.071 | audit | payout_released to pending: a reviewer approved C1 |
| 06:36:41.872 | audit | payout_address_changed (stored as a keyed fingerprint `0a93a2d96c873775`, never the address) and payout_pending_export (method check_export): one transaction, claim row locked before the payout row |
| 06:36:41.883 | request | POST /portal/{token}/payout check_export with a PO box: 200, claim approved, payout pending_export |
| 06:36:41.891 | request | the same request again: 200, no change (already pending_export) |
| 06:36:41.894 | audit | payout_exported (batch 840a70a5) and claim_paid C1: one transaction, rows claimed with FOR UPDATE SKIP LOCKED |
| 06:36:41.904 | request | POST /v1/payouts/exports (admin): 200, 1 row, 5,600 cents, CSV sha256 7d6a7e6112ae... stored in export_batches |
| 06:36:41.908 | request | POST /v1/payouts/exports again: 200, null (nothing left to pay) |
| 06:36:41.912 | request | GET /v1/payouts/exports/840a70a5.csv: 200, header plus 1 row, the PO box present |
| 06:36:41.919 | request | GET /portal/{token}: claim paid, payout exported |
| 06:36:42.030 | audit | mpr_generated 2026-10, reconciled, manual box incidents_after |
| 06:36:42.122 | request | POST /v1/programs/{P}/mpr/generate?period=2026-10: 200, reconciled true; computed boxes: total units 53,634, corrections this period 1, cumulative 1, units with consumers 53,633, email contacts 5 |
| 06:36:42.129 | request | GET /v1/programs/{P}/mpr/{id}.pdf: 200, 2,736 bytes, %PDF-1.4 |

## What the trace shows

- The payout ledger followed the plan's state machine end to end: pending, held, pending (released by a person), pending_export, exported, and the claim moved to paid in the export's transaction.
- The batch is a database row written with the state change, so the download works on any disk; a second export run had nothing to claim.
- The MPR counted the one delivered refund once (corrections 1), the five portal submissions as contacts, and the independent recount agreed inside one REPEATABLE READ snapshot.
