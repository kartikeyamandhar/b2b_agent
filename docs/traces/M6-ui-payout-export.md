# Runtime trace: creating and downloading a payout export batch in the console

Observed on 2026-10-21 in Chrome against the production build of apps/web (`next start`, Next 16.4.2, dev auth as an admin of the demo firm) and the local API (uvicorn, Postgres 16.15). Browser times come from an in-page recorder that wrapped `fetch` for `/api/*` and polled the DOM every 20 ms; API times are the API's own log lines, joined by the `x-request-id` the web proxy sets. Same machine, UTC. Ids are shortened to 8 characters; no request or response body or CSV content was recorded. Nothing below is intended behavior; it is what was recorded.

## What the batch would change, checked before the click

A read-only query listed every payout in `pending_export` whose claim is `approved` (the rows `payouts/export.py` claims): exactly one, payout `dd717e07`, 5,600 cents, method `check_export`, claim `2881c056` in the demo firm. That claim was a synthetic portal claim filed during the UI's live test earlier the same day (example.com email, 555-01xx phone), not seeded demo data. Before: 56 exported payouts in the firm and 1 export batch (the seed's).

## Create the batch

| t (ms) | UTC | where | event |
|---|---|---|---|
| 0.2 | 17:31:45.088 | browser | recorder attached on `/app/payouts` |
| 4473.1 | 17:31:49.561 | browser | "Create export batch" clicked: inline confirmation shown (marks payouts exported and claims paid) |
| 12897.3 | 17:31:57.985 | browser | "Create batch" clicked: → `POST /api/v1/payouts/exports` |
| 12907.4 | 17:31:57.995 | browser | Create batch disabled, "Creating…" |
| | 17:31:58.230 | API | `payout_state_changed` → `exported` |
| | 17:31:58.246 | API | `payout_export_written`, rows 1 |
| | 17:31:58.264 | API | `http_request POST /v1/payouts/exports` 200, 206.0 ms (`1567503b`) |
| 13181.3 | 17:31:58.269 | browser | ← 200 (an `ExportBatchSummary`, not null) |
| 13200.9 | 17:31:58.289 | browser | confirmation removed; success panel "Batch created: 1 payout, $56.00." with Download CSV |
| 13301.6 | 17:31:58.390 | browser | the new batch's row appears in Batch history (server re-render after `router.refresh()`) |

The state change, the audit rows, and the batch row commit in one transaction before the API answers: the payout log line precedes the response by 34 ms. The success panel is drawn from the POST's own answer; the history row follows 100 ms later from the page's server re-render.

## Download the CSV

| t (ms) | UTC | where | event |
|---|---|---|---|
| 15507.0 | 17:32:00.595 | browser | → `GET /api/v1/payouts/exports/562584ea….csv` (the link's own URL, through the authenticated proxy) |
| | 17:32:00.660 | API | `http_request GET` 200, 25.8 ms (`4083f476`) |
| 15578.7 | 17:32:00.667 | browser | ← 200, `text/csv` |

The file held its header and 1 data row, matching the batch's `row_count`, and its sha256 (`947673dc9ae2…`) equals the fingerprint the page lists for the batch.

## After

Database: payout `dd717e07` is `exported` with batch `562584ea`; its claim moved from `approved` to `paid`; no payout is left in `pending_export`; the firm's exported payouts went from 56 to 57 and export batches from 1 to 2 (`created_by` `dev-user`). No other payout or claim changed.

The success message read "1 payouts" when this was recorded; it was corrected to "1 payout" in apps/web (commit `efc921c`).
