# Runtime trace: reissuing a claimant's link from claim detail

Observed on 2026-10-21 in Chrome against the production build of apps/web (`next start`, Next 16.4.2, dev auth as an admin) and the local API (uvicorn, Postgres 16.15, dev claim tokens). Browser times come from an in-page recorder that wrapped `fetch` for `/api/*` and polled the DOM every 20 ms; API times are the API's own log lines, joined by `x-request-id`. Same machine, UTC. Ids are shortened; the link itself was never logged, stored, or copied into this file. Nothing below is intended behavior; it is what was recorded.

The claim (`364f65a6`, status `draft`) was issued for this trace in the test program `7d91d93d`; no seeded claim's link was touched.

| t (ms) | UTC | where | event |
|---|---|---|---|
| 0.1 | 17:34:14.981 | browser | recorder attached on `/app/claims/364f65a6…` |
| 4876.3 | 17:34:19.857 | browser | "Issue a new link" clicked: confirmation "The claimant's current link will stop working" |
| 12362.6 | 17:34:27.344 | browser | "Issue new link" clicked: → `POST /api/v1/claims/364f65a6…/link` |
| 12373.4 | 17:34:27.354 | browser | button disabled, "Issuing…" |
| | 17:34:28.008 | API | `claim_link_reissued`, `rotated: false` |
| | 17:34:28.210 | API | `http_request POST /v1/claims/364f65a6…/link` 200, 583.3 ms (`0a10570a`) |
| 13236.3 | 17:34:28.217 | browser | ← 200 (`IssuedLink`) |
| 13240.5 | 17:34:28.221 | browser | → `GET /api/v1/claims/364f65a6…` (claim detail refetch, so the page reflects the change) |
| 13246.7 | 17:34:28.228 | browser | confirmation removed; "New link issued. It is shown only now." with the link and Copy |
| | 17:34:28.380 | API | `http_request GET /v1/claims/364f65a6…` 200, 139.6 ms (`4024d7d5`) |
| 13406.3 | 17:34:28.387 | browser | ← 200 |

The new link is drawn from the POST's answer and held only in the component's memory; the refetch that follows changes nothing visible, because the backend writes the reissue's audit row with `target_type` "program" (reported to the backend owner), so the claim's own audit trail does not list it. `rotated: false` is the dev token provider: a dev link is a function of firm and claim, so the same link came back. With HMAC links the old one stops working.

The POST took 583 ms at the API, against 14 to 350 ms for every other console request recorded the same day. From the timestamps alone: the API started handling it about 17:34:27.627 (end minus duration), 283 ms after the browser sent it; the `claim_link_reissued` line came 381 ms after that, and the response 202 ms later. Where the time went inside the API was not measured.

After the program was closed (see M6-ui-program-lifecycle.md), the same POST answered 409 "the program is closed: its claim links stay dead", and claim detail now explains that instead of offering the button.
