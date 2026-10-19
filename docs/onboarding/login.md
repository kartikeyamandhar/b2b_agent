# Onboarding: login owner (component 4)

You own Clerk sign-in and organizations in apps/web, Clerk token verification in the backend, firm resolution, roles, and the consumer claim tokens (04-login.md). Tenant scoping, row-level security, the Neo4j graph's structural tenancy, and the cross-tenant tests are the backend owner's and ship today. Read **docs/contracts/CHANGES.md** items 1, 17, and 18 first: the claim-token protocol changed.

## What exists (backend M2 to M5)

- **The auth seam** in `apps/api/src/recallops/auth/` (your package, CODEOWNERS):
  - `provider.py`: `AuthProvider` (`authenticate(headers) -> Principal`, `firm_context(principal) -> FirmContext`), `ClaimTokenVerifier` (`verify(token) -> ClaimTokenContext(firm_id, claim_id)`, `token_hash(token)`, `issue(firm_id, claim_id) -> str`), and the errors `AuthError` (401) and `NoFirmError` (403).
  - `dev.py`: `DevAuthProvider` reads `X-Dev-Firm-Id`, `X-Dev-Role`, `X-Dev-User-Id`; it refuses `APP_ENV=prod` and any Stripe or Clerk key containing `_live_`. `DevClaimTokenVerifier` issues and accepts `dev.<firm uuid>.<claim uuid>`; it refuses `APP_ENV=prod` and `AUTH_PROVIDER=clerk`.
  - `deps.py`: `current_user`, `current_firm`, `require_role("admin")`, `claim_token`, and `build_provider()` / `build_claim_verifier()`, which raise for `clerk` and `hmac` until you register yours.
- Every firm route depends on `current_firm`; portal routes on `claim_token`. Repositories take a `FirmContext`, and each request transaction sets `app.firm_id` with `set_config(..., true)` for RLS.
- **Database roles**: the API connects only as `recallops_app` (no BYPASSRLS, owns nothing); jobs use `recallops_internal`; migrations use `recallops_owner`. Narrow SECURITY DEFINER functions (migration 0003) cover what the API needs across tenants.
- **Rate limits**: the `rate_limits` table and a fixed-window limiter (`api/guards.py`) already guard `/portal/*` per token and per IP (04-login.md section 5). You can replace the limiter; the table stays.
- **Proof today**: `tests/db/test_tenancy.py` (every tenant table, including export batches, registrations, and notifications), cross-tenant 404s through the API in `tests/api/test_api.py` and `tests/api/test_m2_flows.py`, and the fraud graph's isolation tests in `tests/db/test_fraud_graph_store.py`.

## The seam, concretely

1. Add `auth/clerk.py` implementing `AuthProvider`: JWKS fetch and cache, RS256, exp, nbf, iss, azp (04-login.md section 3); org id and role from the token claims (confirm the claim names against the pinned @clerk/backend 3.25.0 and log them). `firm_context` resolves `firms.clerk_org_id` and mirrors `firm_members`.
2. Add an HMAC `ClaimTokenVerifier`: firm id, claim id, expiry, HMAC-SHA256 with `CLAIM_TOKEN_SECRET`, 90 days. `issue(firm_id, claim_id)` is what `POST /v1/programs/{id}/claims/issue` and the campaign drafts call. `claims.token_hash` stores sha256 of the raw token.
3. Register both in `deps.py` together: `AUTH_PROVIDER=clerk` with `CLAIM_TOKEN_PROVIDER=hmac` (the dev verifier refuses to run beside Clerk). Nothing else in the backend should change; if it must, write a decisions-log note first.
4. In apps/web: ClerkProvider, a middleware matcher that excludes `/claim/*`, and the `apiFetch` helper the UI owner uses (dev headers while `AUTH_PROVIDER=dev`, a bearer token after).

## Run the backend locally

```
cp .env.example .env            # Clerk TEST keys go in .env only
docker compose up -d && make setup && make seed
make api
curl -s -H 'X-Dev-Firm-Id: 00000000-0000-4000-8000-00000000d002' \
  http://127.0.0.1:8000/v1/claims/66dcc492-086f-5b59-b441-cbe001eb2e0b    # another firm's claim: 404
```

## Tests that must stay green

`tests/db/test_tenancy.py`, `tests/api/test_api.py` and `tests/api/test_security.py` (auth failures, roles, dev-provider refusals), and the coverage gate. Add your JWT tests (valid, expired, wrong issuer, wrong azp, unknown kid, tampered signature: each a 401) with JWKS from a local fixture, never the network.

## Three commands to verify your work

```
make check                                                                         # everything CI runs
AUTH_PROVIDER=clerk CLAIM_TOKEN_PROVIDER=hmac make api                             # starts with both of yours
cd apps/api && PYTHONPATH=src uv run --frozen pytest tests/db/test_tenancy.py tests/api -q   # isolation still holds
```
