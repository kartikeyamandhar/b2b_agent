# Runtime trace: the first production deploy

Observed on 2026-10-21 (UTC) while the backend owner deployed RecallOps: the website on Vercel, the API, the worker, and two cron jobs on Render (Virginia), Postgres 17 and photo storage on Supabase (us-east-1), the fraud graph on Neo4j AuraDB, sign-in on a Clerk development instance, and Stripe in test mode. Times come from Render's deploy and job records, Clerk's organization record, database timestamps, and the commands' own clocks. Nothing below is intended behavior; it is what was recorded. All claims are synthetic.

## 1. Services come up

| time | event |
|---|---|
| before 22:00 | Supabase: three roles, the `recallops` database, pgvector 0.8.2, migrations to `0007_login` (29 tables plus 4 checkpointer tables; row-level security forced on 22) |
| 22:00:38 | Local preflight: each Render service's settings loaded with its real environment; both cron jobs failed (`unsafe fraud graph settings for APP_ENV=prod`) and `infra/render.yaml` was fixed before they were created |
| 22:01:19 | API service created; build started (commit 8c1a77f) |
| 22:01:53 | API build successful |
| 22:03:46 | `/healthz` 200, `"database":true`; `/readyz` 503 "no worker heartbeat" |
| 22:04:40 | API redeployed with the two Stripe webhook signing secrets |
| 22:09:58 | Worker deploy started (created once a payment card was on file; the first attempt answered HTTP 402) |
| 22:10:39, 22:10:41 | Both cron jobs live |
| 22:11:02 | Worker live |
| 22:11:13 | `worker_started`, `adjudicator: agent`, `variant: loop_full`, `llm_mode: live` |
| 22:11:14 | `graph_epoch_reset` on the empty `prod` namespace (expected on a first run) |
| 22:12:06 | Website production deployment READY |
| 22:13 (about) | `/readyz` 200: database true, worker heartbeat 0.8 s, graph ok |

## 2. First sign-in and the seed

| time | event |
|---|---|
| 22:14:09.991 | The owner's Clerk organization created |
| 22:14:11 | First console request: the API created an empty firm for the organization (migration 0007's function) |
| 22:14:59 | Seed, run 1, from the owner's machine with the worker's settings |
| 22:15:14 | Worker suspended by hand: the seed commits claims as submitted before deciding them, and the live agent worker could lease them and spend model money on demo data. Checked right after: 0 claims, 0 runs, 0.00 USD |
| 22:19:27 | Run 1 stopped: Supabase Storage answered HTTP 429 on a delete (about 3,000 storage calls in a burst). The claims transaction rolled back: 0 claims, 0 photos |
| 22:23:01 to 22:29:35 | Run 2, with a 429 retry added to the storage adapter: stopped on a photo upload's read timeout; rolled back again |
| 22:34:14 to 22:34:41 | Run 3: could not connect. From this machine the pooler took 8.2, 35.9, and 68.4 s to accept a connection, while the API on Render answered `/healthz` (database included) in 0.8 s |
| 22:54:11 to 22:56:02 | Fixes merged (PR #10, 8ae9a9f: storage 429 retry, the seed's lease on its claims, repeatable photo writes); worker resumed and deployed at 8ae9a9f; API and cron jobs deployed by hand (Render's auto-deploy did not fire for this repository) |
| 22:56:37 | Seed started as a one-off Render job on the worker service, next to the database, with the worker still running |
| 22:56:58 | Demo Recall Co and Other Firm Inc written; the demo firm took the organization's binding over from the empty firm |
| 23:00:03 | 505 claims committed with their photos (rendering and uploads ran inside that transaction) |
| 23:08:39 to 23:10:13 | All 505 decided by the rules-only adjudicator |
| 23:10:38 | Job succeeded (14 min 1 s): 500 demo claims, 1,414 photos, 58 fraud clusters, 505 rules-only runs, 0 claims still leased, 0.00 USD model spend |

The live agent worker ran throughout the successful seed and leased none of the seeded claims: the seed's lease (now in `recallops/seed.py`) hid each claim until its decision was recorded.

## 3. The homepage under Clerk

The first homepage deploy answered a browser's first visit to `/` with HTTP 307 to Clerk's development handshake (`x-clerk-auth-status: handshake`, reason `dev-browser-missing`), because the proxy matcher covered `/`. A browser returns to the page after the bounce; a link preview or crawler sees only the redirect. The matcher now excludes exactly `/`. Checked on a production build with a foreign Host: `/` 200 (proxy skipped), `/app/programs` and `/sign-in` 403 (proxy ran), `/claim/x` 200 (excluded as before).

## Not observed here

- A claim filed through the live portal and decided by the live agent (the next check, by the owner, from the console's "Issue claim links").
- A Stripe webhook delivery to the live API, and a Connect payout.
- Render's auto-deploy on push: until Render's GitHub app has access to this repository, deploys are triggered by hand.
