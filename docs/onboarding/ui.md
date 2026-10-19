# Onboarding: UI owner (component 2, apps/web)

You build the firm console and the claim portal in apps/web (02-ui.md). The backend owner keeps a local API with realistic data and a fixed contract, so you can build every screen before the M2 write routes exist.

## What changed on 2026-10-18 (M2 backend; additive, no field removed)

- Write routes now work: `POST /v1/programs` (draft program with extracted rules), `PATCH /v1/programs/{id}` (edit rules, threshold, status), `POST /v1/programs/{id}/claims/issue`, `POST /v1/claims/{id}/actions`, `POST /portal/{token}/submit`, `/photos`, `/answer`, and `POST /internal/feeds/pull`. Still 501: the portal payout step (M4), MPR (M4), billing and the Stripe webhook (payments owner).
- A claim filed through the portal is adjudicated by the worker (`make worker`) within a second or two of submit. Its trace grows row by row; poll `GET /v1/claims/{id}/trace` every 1 to 2 s while `run.status` is `running` (docs/contracts/trace.md). The header bar should show `used.steps` of `caps.max_steps`, not `run.steps`.
- `PortalView` gains `photos_uploaded` and `messages` (needs_info questions and the claimant's replies). Upload photos before `submit`; a second `submit` changes nothing.
- A flagged claim's run waits for a reviewer: `POST /v1/claims/{id}/actions` returns the claim unchanged (the review is queued), and the worker resumes the run; poll until the status changes. A second action while one is pending is a 409.
- `/readyz` is 503 until a worker heartbeat exists.

## What existed at the end of M1

- A FastAPI backend in apps/api with every route from 01-backend.md section 9, plus billing stubs and a photo route, in **docs/contracts/openapi.json**. Generate your zod types and client from that file (02-ui.md section 3: `npm run gen:api`). The API also serves it live at http://127.0.0.1:8000/openapi.json, with interactive docs at http://127.0.0.1:8000/docs.
- Implemented now (return real data): `GET /v1/recalls`, `GET /v1/programs`, `GET /v1/programs/{id}`, `GET /v1/programs/{id}/claims` (status filter, keyset cursor), `GET /v1/claims/{id}`, `GET /v1/claims/{id}/photos/{photo_id}`, `GET /v1/claims/{id}/trace`, `GET /v1/usage`, `GET /portal/{token}`, `/healthz`, `/readyz`.
- Contract only, returning 501 with a problem detail that names the milestone: program create and patch, link issue, reviewer actions, MPR, portal submit, photo upload, answer, and payout, billing checkout and portal, and the internal routes. Build these screens against the schemas; the shapes will not change without a decisions-log entry and a message to you.
- `make seed` loads a demo firm with one open program (the real Scotch thermal laminator recall, CPSC 27024) and 500 synthetic claims already adjudicated by the rules-only adjudicator: 182 approved, 94 denied, 107 needs_info, 117 flagged, 59 duplicate clusters, a 7-step trace per claim, rendered label, destruction, and receipt photos, and an audit row per claim. A second firm has 5 claims, so you can see tenant isolation from the UI.
- The trace viewer's row shape and status vocabulary are in **docs/contracts/trace.md**.

## The seam

- **Auth in development.** Send `X-Dev-Firm-Id` and `X-Dev-Role` (`admin` or `reviewer`) headers. Your `apiFetch` helper (from the login owner) should add them while `AUTH_PROVIDER=dev`; it later sends a Clerk bearer token instead and nothing else in your code changes.
  - Demo firm: `00000000-0000-4000-8000-00000000d001`
  - Other firm: `00000000-0000-4000-8000-00000000d002`
- **Stable ids across reseeds.** Demo program `0874f66f-bfb5-552b-bd93-6266f49c1d5a`; first claim `66dcc492-086f-5b59-b441-cbe001eb2e0b`.
- **Portal tokens in development** are `dev.<claim id>`, for example `/portal/dev.66dcc492-086f-5b59-b441-cbe001eb2e0b`. The login owner replaces them with signed tokens.
- **Errors** are RFC 7807 problem details (`application/problem+json`) with `detail` and `correlation_id`; show both (02-ui.md section 6). Another firm's claim is a 404, never a 403.
- **Photos.** `photos[].url` is an API path in every environment (send the same auth headers). The API reads the bytes from storage after checking the firm (local disk in development, a private Supabase bucket in production), so no storage URL ever reaches the browser; treat the path as opaque.
- **Money** is integer cents (`refund_cents`, `amount_cents`); costs are decimal strings in USD.

## Run the backend locally

Prerequisites: Docker, uv 0.11.21, Python 3.13. A fresh machine pulls the database image once: `docker pull postgres:16`.

```
cp .env.example .env
docker compose up -d
make setup      # pinned deps, roles, databases, migrations
make seed       # about 45 s
make api        # http://127.0.0.1:8000, docs at /docs
```

## Tests that must stay green

Backend CI (`.github/workflows/ci.yml`, job `backend`) runs on every pull request; you do not change backend code, but a contract change by the backend owner must keep `test_committed_openapi_matches_the_app` green. Your own job (`web`, vitest and `tsc --noEmit`) is already in ci.yml and switches on as soon as apps/web/package.json exists.

## Three commands to verify your work

```
make seed && make api                                                         # realistic local data
curl -s -H 'X-Dev-Firm-Id: 00000000-0000-4000-8000-00000000d001' \
  'http://127.0.0.1:8000/v1/programs/0874f66f-bfb5-552b-bd93-6266f49c1d5a/claims?status=flagged&limit=5'  # the queue your screen renders
cd apps/web && npm run gen:api && npx vitest run                              # types from the contract, your tests
```
