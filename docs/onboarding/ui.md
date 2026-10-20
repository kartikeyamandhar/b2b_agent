# Onboarding: UI owner (component 2, apps/web)

You build the firm console and the claim portal in apps/web (02-ui.md). The backend keeps a local API with realistic data and a contract that only changes with a decisions-log entry and a note to you. The latest note is **docs/contracts/CHANGES.md**: read it first, several items affect screens you may have started.

For billing screens, upgrade prompts, claimant onboarding, payout status or export downloads, follow the
[payments handoff: UI owner](payments.md#ui-owner). It lists the implemented routes, redirect semantics,
new response fields and checks required before UI acceptance.

## What exists (backend M2 to M5, October 2026)

- Every route in 01-backend.md section 9 is implemented, in **docs/contracts/openapi.json** (generate your zod types and client from it: `npm run gen:api`; also live at http://127.0.0.1:8000/openapi.json, docs at /docs). Payment Checkout, Customer Portal, signed webhooks and claimant Connect onboarding are implemented in test mode; use the payments handoff for their integration.
- Console data: recalls search, programs (create from a recall with extracted rules, patch rules, threshold, and status), the claims queue (status filter, keyset cursor), claim detail (photos, decision, run summary, clusters, payout, audit), the trace (docs/contracts/trace.md), reviewer actions (`resume_override`, `request_info`, `mark_fraud`, `mark_delivered`), MPR (get, generate with manual boxes, PDF), payout export batches (create, list, CSV download), the fraud graph (rings across duplicate kinds, a 503 when the graph is down), campaigns (registrations upload, match, drafts, simulated send), and usage.
- Portal: view, photo upload, submit, needs_info answer, payout choice (mailed check or Stripe-hosted Accounts v2 onboarding).
- A claim filed through the portal is decided by the worker (`make worker`) a few seconds after submit. Poll `GET /v1/claims/{id}/trace` every 1 to 2 s while `run.status` is `running`. Flagged claims wait for a reviewer: an action is queued and the worker resumes the same run.
- `make seed` loads a demo firm with the real Scotch thermal laminator recall (CPSC 27024) and 500 synthetic claims decided by the rules-only adjudicator (182 approved, 94 denied, 107 needs_info, 117 flagged). The approved refunds' payouts then move through the ledger, so the statuses you see after seeding are: paid 110 (56 exported in one export batch, 54 transferred), approved 72 (payouts 36 pending, 18 onboarding, 18 failed), denied 94, needs_info 107, flagged 117. Paid claims, an export download, and a non-zero MPR exist. 58 fraud clusters come from the Neo4j graph. A second firm has 5 claims, for tenant isolation.

## The seam

- **Auth in development.** Send `X-Dev-Firm-Id` and `X-Dev-Role` (`admin` or `reviewer`) headers; the login owner's `apiFetch` switches to a Clerk bearer token later and nothing else in your code changes.
  - Demo firm `00000000-0000-4000-8000-00000000d001`; other firm `00000000-0000-4000-8000-00000000d002`.
- **Stable ids across reseeds.** Demo program `0874f66f-bfb5-552b-bd93-6266f49c1d5a`; first claim `66dcc492-086f-5b59-b441-cbe001eb2e0b`.
- **Portal tokens in development** are `dev.<firm id>.<claim id>`, for example `/portal/dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b`. The login owner replaces them with signed tokens.
- **Errors** are RFC 7807 problem details with `detail` and `correlation_id`; show both. Another firm's resource is a 404, never a 403.
- **Photos.** `photos[].url` is an API path in every environment (send the same auth headers). The API reads the bytes from storage after checking the firm (local disk in development, a private Supabase bucket in production), so no storage URL ever reaches the browser; treat the path as opaque.
- **Money** is integer cents; costs are decimal strings in USD. Payout status can be `transferring` or `held` (CHANGES.md item 5).

- **A working reference, not your app.** With the backend running in development, http://127.0.0.1:8000/dev/console is a single-file test console (apps/api/src/recallops/api/static/dev_console.html) that calls every route in the contract with the dev headers, including the portal's photo upload. Read its JS to see a request and response for any screen you build. It is served only with the dev auth provider, it is not in the contract, and it does not replace apps/web.

## Run the backend locally

Prerequisites: Docker, uv 0.11.21, Python 3.13. A fresh machine pulls `pgvector/pgvector:0.8.6-pg16` and `neo4j:2026.09.0-community` once.

```
cp .env.example .env
docker compose up -d     # Postgres and Neo4j
make setup               # pinned deps, roles, databases, migrations
make seed                # about 45 s
make api                 # http://127.0.0.1:8000, docs at /docs
make worker              # optional: decides portal claims (ADJUDICATOR=rules_only runs offline at no cost)
```

## Tests that must stay green

Backend CI (`.github/workflows/ci.yml`, job `backend`) runs on every pull request, including `test_committed_openapi_matches_the_app`. Your job (`web`, vitest and `tsc --noEmit`) is in ci.yml and switches on when you commit `apps/web/package-lock.json`; it reports skipped until then.

## Three commands to verify your work

```
make seed && make api                                                         # realistic local data
curl -s -H 'X-Dev-Firm-Id: 00000000-0000-4000-8000-00000000d001' \
  'http://127.0.0.1:8000/v1/programs/0874f66f-bfb5-552b-bd93-6266f49c1d5a/claims?status=flagged&limit=5'  # the queue your screen renders
cd apps/web && npm run gen:api && npx vitest run                              # types from the contract, your tests
```
