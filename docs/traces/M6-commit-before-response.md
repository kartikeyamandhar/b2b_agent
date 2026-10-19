# Runtime trace: the API commits before it answers

Observed on 2026-10-19 (local Postgres 16.15, FastAPI 0.143.3), in process through `httpx.ASGITransport`, with the session dependency's commit and the ASGI response messages timestamped. One `PATCH /v1/programs/{id}` (value threshold), the same request before and after the change in apps/api/src/recallops/api/deps.py. Nothing below is intended behavior; it is what was recorded.

## Before: `Depends(tenant_db)` (FastAPI's default request scope)

| t (ms) | event |
|---|---|
| 0.00 | PATCH received |
| 26.28 | response headers sent (HTTP 200) |
| 26.32 | response body sent |
| 26.41 | route returned; transaction commit starts |
| 27.05 | commit finished |

The client could hold a 200 for about 0.7 ms before the row was committed. A read sent in that window saw the old row: the test console's QA measured the campaign summary read right after `POST .../campaign/match` stale in 4 of 10 tries. A commit that failed in that window was invisible: `test_a_failed_commit_answers_an_error_not_a_success` answered **200** with the commit raising.

## After: `Depends(tenant_db, scope="function")`

| t (ms) | event |
|---|---|
| 0.00 | PATCH received |
| 26.07 | route returned; transaction commit starts |
| 27.07 | commit finished |
| 27.28 | response headers sent (HTTP 200) |
| 27.33 | response body sent |

The commit now finishes before the first response byte. The same failing-commit test answers **500**, as an `application/problem+json` body with a fixed detail ("unexpected server error; nothing was saved...") and the correlation id, never the exception's text: a new catch-all handler in apps/api/src/recallops/api/problems.py. Before it, an unhandled error answered `text/plain` "Internal Server Error", outside the RFC 7807 contract.

`PublicDb` (recall search) got the same scope. A simple write-then-read loop (200 PATCH and GET pairs over HTTP) showed no stale read either before or after the change: a one-row commit usually lands before the next request arrives, which is why the race appeared on the heavier campaign match and not here. The ordering above is the evidence, not the loop.
