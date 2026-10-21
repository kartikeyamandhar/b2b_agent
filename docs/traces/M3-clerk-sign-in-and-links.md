# Runtime trace: Clerk sign-in, firm resolution, and signed claim links

Observed on 2026-10-21 03:18 UTC (local Postgres 16.15, FastAPI 0.143.3) by running `tests/api/test_clerk_api.py` with `-s`. The real app (`create_app()`) ran in process with `AUTH_PROVIDER=clerk` and `CLAIM_TOKEN_PROVIDER=hmac` against the test database. Clerk session tokens were RS256 JWTs signed by a key generated in the test. The JWKS client was a stub serving that key, so nothing reached Clerk. Times are milliseconds after `clerk_auth_provider_loaded` (03:18:21.101), taken from the API's JSON log lines. Nothing below is intended behavior; it is what was recorded.

Before the clock starts, the fixture signed `org_demo` in through a separate provider, which created an empty firm. It then ran `make seed`'s code with `SEED_CLERK_ORG_ID=org_demo`. Logging is configured only by `create_app()`, so those steps left no log lines. Their result is asserted instead: the demo firm holds `org_demo`, and the earlier firm is unbound but kept.

## Sessions and tenancy

| t (ms) | event | answer |
|---|---|---|
| 33 | `clerk_firm_resolved` demo firm (`org_demo`, first request of this process) | |
| 44 | GET a demo claim as `org_demo` admin | 200 (33.0 ms, including the resolver call) |
| 49 | `clerk_firm_resolved` other firm (`org_other`) | |
| 53 | GET the same claim as `org_other` admin | 404 |
| 57 | `clerk_firm_resolved` a new firm `262b84b4-...` (`org_new`, created on first sight) | |
| 60 | GET the same claim as `org_new` admin | 404 |
| 60 | GET /v1/programs, no bearer | 401 (0.3 ms) |
| 61 | GET /v1/programs, only X-Dev-* headers | 401 (the dev seam is off) |
| 62 | GET /v1/programs as `org:member` | 403, "your organization role has no access..." (0.4 ms, no resolver call, no firm created) |

Each organization's first request pays one resolver call (`recallops_firm_for_clerk_org` plus the member mirror) before the route runs. Later requests from the same org, user, and role hit the 5-minute cache. The resolver ran again for the demo firm at 67 ms and at 109 ms, for two new user and role pairs, because the cache key includes both.

## Links: issue, open, tamper

| t (ms) | event | answer |
|---|---|---|
| 67 | POST issue as reviewer | 403 |
| 71 | POST issue on the demo program as `org_other` admin | 404 |
| 76 | `claim_links_issued` n=1 | |
| 78 | POST issue as admin | 200, `http://localhost:3000/claim/<96 characters>` |
| 94 | GET /portal/`<token>` | 200 (12.0 ms) |
| 97 | GET /portal/`<token with the last two characters changed>` | 404 (0.3 ms: the MAC check refuses it in the dependency, before the route looks up a claim) |

The request log writes `/portal/<token>` for every portal path. It never holds the token itself.

## Reissue

| t (ms) | event | answer |
|---|---|---|
| 105 | POST issue as admin (claim `8076b99a-...`) | 200 |
| 109 | POST /v1/claims/{claim}/link as reviewer | 403 |
| 113 | the same as `org_other` admin | 404 |
| 117 | an unknown claim id | 404 |
| 123 | `claim_link_reissued` **rotated: false** | 200: the same second as the issue at 105, so the same token |
| 1,204 | `claim_link_reissued` **rotated: true** | 200, a new token (the test waited 1.05 s) |
| 1,225 | GET /portal/`<old token>` | 404 |
| 1,248 | GET /portal/`<new token>` | 200 |

A token is a function of firm, claim, and expiry second. A reissue in the same second therefore returns the link the caller already holds. The audit row records `rotated: false`, so revocation never silently claims success.

The first CI run of this test (GitHub Actions, 2026-10-21 03:50 UTC) recorded `rotated: true` for both reissues: on the slower runner, the issue and the first reissue fell in different seconds. The test assumed they shared one, which was a timing bug in the test, not in the route. The test now drives the link verifier's clock (it holds still for the same-second case and moves 5 s for the rotation) instead of sleeping, so both outcomes are exercised on any machine.

## Close

| t (ms) | event | answer |
|---|---|---|
| 1,272 | POST issue as admin (claim `c731cec2-...`) | 200 |
| 1,304 | PATCH the program to closed | 200 (29.2 ms) |
| 1,316 | GET /portal/`<that link's token>` | 404 |
| 1,329 | POST /v1/claims/{claim}/link | 409, "the program is closed..." |

After the close, the link stops working on the very next request. There is no cache between the token check and the program status.

## Not observed here

A real Clerk instance: its JWKS endpoint, real session tokens from `getToken()`, and the azp value a browser session carries. Those need the login owner's test instance and a network call. The JWKS outage path (503 "sign-in verification unavailable") is covered by `tests/unit/test_clerk_auth.py::test_a_keys_outage_answers_503_not_401` with a stub that raises `PyJWKClientConnectionError`, not by a real timeout.
