# Contract changes for the UI, payments, and login owners

CLAUDE.md ("Working in a team") requires a decisions-log entry and a message to the team before a contract change merges. This file is that message for the backend's M2 to M5 work (decisions log, 2026-10-18 and 2026-10-19). The contract itself is docs/contracts/openapi.json (regenerated, drift-tested) and docs/contracts/trace.md. Items marked **breaking** can break code written against the earlier contract.

## Everyone

1. **Breaking: dev portal tokens are `dev.<firm uuid>.<claim uuid>`** (they were `dev.<claim uuid>`). The firm is inside the token so the portal never needs the BYPASSRLS role. `POST /v1/programs/{id}/claims/issue` returns the full link; `make seed` claims use `dev.<demo firm>.<claim>`.
2. New services: `docker compose up -d` now starts Postgres and Neo4j (`graph`, bolt on 127.0.0.1:57687). Both bind to localhost only.
3. Write routes that were 501 are implemented: program create and patch, link issue, reviewer actions, portal submit, photos, answer, and payout (check), MPR get, generate, and PDF, and the feed pull. The only 501s left are the payments owner's (billing checkout and portal, the Stripe webhook, Connect onboarding from the portal).
4. `/readyz` gains `graph`: `ok`, `disabled`, or `unavailable`. The graph never makes the service unready; health-check deploys on `/healthz`.

4a. (2026-10-19) **Writes are committed before the response is sent.** Until now the API answered first and committed a moment later, so a read sent right after a write could return the old data, and a commit that failed after a 200 was invisible. Now a successful answer means the write is saved, and a failed commit answers 500. No code change is needed on your side; a re-read after a write is no longer required. Trace: docs/traces/M6-commit-before-response.md.
4b. (2026-10-19) **Every error is a problem detail.** An unexpected error now answers `application/problem+json` with status 500, a fixed `detail` ("unexpected server error; nothing was saved...") and the `correlation_id` (it was `text/plain` "Internal Server Error"). A 422 `detail` now reads as `field.path: message; ...` (it was a Python list repr). Show `detail` as text; do not parse it.
4c. (2026-10-19) **A backend test console** is served at `/dev/console` while the dev auth provider is active (never in prod, not in the contract). It calls every route; read its JS for a working request and response per screen. It is not the product UI.

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
19a. (2026-10-20, login owner) **The Clerk provider and HMAC claim links are registered.** With `AUTH_PROVIDER=clerk`, every `/v1` request sends `Authorization: Bearer <Clerk session token>` (`getToken()` in @clerk/nextjs); the active organization and role travel inside the token, never as a header or body field. 401: missing, expired, or unverifiable token. 403: no active organization, a role other than `org:admin` or `org:reviewer`, or a reviewer on an admin route. With `CLAIM_TOKEN_PROVIDER=hmac`, claim links are 96 URL-safe characters (`[A-Za-z0-9_-]`) and expire after `CLAIM_TOKEN_TTL_DAYS` (90). New settings: `CLERK_ISSUER`, `CLAIM_TOKEN_TTL_DAYS`. Requested schema change for the backend owner: migration `0007_login` (one SECURITY DEFINER function, `recallops_firm_for_clerk_org`), decisions log 2026-10-20.


## Payments integration (2026-10-20; built by the payments owner, adopted by the backend owner)

20. The four payment 501 handlers are replaced. Checkout/Customer Portal retain bodyless requests and `{url}`
    responses. Missing test configuration returns 503; unavailable Portal customer returns 409. Invalid webhook
    signature, live-mode event, or mismatched API version returns 400. Snapshot events use 2026-09-30.endive.
21. **Breaking for strict response consumers:** PortalView adds nullable `connect_onboarding_url` (only populated
    by the Connect payout POST; never stored). Usage adds plan, effective_plan, subscription_status, limits,
    invoices, billing_sync_error. PayoutView adds stripe_transfer_id and stripe_reversed_cents.
22. **Ledger protocol:** transition methods accept caller-owned `session`; webhook event/state/audit/processed_at
    now share that transaction. Payout states and lock ordering are preserved. Reversals are cumulative facts and
    alerts, never a new payable state. A timeout is not a failure that permits export.
23. **BillingGate:** `check_entitlement(ctx, action, claim_id=None)` may recognize an already-counted claim.
    `record_usage(ctx, claim_id, run_id, model_cost_usd, *, session=None)` deduplicates claims/month and cost/run.
    Runtime allow-all wiring is removed. Free exports refunds; the second unpaid invoice collection failure
    applies free access. Commercial plan is separate from effective access and Stripe status.
24. Migration `0006_payments` is the backend owner's, adopted 2026-10-20 (it was drafted by the payments owner for review).
25. UTC calendar months; one-program minimum; prorated initial month; only growth-time claim usage is billed.
    Growth waits in onboarding; existing pending_export rows cannot become Connect on upgrade. Product UI and
    reviewer-seat enforcement are explicit UI/login handoffs. Runtime verification remains pending; local checks
    are recorded separately in docs/traces/M4-payments-local-validation.md.

26. (Folded into item 24 at adoption.)
27. **Accounts v2 revision (owner-approved):** replace legacy Express creation/account.updated handling with
    Accounts v2 recipient configuration, Express dashboard, application fee/loss responsibility, and claimant
    contact email. Recipient Stripe transfer and payout capabilities must both be active. Thin account events
    resolve their related account through the stored ID, then retrieve current capabilities. Their separate
    endpoint secret is STRIPE_CONNECT_WEBHOOK_SECRET; Billing/transfer snapshot version/secret stay unchanged.
    No alternate Stripe-managed-liability funds flow or legacy account path is added.

28. The payments owner's runs against a Stripe test account are recorded in `docs/traces/M4-payments-stripe-sandbox.md`; runtime commands are in `docs/onboarding/payments-runtime.md`.
29. (Folded into item 24 at adoption.)

Changes the backend owner made while adopting the PR (2026-10-20):

30. **Rebuild any database migrated from the fork.** Migration 0006's trigger changed after some databases recorded it (it now also lets a pending or onboarding payout switch to a mailed check), and Alembic does not rerun a recorded revision. Run `alembic downgrade 0005` then `upgrade head`, or recreate the volume.
31. **Contract:** `POST /v1/programs` no longer lists 402 (a program is created as a draft; the plan is checked when it is opened with `PATCH status=open`). `POST /portal/{token}/payout` lists 400 (Stripe onboarding needs the claimant's email) and 402 (the firm's plan pays by check). The webhook lists 413, and `/v1/usage` and the billing routes list 404. A Stripe configuration error now answers 503 with "the payment configuration needs operator attention" instead of telling the caller to retry.
32. **Payouts:** a held Connect payout that a person re-approves goes back to `onboarding` when the claimant already linked an account (it went to `pending`, where nothing picks it up). An unreadable Stripe response is an unknown outcome and stays `transferring`. A refund that falls back to a mailed check after a confirmed Connect failure passes the same shared-address hold as a claimant's own check choice.
33. **Demo data:** `make seed` makes the demo firm `enterprise` (unlimited, never billed) so its 500 seeded claims do not block new ones. The other firm is `free`: use it to try Checkout.
34. **Settings:** an existing `.env` with the old placeholder `STRIPE_API_VERSION=2026-09-30.fake` keeps working (it means the pinned version); any other value is refused with a message to delete the line. The Stripe secret key and both webhook secrets are masked (SecretStr).
35. **Tests:** Stripe TEST account checks in `tests/manual/` are deselected by default (`-m 'not stripe_test'` in pyproject addopts); run them with `-m stripe_test` and the approval variables.

## Login integration (2026-10-20; built by the login owner in PR 8, adopted by the backend owner)

36. **Rebuild any database that applied the PR's migration 0007.** Its SECURITY DEFINER function now pins `search_path = pg_catalog` and names `public.firms`, and Alembic does not rerun a recorded revision. Run `alembic downgrade 0006_payments` then `upgrade head` (as recallops_owner), or recreate the volume.
37. **New route:** `POST /v1/claims/{claim_id}/link` (admin) returns `IssuedLink {claim_id, url}` with a fresh link and stops the previous one. 404: unknown claim or another firm's. 409: the program is closed, or the claim is paid or closed. Use it for a lost, leaked, or expired link, and at send time to fill a campaign draft's `{claim_link}` (drafts never store a token). Within the second of the last issue, and always under dev tokens, the same link comes back; the audit row (`claim_link_reissued`) records `rotated`.
38. **Auth answers:** 503 "sign-in verification unavailable" when Clerk's keys cannot be fetched: retry, do not sign the user out. A member whose role is neither admin nor reviewer gets 403 "your organization role has no access: ask an admin for org:admin or org:reviewer", distinct from "no active organization: create or select one"; no firm is created for them.
39. **Correction to 19a:** the 96 characters are the token; the issued link is `<PORTAL_BASE_URL>/<token>`. The API sends no CORS headers: the UI calls it through the Next.js server, so `CLERK_ALLOWED_ORIGINS` is only the allow-list for a token's azp.
40. **Settings:** `CLERK_JWKS_URL` is optional (empty derives `<CLERK_ISSUER>/.well-known/jwks.json`; another host is refused). `PORTAL_BASE_URL` must be an http(s) URL with no credentials, query, or fragment, and in prod an https, non-localhost one (both Render services need it). In prod `CLERK_ISSUER` must be the real https instance and `CLERK_ALLOWED_ORIGINS` must list https origins. `CLAIM_TOKEN_TTL_DAYS` is at most 90. `CLERK_SECRET_KEY` and `CLAIM_TOKEN_SECRET` are masked, and every base64 spelling of the example secret is refused. New: `SEED_CLERK_ORG_ID` (development) binds the seeded demo firm to a Clerk organization. Deploy runs uvicorn with `--no-access-log`, because a portal path is a bearer token.

## Website integration (2026-10-21; built by the UI owner in PR 9, adopted by the backend owner)

41. **Portal status:** `PortalView.status` never answers `flagged` or `capped`. A claim a person is deciding reads `under_review` to the claimant; the firm's routes still return the real status.
42. **Portal denial reasons** name only the rules the claim's latest run actually failed, read from its recorded check results (model mismatch, serial or date code out of range, bought before the sale window). A passing rule the decision also cited is no longer worded as a reason; with no failed rule the claimant gets the generic sentence.
43. **`Decision` gains five fields** (additive; item 6 promised them, but the schema dropped them): `hold_reason`, `reopened_from`, `duplicate_found_later`, `duplicates_ack`, `question_for_claimant`. The `hold_reason` values are `flagged`, `over_value_threshold`, `model_disagrees_with_rules`, `capped`, `failed`, `payout_held_needs_review`, and `shared_mailing_address`. Claim detail now shows why a claim waits for a person and which later claim reopened it.
44. **Program title:** a program created from a typed notice keeps that notice's title (it read "Manual recall" everywhere).
45. **Audit:** `claim_link_reissued` rows have `target_type` `claim` (they had `program`), so the claim's own audit trail shows each reissue.
46. **Contract:** `GET /v1/programs/{id}/claims` lists 400 (a cursor this API did not issue). Schema examples keep their null fields (`Program.closed_at`, `Trace.steps[].thought`): FastAPI dropped them while generating the document.
47. **Payment return URLs** default to the website: `BILLING_RETURN_URL=http://localhost:3000/app/billing/return` (a new page; the API refuses query strings in return URLs, and `?stripe=return` still works), `CONNECT_RETURN_URL=.../claim/connect-return`, `CONNECT_REFRESH_URL=.../claim/connect-refresh`.
48. **New setting, both sides:** `WEB_PROXY_SECRET` (API) and `RECALLOPS_WEB_PROXY_SECRET` (website), the same value of 32 or more characters. Portal calls reach the API from the web server, so every claimant shared its address and one per-IP rate-limit bucket: about 20 claimants an hour, or one person sending 300 requests, locked the portal for everyone. Now the website names the claimant's address in `X-RecallOps-Client-IP` next to the secret, and the API believes it only when the secret matches and the value is an IP address. The API refuses to start in prod without the secret; locally both stay empty and nothing changes.
49. **Website security:** dev auth answers only on a loopback `Host` (no LAN, no DNS rebinding), only against an API on this machine, and never on any Vercel deployment, previews included. `RECALLOPS_API_URL` must be https unless it is this machine. `next dev` binds to 127.0.0.1. Every page sends `frame-ancestors 'none'`, `X-Frame-Options: DENY`, and `nosniff`; claim pages also send `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex, nofollow` as headers.
50. **Website fixes from the review**, each with a test that fails without it:
    - **Portal:** bank transfer is no longer offered once a check is scheduled; blocked site data no longer stops the hand-off to Stripe; Send answer and Continue wait for photos still uploading; a stalled upload stops after 45 s with Try again; a model number is optional when the recall lists none; choosing a new photo drops the old one.
    - **Console:** the rules editor rebuilds remedy options to match a changed remedy; the close confirmation counts the claimants who lose access, and close is blocked while rule edits are unsaved; a queued review disables the actions, explains itself, and ends in a "still waiting" message; claim detail and the trace poll a submitted claim until its first run (every 2 s, up to 2 minutes); request-info notes stop at the backend's 500 characters with a counter; monthly report boxes start empty for each period; export totals say when they cover only the latest batches; "1 discrepancy" is singular.
51. **Visual system:** at the backend owner's request the console and portal were restyled to match the README graphics: brand blue (#2672cf light, #3987e5 dark), the system sans, and the README's shield mark. The tokens still live in `globals.css`; `copper` became `brand`, and the two Fontsource packages were removed.
52. **Tooling and deploy:** `make setup` also installs the website (`make web-setup`); new `make web` and `make web-check`. CI's web job adds the generated-types check and `npm run build`. `infra/render.yaml` adds `WEB_PROXY_SECRET` and two scheduled jobs (payment jobs every 10 minutes, the check export nightly). `docs/DEPLOY.md` is the go-live guide. `.github/CODEOWNERS` has the four real handles and `src/proxy.ts`.
