# Component 4: Login, tenancy, and access

Owner of: firm user authentication (Clerk), organizations as firms, roles, token verification in the backend, tenant isolation enforcement (repository scoping plus Postgres RLS), consumer claim tokens, rate limiting, and the audit log policy.

Read CLAUDE.md first. Invariants 2 and 10 govern this component.

## 1. Identity model

- A firm is a Clerk Organization. firms.clerk_org_id links them. Creating an organization in Clerk creates the firms row on first API call (lazy, idempotent) and records the creator as admin in firm_members.
- Roles: org:admin (everything, including billing and member management) and org:reviewer (programs read, claims read, reviewer actions, trace read; no billing, no member management, no program deletion). Roles live in Clerk; the backend reads them from the session token's organization claims and mirrors them to firm_members for audit joins only.
- Consumers have no identity. A claim token is the only credential for the portal.

## 2. Frontend (apps/web)

- @clerk/nextjs with the App Router: ClerkProvider in the root layout; clerkMiddleware protecting /app/(.*) and the API proxy routes; public matcher for /, /claim/(.*), /sign-in, /sign-up, and the Stripe return pages.
- Organization switcher in the app shell; if the user has no organization, show the create-organization flow before anything else.
- `apiFetch(path, init)` helper: server-side gets the session token via auth(); client-side via useAuth().getToken(). Always sends Authorization: Bearer and the active organization id is not sent separately; it is inside the token.
- Clerk test mode for Playwright: use Clerk's testing tokens and a seeded test organization; never use a real inbox.

## 3. Backend verification (apps/api)

- Dependency `current_user` verifies the Clerk session JWT: fetch JWKS from the Clerk instance (cached 1 hour, refresh on kid miss), verify RS256 signature, exp, nbf, azp against allowed origins (when the token carries azp), and iss. Reject anything else with 401. When the keys cannot be fetched (Clerk unreachable), answer 503 "sign-in verification unavailable", never 401, so the UI retries instead of signing the user out. The keys URL is `<CLERK_ISSUER>/.well-known/jwks.json` unless CLERK_JWKS_URL names another path on the same host.
- Dependency `current_firm`: reads org id and org role from the token claims (o.id and o.rol in Clerk's current token shape; confirm the claim names against the pinned @clerk/backend version at phase 0 and record them in the decisions log). Resolves firms by clerk_org_id (creating on first sight). Returns a FirmContext {firm_id, user_id, role}. No organization in the token -> 403 with a problem detail that the UI turns into the create-organization flow. A role other than admin or reviewer (Clerk's default org:member) -> 403 "your organization role has no access: ask an admin for org:admin or org:reviewer", and no firm is created.
  Confirmed 2026-10-20: session token v2 carries `o: {id, rol, slg}` with `rol` unprefixed (`admin`); v1 carried `org_id`, `org_role` (`org:admin`) and `org_slug`. `auth/clerk.py` reads both shapes and strips `org:`. First-sight creation goes through the SECURITY DEFINER function `recallops_firm_for_clerk_org` (migration 0007), because the API role never bypasses RLS.
- Role guard: `require_role("admin")` for billing, member, and program deletion routes.
- Every repository method takes FirmContext, not a raw firm id, so a route cannot accidentally pass a body-supplied id.

## 4. Tenant isolation, two layers

Layer 1, application: TenantScoped repositories add WHERE firm_id = :firm_id to every query and set firm_id on every insert from the context. There is no repository method that accepts a firm_id argument.

Layer 2, database: RLS enabled on every tenant table with a policy USING (firm_id = current_setting('app.firm_id')::uuid). The request-scoped session runs SET LOCAL app.firm_id = :firm_id at transaction start. Internal jobs (feed pull, worker, webhooks) use a separate role with BYPASSRLS, and each such code path sets the firm explicitly per claim when it touches tenant rows.

Proof: tests/test_tenancy.py creates two firms, inserts rows for each, and asserts for every tenant table that (a) a request with firm A's token cannot read, update, or delete firm B's rows through the API (404, never 403, so existence is not leaked), and (b) a raw SQL query under the request role with app.firm_id = A returns zero rows for B. This test is in the deploy gate.

## 5. Consumer claim tokens (invariant 10)

- Format (as built, 2026-10-20): 96 characters of unpadded base64url over firm id (16 bytes), claim id (16), expiry in Unix seconds (8), and HMAC-SHA256 over those 40 bytes (32) with CLAIM_TOKEN_SECRET. Stored as token_hash (sha256) on the claim; the raw token appears only in the issued link, `<PORTAL_BASE_URL>/<token>`.
- Expiry 90 days from issue (CLAIM_TOKEN_TTL_DAYS, at most 90); a program close invalidates all its tokens (status check in the dependency).
- Reissue: POST /v1/claims/{claim_id}/link (admin) returns a fresh link and rotates token_hash, so the previous link stops working. This is the answer to a lost, leaked, or expired link (a claim still open at day 90) and what a campaign sender calls at send time to fill a draft's {claim_link}. Refused with 409 on a closed program and on a paid or closed claim; audited as claim_link_reissued with `rotated`. A token is a function of firm, claim, and expiry second, so a reissue within the same second, or under the dev verifier, returns the same link (`rotated: false`).
- Single claim per token. A token cannot be used to create a second claim.
- Rate limits (in-process token bucket keyed by token and by IP, backed by a small table so limits survive restarts): 60 requests per hour per token, 10 photo uploads per hour per token, 300 requests per hour per IP on /portal/*. Over limit -> 429 with Retry-After.
- Token in the URL path; never in a query string that could be logged by proxies; portal pages set Referrer-Policy: no-referrer.
- Issued links are generated in batches by an admin (POST /v1/programs/{id}/claims/issue) and exported as CSV for the firm to distribute through its own notification channels.

## 6. Audit log policy

Write audit_log for: program create, rules edit, program status change, reviewer action on a claim, payout state change, plan change, member role change (from a Clerk webhook on organizationMembership.updated), token batch issue. Each row has actor, action, target, before, after. Audit rows are append-only; no update or delete path exists in code, and the database role used by the API has no DELETE grant on the table.

## 7. Secrets and configuration

- CLERK_SECRET_KEY, CLERK_PUBLISHABLE_KEY, CLERK_JWKS_URL, CLERK_ALLOWED_ORIGINS, CLAIM_TOKEN_SECRET (32 random bytes, base64), DATABASE_URL (request role), DATABASE_URL_INTERNAL (bypass role), all documented in .env.example with fake values.
- Startup refuses to boot if CLAIM_TOKEN_SECRET decodes to the example key (in any base64 spelling) or to fewer than 32 bytes.
- CLERK_ISSUER (the Frontend API URL, equal to the token's iss), PORTAL_BASE_URL, and SEED_CLERK_ORG_ID (development: `make seed` binds the demo firm to that organization). In prod, settings refuse an http, localhost, or example CLERK_ISSUER, empty or non-https CLERK_ALLOWED_ORIGINS, and a non-https or localhost PORTAL_BASE_URL. CLERK_SECRET_KEY and CLAIM_TOKEN_SECRET are masked in logs and errors.
- The API sends no CORS headers: the browser reaches it through the Next.js server, so CLERK_ALLOWED_ORIGINS is the azp allow-list, not a CORS list. Uvicorn's access log is off in deploy (`--no-access-log`), because a portal path is a bearer token; the API's own request log redacts it.

## 8. Tests

- JWT verification: valid token passes; expired, wrong issuer, wrong audience, unknown kid, tampered signature each fail with 401.
- Role guard matrix.
- Tenancy test from section 4.
- Claim token: round trip, expiry, tamper, reuse after program close, rate limit hit and recovery.
- Playwright: sign in as reviewer, confirm billing page is hidden and the API returns 403 if hit directly.

## 9. Runtime traces required

- One full sign-in to first API call, showing the token fetch, the JWKS cache hit or miss, and the firm resolution.
- One portal session showing token validation per request and a rate-limit trip.

## 10. Acceptance

- Two firms, two users, no leakage in either layer, proven by the gate test.
- A reviewer cannot reach billing or delete a program through the UI or the API.
- A claim link works on a phone with no account, expires at 90 days, and dies when the program closes.
