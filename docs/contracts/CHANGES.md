# Contract changes for the UI, payments, and login owners

CLAUDE.md ("Working in a team") requires a decisions-log entry and a message to the team before a contract change merges. This file is that message for the backend's M2 to M5 work (decisions log, 2026-10-18 and 2026-10-19). The contract itself is docs/contracts/openapi.json (regenerated, drift-tested) and docs/contracts/trace.md. Items marked **breaking** can break code written against the earlier contract.

## Everyone

1. **Breaking: dev portal tokens are `dev.<firm uuid>.<claim uuid>`** (they were `dev.<claim uuid>`). The firm is inside the token so the portal never needs the BYPASSRLS role. `POST /v1/programs/{id}/claims/issue` returns the full link; `make seed` claims use `dev.<demo firm>.<claim>`.
2. New services: `docker compose up -d` now starts Postgres and Neo4j (`graph`, bolt on 127.0.0.1:57687). Both bind to localhost only.
3. Write routes that were 501 are implemented: program create and patch, link issue, reviewer actions, portal submit, photos, answer, and payout (check), MPR get, generate, and PDF, and the feed pull. The only 501s left are the payments owner's (billing checkout and portal, the Stripe webhook, Connect onboarding from the portal).
4. `/readyz` gains `graph`: `ok`, `disabled`, or `unavailable`. The graph never makes the service unready; health-check deploys on `/healthz`.

## UI owner (component 2)

5. **Breaking for strict enums: payout status** gains `transferring` (component 3 is sending a Stripe transfer) and `held` (the claim left approved before money moved: a reviewer override, or a duplicate found later). Both appear in `PayoutView.status` and `PortalView.payout_status`. Treat unknown values as "in progress"; show `held` as "on hold, a person is reviewing".
6. **Claim statuses now used**: `paid` (a refund reached transferred or exported), `closed` (a repair or replacement marked delivered). A claim can go back to `submitted` when a duplicate turns up after its decision: its `decision` keeps the earlier outcome plus `reopened_from` and `duplicate_found_later` (claim ids), and the worker decides it again. A claim can be `under_review` with `decision.hold_reason` of `payout_held_needs_review` or `shared_mailing_address`; its run is `done`, so show the reviewer actions from the claim status, not only from `run.status == waiting_human`. Reviewer decisions carry `decision.duplicates_ack`.
7. `ClaimAction.action` gains `mark_delivered` (approved repair or replacement only; 409 otherwise).
8. `Cluster.kind` gains `same_mailing`. Clusters are now connected components computed by the Neo4j projection, not pairs: a row grows as claims join and keeps its oldest id; a row can disappear when a photo is replaced. Do not deep-link to a cluster id as if it were permanent.
8a. Campaign drafts store the placeholder `{claim_link}` instead of the owner's link: the link holds the claim token, which the system keeps only as a hash (invariant 10). A real sender substitutes a link issued at send time; sending is simulated today. Registration rows must carry a synthetic marker (an example.com/.net/.org email or a 555-01xx phone); a row with neither is rejected with "no synthetic marker".
9. New routes:
   - `GET /v1/programs/{id}/fraud-graph?limit=`: `{nodes: [{claim_id, status, cluster_kinds}], edges: [{a, b, kind}], rings: [[claim ids]], truncated}`. A 503 with the fixed detail "fraud graph unavailable" when the graph is down; keep the rest of the page working.
   - `POST /v1/payouts/exports` (admin; null when nothing is waiting), `GET /v1/payouts/exports`, `GET /v1/payouts/exports/{id}.csv` (admin; `text/csv`).
   - `POST /v1/programs/{id}/registrations` (admin, multipart CSV; row-level rejections listed), `POST .../campaign/match`, `POST .../campaign/drafts` `{channels}`, `POST .../campaign/send` `{channel?}`, `GET .../campaign`. Sending is simulated; say so in the UI.
10. `POST /v1/programs/{id}/mpr/generate` takes an optional JSON body of manual boxes (`MprManual`). The report payload now has `boxes` (what the form shows), `computed`, `manual`, `notes`, and `reconciliation`. `period` must be `YYYY-MM` with a real month (422 otherwise).
11. Responses newly documented: 402 (over the plan's limit) on program create and patch, 409 on patch, issue, actions, and the portal payout step, 413 on the registrations upload.
12b. `POST /portal/{token}/payout` answers 409 "the mailing address needs letters or digits a US carrier can read" for an address with no ASCII letter or digit, and the shared-address hold now also fires when the check goes to the claimant's home address and another claim uses that address as its mailing address.
12a. Photos: `GET /v1/claims/{id}/photos/{photo_id}` and `POST /portal/{token}/photos` can answer 503 ("photo storage unavailable"; nothing was saved on upload). The photo URL stays an API path in production: the API reads the private bucket after the firm check.
12. Portal: `POST /portal/{token}/payout` with `check_export` can return `under_review` and `payout_status: held` when the mailing address is one another claim of the program uses. `POST /portal/{token}/answer` answers 409 when the claim is no longer waiting for an answer (instead of silently dropping the reply). Claimant text is cleaned on submit: control characters removed, and a value with no letter or digit (for example a serial of "-") becomes empty.

## Payments owner (component 3)

13. **Breaking: `PayoutLedger` protocol.** `create_pending(ctx, claim_id, amount_cents, method, session=None)` takes an optional session (the backend creates the payout inside the approval's transaction). New `claim_for_transfer(ctx, payout_id)`: call it under the row lock immediately before `stripe.Transfer.create`; it moves the row to `transferring` and refuses (`NotPayable`) unless the claim is approved and the payout is pending, onboarding, or failed. `mark_transferred` is legal only from `transferring` (or from `held`, as a late result that is recorded with an alert, never refused). `mark_onboarding` stores the Connect account id; `mark_exported` stores the batch id.
14. Holds: a hold that lands while a row is `transferring` sets `hold_requested`; if the transfer then fails, the row goes to `held`. Only a re-approval of the claim releases a hold, and never after money moved. Every money path locks the claim row before the payout row; keep that order in the webhook.
15. Export batches are rows in `export_batches` (CSV bytes, sha256, totals), written in the same transaction that marks the payouts exported and the claims paid. The nightly export is `make export-payouts`; there is no file to recover.
16. Correction to the onboarding text: the webhook route runs as `recallops_app` like every API route (tests/api/test_security.py fails if API code uses the internal engine). Use the SECURITY DEFINER functions from migration 0003: `recallops_record_stripe_event(id, type, payload)` (returns false on a replay), `recallops_firm_by_stripe_customer(customer)`, and `recallops_mark_stripe_event_processed(id)`.

## Login owner (component 4)

17. **Breaking: `ClaimTokenVerifier`** returns `ClaimTokenContext(firm_id, claim_id)` from `verify`, and has `issue(firm_id, claim_id) -> str`. Your HMAC token must carry the firm id.
18. `AUTH_PROVIDER=clerk` and `CLAIM_TOKEN_PROVIDER=hmac` must ship together: the dev claim-token verifier refuses to load when `AUTH_PROVIDER=clerk`, and the dev auth provider refuses `APP_ENV=prod`. infra/render.yaml deploys with `APP_ENV=prod`, so the API fails closed until both of yours are registered in `auth/deps.py`.
19. The rate-limit table exists (`rate_limits`, migration 0003) with a fixed-window limiter in `api/guards.py`; you can replace the limiter, the table stays.
