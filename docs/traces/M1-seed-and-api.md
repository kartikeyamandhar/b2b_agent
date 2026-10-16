# M1 runtime trace: make seed and the read API

Observed on 2026-10-16 against local Docker Postgres 16.14. Timestamps are UTC from the JSON-lines logs of the seed process and the API server (`http_request` events). Nothing below is intended behavior; it is what the logs show.

## make seed (second run, proving idempotency)

Command: `make seed`. The first run (22:44) and this second run produced the same program id (`0874f66f-bfb5-552b-bd93-6266f49c1d5a`), the same first claim id (`66dcc492-086f-5b59-b441-cbe001eb2e0b`), and the same status counts, because every id derives from fixed namespaces and the generator's RNG streams are seeded by sha256.

| ts | event | detail |
|---|---|---|
| 23:01:42.683 | feed_run_started | fixture load, run efb639a9 |
| 23:01:42.750 | feed_run_finished | 51 fetched, 0 inserted, 51 unchanged, 66 ms |
| 23:01:42.75 to 23:02:19.72 | (no per-claim events) | seed firms wiped and recreated; baseline rules extracted for 27024 and cached on the recalls row; 500 claims generated (label, destruction, and receipt photos rendered, pHash separation pass); claims and claim_photos inserted in one transaction; photo bytes written to STORAGE_DIR before their hashes were recorded |
| 23:02:19.724 | agent_run_started | first rules_only run (run cac20d49) |
| 23:02:19.738 | claim_adjudicated | first claim: deny, 13.73 ms including 7 agent_steps writes |
| 23:02:26.084 | claim_adjudicated | last demo claim: deny, 9.05 ms |
| 23:02:26.096 | seed_program_done | demo firm: 500 claims adjudicated in 6,364 ms |
| 23:02:26.315 | seed_program_done | other firm: 5 claims in 49 ms |

Totals: 43.73 s wall clock, 5,237 log events, 3,535 agent_step events (500 x 7 + 5 x 7), 182 `payout_created_pending` events from the FakeLedger (approved refunds under the value threshold). Final statuses for the demo firm: 182 approved, 94 denied, 107 needs_info, 117 flagged; 59 duplicate clusters.

Ordering observed: each claim's agent_runs row is committed before its first step; each step is its own transaction, so a crash mid-claim leaves the completed steps visible with the run still `running`; the claim's status and decision are updated, and its audit row written, only after the run's `decide` step and `finish`.

## Read API session

Command: `make api`, then the seven requests below with explicit `X-Request-Id` headers so each log line can be matched.

| ts | correlation id | request | status | server ms |
|---|---|---|---|---|
| 23:01:30.264 | trace-01 | GET /v1/programs (demo firm) | 200 | 28.3 |
| 23:01:30.280 | trace-02 | GET /v1/programs/{P}/claims?status=needs_info&limit=5 | 200 | 5.6 |
| 23:01:30.302 | trace-03 | GET /v1/claims/{C} | 200 | 13.6 |
| 23:01:30.320 | trace-04 | GET /v1/claims/{C}/trace | 200 | 5.8 |
| 23:01:30.336 | trace-05 | GET /v1/claims/{C} with the other firm's header | 404 | 4.5 |
| 23:01:30.372 | trace-06 | GET /portal/dev.{C} (no firm header) | 200 | 28.2 |
| 23:01:30.382 | trace-07 | POST /v1/claims/{C}/actions | 501 | 1.0 |

What each step does, in order, for a tenant route: the dev provider reads the headers (401 on a malformed firm id, 403 without one), the route opens a transaction as `recallops_app`, runs `set_config('app.firm_id', <firm>, true)`, and the repository query adds `firm_id = <firm>`. For trace-05 both guards apply: the repository filter returns nothing and RLS would hide the row anyway, so the answer is 404 (never 403, so existence is not leaked). The portal route (trace-06) resolves the token's claim through the internal role, then reads it under that claim's firm context. The 501 (trace-07) is answered by the exception handler before any database work, with the problem detail naming milestone M3. Every response carries the correlation id back in `x-request-id`.

Earlier in the same session, before a fix: `GET /v1/claims/{C}` returned 500 because the generic SQLAlchemy ARRAY type has no `contains()`; the models now use the PostgreSQL ARRAY type, and `tests/api/test_api.py::test_claim_detail_trace_and_photo` covers the route.
