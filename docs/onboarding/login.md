# Onboarding: login owner (component 4)

You own Clerk sign-in and organizations in apps/web, Clerk token verification in the backend, firm resolution, roles, and the consumer claim tokens (04-login.md). Tenant scoping, row-level security, and the cross-tenant tests are the backend owner's and already ship (M1).

## What exists today (end of M1)

- **The auth seam** in `apps/api/src/recallops/auth/` (your package, CODEOWNERS):
  - `provider.py`: the `AuthProvider` protocol (`authenticate(headers) -> Principal`, `firm_context(principal) -> FirmContext`), `ClaimTokenVerifier` (`verify(token)`, `token_hash(token)`), and the errors `AuthError` (401) and `NoFirmError` (403).
  - `dev.py`: `DevAuthProvider` reads `X-Dev-Firm-Id`, `X-Dev-Role`, `X-Dev-User-Id`; it refuses to load with `APP_ENV=prod` or when any Stripe or Clerk key contains `_live_`. `DevClaimTokenVerifier` accepts `dev.<claim uuid>`.
  - `deps.py`: the FastAPI dependencies `current_user`, `current_firm`, `require_role("admin")`, `claim_token`, and the `build_provider()` switch on `AUTH_PROVIDER`.
- Every route already depends on `current_firm` (or `claim_token` for /portal). Repositories take a `FirmContext` (`recallops.tenancy`), never a raw firm id, and the request session sets `app.firm_id` with `set_config(..., true)` for RLS.
- **Database roles**: the API connects as `recallops_app` (no BYPASSRLS, owns nothing); jobs use `recallops_internal` (BYPASSRLS); migrations use `recallops_owner`. `DATABASE_URL` and `DATABASE_URL_INTERNAL` match 04-login.md section 7.
- **Proof today**: `tests/db/test_tenancy.py` (14 tests, connects as `recallops_app`, mutation-checked: disabling RLS on one table fails 5 of them) and `tests/api/test_api.py::test_cross_tenant_reads_are_404` (404 through the API with the dev provider).
- Settings already declare `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_ALLOWED_ORIGINS`, `CLAIM_TOKEN_SECRET` with fake values.

## The seam, concretely

1. Add `auth/clerk.py` implementing `AuthProvider`: JWKS fetch and cache, RS256, exp, nbf, iss, azp (04-login.md section 3); read org id and role from the token claims (confirm claim names against the pinned @clerk/backend 3.25.0 and log them). `firm_context` resolves `firms.clerk_org_id`, creating the row on first sight through a narrow internal-role path, and mirrors `firm_members`.
2. Register it in `deps.build_provider()` for `AUTH_PROVIDER=clerk`. Nothing else in the backend changes; if you find you need to, write a decisions-log note first.
3. Add an HMAC `ClaimTokenVerifier` (claim id + expiry + HMAC-SHA256, 90 days) and register it in `build_claim_verifier()`. `claims.token_hash` stores sha256 of the raw token. Token issue (`POST /v1/programs/{id}/claims/issue`) is a 501 stub whose contract is in docs/contracts/openapi.json; the backend implements it in M2 using your verifier's issue function.
4. Rate limits (04-login.md section 5) need a table: request it with a decisions-log note; only the backend owner adds migrations.
5. In apps/web: ClerkProvider, middleware matcher excluding `/claim/*`, and the `apiFetch` helper the UI owner uses (send `X-Dev-*` headers while `AUTH_PROVIDER=dev`, a bearer token after).

## Run the backend locally

```
cp .env.example .env            # Clerk TEST keys go in .env only
docker compose up -d && make setup && make seed
make api
curl -s -H 'X-Dev-Firm-Id: 00000000-0000-4000-8000-00000000d002' \
  http://127.0.0.1:8000/v1/claims/66dcc492-086f-5b59-b441-cbe001eb2e0b    # another firm's claim: 404
```

## Tests that must stay green

`tests/db/test_tenancy.py` (all 14), `tests/api/test_api.py` (cross-tenant 404s, auth failures and roles, dev provider refusals), and the coverage gate in `make check`. Add your JWT tests (valid, expired, wrong issuer, wrong audience, unknown kid, tampered signature, each a 401) under `apps/api/tests/`, with JWKS served from a local fixture, never the network.

## Three commands to verify your work

```
make check                                                                    # everything CI runs
AUTH_PROVIDER=clerk make api                                                  # starts with your provider
cd apps/api && uv run --frozen pytest tests/db/test_tenancy.py tests/api -q   # isolation still holds
```
