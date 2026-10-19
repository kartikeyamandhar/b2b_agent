# M5 runtime trace: the Neo4j projection after decisions, and a notification campaign

Observed on 2026-10-19 (UTC) on the Trace Demo firm (`00000000-0000-4000-8000-0000000007a1`, program `bdef6511`), with Neo4j 2026.09.0 Community and Postgres 16.15 in docker compose, the API on port 8010, and the worker run as `python -m recallops.claims.worker --once` with ADJUDICATOR=rules_only (offline, no model calls). Rows come from the worker's JSON-lines log, audit_log, fraud_clusters, and the API. The money-safety half of the duplicate story (an approved claim reopened and its payout held when a later duplicate arrives) was observed with the live agent in docs/traces/M2-adjudication.md; this trace covers the projection.

## A duplicate pair through the worker and the graph

An earlier, interrupted run of this driver had filed two claims with the same photo seed (8101) and addresses 11 and 12 Oak St. So the pair filed here (A, then B, same seed and addresses) matched those two as well, which shows how a cluster grows.

| ts (UTC) | source | event |
|---|---|---|
| 06:54:57.818 | audit | claim_submitted A |
| 06:54:58.454 | audit | claim_held A flagged (check_duplicates: same photos and address as the two earlier claims) |
| 06:54:58.466 | worker | worker_claim_done A flag, 35 ms |
| 06:54:58.495 | worker | graph_sync_claim A synced at graph_version 4 in 29 ms: 1 cluster row inserted (same_address), 1 updated (same_photo), 0 ids dropped |
| 06:54:58.727 | audit | claim_submitted B |
| 06:54:59.368 | audit | claim_held B flagged |
| 06:54:59.379 | worker | worker_claim_done B flag, 35 ms |
| 06:54:59.405 | worker | graph_sync_claim B synced in 26 ms: 1 inserted (same_address with the earlier 12 Oak St claim), 1 updated (same_photo) |

Resulting fraud_clusters rows for A: same_photo with 4 members (A, B, and the two earlier claims), score 1.0, still the row created at 06:54:45.383 (id 75a94b69): a growing component keeps its oldest id. same_address with 2 members. GET /v1/claims/{A} and /{B} both list cluster 75a94b69, and GET /v1/programs/{P}/fraud-graph returns one ring of the 4 claims, with statuses read from Postgres.

Order observed on every claim: the decision's transaction commits (claim status, audit, payout effects), the worker logs the claim done, then the projection runs: about 25 to 30 ms later on a local graph. The projection never changed a claim's status or a payout; it wrote only fraud_clusters rows and claims.graph_synced_version.

## A photo re-upload while synced (observed by the graph package's tests, local run)

| ts (UTC) | event |
|---|---|
| 06:36:30.961 | claims a and b inserted, both graph_version 1, unsynced |
| 06:36:31.049 to .143 | a projected and synced |
| 06:36:31.150 to .220 | b projected and synced: one 2-member same_photo cluster |
| 06:36:31.223 | b's photo replaced (needs_info): the trigger moves b to graph_version 2, so b is dirty |
| 06:36:31.251 to .263 | b re-projected: its old relationships deleted first, synced at version 2, the cluster removed |

## A campaign from a synthetic registrations CSV to the MPR

| ts (UTC) | request | result |
|---|---|---|
| (before) | `python -m recallops.synth --registrations owners.csv --program P --n 40 --seed 7` | 40 synthetic owners (example.* emails, 555-01xx phones) |
| 06:55:00.106 | POST /v1/programs/{P}/registrations | 40 accepted, 0 rejected |
| 06:55:00.111 | POST .../campaign/match | 36 matched (recalled model), 4 unmatched (another model) |
| 06:55:00.190 | POST .../campaign/drafts email, mail, phone_text | 36 drafts per channel; 36 claim links issued, one per matched owner |
| 06:55:00.195 | POST .../campaign/send email | 36 marked sent (simulated: nothing left the system) |
| 06:55:00.254 | POST .../mpr/generate 2026-10 | reconciled; notified_email 36, notified_mail 0, notified_phone_text 0 (drafted, not sent) |

The email draft quotes the notice: "Hazard (from the recall notice): The recalled thermal laminators can overheat, posing a fire hazard." and the REMEDY section verbatim, then the owner's own claim link.

## Seed timing

`make seed` rebuilds both seed programs through `sync_program`: 500 demo claims projected and clustered in 512 to 570 ms on three runs (58 cluster rows: 14 same_photo, 13 same_address, 17 same_serial, 14 same_payout). The first seed after the graph was wired took 74.8 s in that step; it did not reproduce on two later runs, one of them right after a full test run. The cause is not known; a cold Neo4j query cache on the first projection into the dev namespace is the leading guess, unverified.
