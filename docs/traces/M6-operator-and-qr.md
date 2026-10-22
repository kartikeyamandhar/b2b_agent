# M6 trace: the operator by email, and QR codes for claim links (2026-10-22)

What changed (CHANGES.md item 55): the platform operator is any account with a verified address in
`PLATFORM_OPERATOR_EMAILS`, read from Clerk's Backend API; the operator passes the console gate, and the organization
they act in is approved on request. Claim links show as QR codes and print on sheets.

## 1. The operator check, observed

Run: the real app with Clerk session tokens (the end-to-end harness of `tests/api/test_clerk_api.py`) against the test
database, `ACCESS_APPROVAL=required`, `PLATFORM_OPERATOR_EMAILS=kartikeyamandhar777@gmail.com`. Clerk's Backend API is
a local stub that answers in 150 ms and returns the operator's address in Google's dotted form
(`kartikeya.mandhar777@gmail.com`); then it goes down, failing after 400 ms. One line per request, as printed by the
run (2026-10-22, 02:25 UTC):

| t (ms) | request | answer | took (ms) | Clerk lookups |
|---:|---|---|---:|---:|
| 1.6 | operator, no organization: `GET /v1/operator/access` | 200 | 175.0 | +1 |
| 176.6 | the same again | 200 | 6.8 | +0 (kept 60 s) |
| 183.4 | operator in a new organization: `GET /v1/access` | 200 | 12.9 | +0 |
| 196.3 | operator in that unapproved organization: `GET /v1/programs` | 200 | 30.2 | +0 (the gate lets the operator through) |
| 226.5 | company user: `GET /v1/access` | 200 | 167.6 | +1 |
| 394.1 | company user, not approved: `GET /v1/programs` | 403 | 5.8 | +0 |
| | Clerk goes down; caches cleared to show a cold start | | | |
| 399.9 | company user: `GET /v1/programs` | 403 | 407.9 | +1, failed |
| 807.9 | the same within 15 s | 403 | 6.3 | +0 (the failure is kept 15 s) |
| 814.2 | operator: `GET /v1/operator/access` | 403 | 407.2 | +1, failed: fails closed |
| 1221.4 | the same within 15 s | 403 | 1.2 | +0 |

The two failed lookups logged `clerk_user_lookup_failed` with `duration_ms` 401 and 405. Before the review, a failure
was not kept, so every request during a Clerk outage paid the full wait; `GET /v1/access` resolves the operator before
the tenant session opens, so no database connection waits on Clerk.

## 2. Requests made at the same moment

Observed in `tests/api/test_recall_access.py` (both fail without the fix, with 409 instead of 201):

1. A second open request for the same recall is inserted on another connection and committed 0.5 s later, while the
   operator's `POST /v1/programs` waits on the unique index. The operator's insert fails, the grant re-reads the request
   that won, approves it, and the program opens: 201, one approved row. Before: 500.
2. The operator's open request is approved by another connection that commits 0.5 s later, while this
   `POST /v1/programs` waits on the row lock. The decision function sees `approved` and refuses the move; the grant
   re-reads the row, finds it approved for this organization, and the program opens: 201. Before: 409 "another
   organization runs this recall" about the operator's own organization.

## 3. QR codes, scanned

The app's own `src/lib/qr.ts` drew codes for six claim links in the live format (`https://recallops-five.vercel.app/claim/`
plus a 96-character token, 49 modules) and one local dev link (41 modules), each at the console size (3 px per
module, 171 px) and at the 2 px per module minimum (98 to 114 px). Headless Chrome rasterized each SVG and macOS's
built-in QR detector (CoreImage) decoded it: 14 of 14 decoded to their exact link. The printed sheet renders three
codes to a row with the claim, "Scan to file your recall claim", and the link spelled out.

## 4. The website's routes, observed

The production build in dev auth against the local API (2026-10-22, 02:53 UTC), as the operator unless noted:

| request | answer | time |
|---|---|---:|
| `GET /operator` | 200, the queue ("Organizations asking to run a recall") | 0.204 s |
| `GET /app/operator` | 307 to `/operator` | 0.022 s |
| `GET /app/nonexistent-page` | 404, the console's own not-found page inside the shell | 0.016 s |
| `GET /icon.svg`, `GET /apple-icon.png` | 200, `image/svg+xml` and `image/png`; every page links both | 0.007 s, 0.003 s |
| `GET /operator` as a company user | 200, "Only the platform operator can open this page." | |

A signed-in user with no organization is redirected to `/organization` before any API call (the queue check had held
that redirect for up to the API timeout; `tests/clerk-integration.test.tsx` counts zero checks). `/organization` and the
no-access view stream the operator link with a 3 s deadline, so they never wait on a sleeping API.
