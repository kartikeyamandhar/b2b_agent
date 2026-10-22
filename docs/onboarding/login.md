# Onboarding: login owner (component 4)

> Since 2026-10-22 (plan v3, CHANGES item 57) the owner builds every component alone and there are no Clerk organizations: one account is one company. This guide is kept as history; the passages below describe the current behaviour.

You own Clerk sign-in in apps/web, Clerk token verification in the backend, firm resolution, roles, and the consumer claim tokens (04-login.md). Tenant scoping, row-level security, the Neo4j graph's structural tenancy, and the cross-tenant tests are the backend owner's and ship today. Read **docs/contracts/CHANGES.md** items 1, 17, 18, 19a, and 36 to 40 first: the claim-token protocol changed, and the backend review of your PR 8 changed a migration, added a route, and tightened settings.

For billing-admin authorization, claimant payout tokens or reviewer-seat limits, follow the
[payments handoff: login owner](payments.md#login-owner). It defines the payment integration checks your
provider must preserve and separates logged-in user requests from signed Stripe webhooks.

## What exists (backend M2 to M5)

- **The auth seam** in `apps/api/src/recallops/auth/` (your package, CODEOWNERS):
  - `provider.py`: `AuthProvider` (`authenticate(headers) -> Principal`, `firm_context(principal) -> FirmContext`), `ClaimTokenVerifier` (`verify(token) -> ClaimTokenContext(firm_id, claim_id)`, `token_hash(token)`, `issue(firm_id, claim_id) -> str`), and the errors `AuthError` (401) and `NoFirmError` (403).
  - `dev.py`: `DevAuthProvider` reads `X-Dev-Firm-Id`, `X-Dev-Role`, `X-Dev-User-Id`; it refuses `APP_ENV=prod` and any Stripe or Clerk key containing `_live_`. `DevClaimTokenVerifier` issues and accepts `dev.<firm uuid>.<claim uuid>`; it refuses `APP_ENV=prod` and `AUTH_PROVIDER=clerk`.
  - `deps.py`: `current_user`, `current_firm`, `require_role("admin")`, `claim_token`, and `build_provider()` / `build_claim_verifier()`, which build `ClerkAuthProvider` (`clerk.py`) and `HmacClaimTokenVerifier` (`hmac_tokens.py`) for `clerk` and `hmac` (2026-10-20).
  - `clerk.py`: verifies the Clerk session JWT (RS256 against the instance JWKS, cached 1 hour and refetched on an unknown kid; exp, nbf, iat, iss, sub required; azp checked against `CLERK_ALLOWED_ORIGINS` when present). Organization claims are ignored (plan v3): the account (`sub`) is the company and its role is always admin. `firm_context` finds the company with `recallops_firm_of_clerk_user` and, at an account's first sign-in, creates it with `recallops_firm_for_clerk_user` (migration 0009, SECURITY DEFINER); the platform operator never gets one, and when Clerk cannot say whether a new account is the operator the answer is a 503 that creates nothing.
  - `hmac_tokens.py`: 96-character base64url tokens carrying firm id, claim id and expiry under HMAC-SHA256 (the link is `<PORTAL_BASE_URL>/<token>`); refuses any spelling of the example `CLAIM_TOKEN_SECRET` or one shorter than 32 bytes.
  - `POST /v1/claims/{claim_id}/link` (admin, programs.py `reissue_link`): a fresh link for one claim that stops the old one; the fix for lost, leaked, and day-90 links (04-login.md section 5).
  - A JWKS fetch failure is `AuthUnavailable` (503), not a 401.
- Every firm route depends on `current_firm`; portal routes on `claim_token`. Repositories take a `FirmContext`, and each request transaction sets `app.firm_id` with `set_config(..., true)` for RLS.
- **Database roles**: the API connects only as `recallops_app` (no BYPASSRLS, owns nothing); jobs use `recallops_internal`; migrations use `recallops_owner`. Narrow SECURITY DEFINER functions (migration 0003) cover what the API needs across tenants.
- **Rate limits**: the `rate_limits` table and a fixed-window limiter (`api/guards.py`) already guard `/portal/*` per token and per IP (04-login.md section 5). You can replace the limiter; the table stays.
- **Proof today**: `tests/db/test_tenancy.py` (every tenant table, including export batches, registrations, and notifications), cross-tenant 404s through the API in `tests/api/test_api.py` and `tests/api/test_m2_flows.py`, and the fraud graph's isolation tests in `tests/db/test_fraud_graph_store.py`.

## The seam, concretely

1. Add `auth/clerk.py` implementing `AuthProvider`: JWKS fetch and cache, RS256, exp, nbf, iss, azp (04-login.md section 3); org id and role from the token claims (confirm the claim names against the pinned @clerk/backend 3.25.0 and log them). `firm_context` resolves `firms.clerk_org_id` and mirrors `firm_members`.
2. Add an HMAC `ClaimTokenVerifier`: firm id, claim id, expiry, HMAC-SHA256 with `CLAIM_TOKEN_SECRET`, 90 days. `issue(firm_id, claim_id)` is what `POST /v1/programs/{id}/claims/issue` and the campaign drafts call. `claims.token_hash` stores sha256 of the raw token.
3. Register both in `deps.py` together: `AUTH_PROVIDER=clerk` with `CLAIM_TOKEN_PROVIDER=hmac` (the dev verifier refuses to run beside Clerk). Nothing else in the backend should change; if it must, write a decisions-log note first.
4. In apps/web: ClerkProvider, a middleware matcher that excludes `/claim/*`, and the `apiFetch` helper the UI owner uses (dev headers while `AUTH_PROVIDER=dev`, a bearer token after).

## Clerk dashboard (test instance)

Sign-up with email and the user's own password, with a verification code emailed at sign-up, is Clerk configuration; the backend sends no email.

1. Create an application. **User & authentication → Email**: sign-up with email on, **Verify at sign-up** on, method **Email verification code**. **Password**: on.
2. **Organizations**: off (plan v3: one account is one company).
3. **API keys**: copy into `.env` only: `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_ISSUER` (the Frontend API URL), and `CLERK_ALLOWED_ORIGINS` (the web app's origin). Leave `CLERK_JWKS_URL` empty: it is derived from the issuer, and a value on another host is refused. Generate `CLAIM_TOKEN_SECRET` with the command in `.env.example`, and set `AUTH_PROVIDER=clerk` and `CLAIM_TOKEN_PROVIDER=hmac`.
4. Sign up on the website (`/sign-up`); the account's first sign-in creates its company, which names itself and picks a plan at `/app/welcome`.
5. **Demo data under Clerk** (local only; the seed refuses production): your first sign-in creates an empty company for the account. To land on the seeded demo company instead, put the account's user id (`user_...`, Clerk dashboard) in `SEED_CLERK_USER_ID` and run `make seed`; the demo company takes the binding over. Restart `make api` afterwards, because the company lookup is cached.

## Run the backend locally

```
cp .env.example .env            # Clerk TEST keys go in .env only
docker compose up -d && make setup && make seed
make api
curl -s -H 'X-Dev-Firm-Id: 00000000-0000-4000-8000-00000000d002' \
  http://127.0.0.1:8000/v1/claims/66dcc492-086f-5b59-b441-cbe001eb2e0b    # another firm's claim: 404
```

## Tests that must stay green

`tests/db/test_tenancy.py`, `tests/api/test_api.py` and `tests/api/test_security.py` (auth failures, roles, dev-provider refusals), your `tests/unit/test_clerk_auth.py`, `tests/unit/test_hmac_tokens.py`, and `tests/db/test_clerk_firms.py`, the review's `tests/unit/test_login_review.py` and `tests/api/test_clerk_api.py` (the API end to end with Clerk tokens signed by a local key), and the coverage gate. JWKS always comes from a local fixture, never the network.

## Three commands to verify your work

```
make check                                                                         # everything CI runs
AUTH_PROVIDER=clerk CLAIM_TOKEN_PROVIDER=hmac make api    # both of yours; needs CLERK_ISSUER and a generated CLAIM_TOKEN_SECRET in .env
cd apps/api && PYTHONPATH=src uv run --frozen pytest tests/db/test_tenancy.py tests/api -q   # isolation still holds
```
