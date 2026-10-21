# Runtime trace: the website's waiting, polling, and refreshing

Observed on 2026-10-21 around 20:40 UTC, while the backend owner reviewed PR 9. The website ran in development mode (`next dev`, dev auth, the demo firm's admin) against the local API on 127.0.0.1:8000, with the seeded database and the rules-only worker (`ADJUDICATOR=rules_only`, no model calls). Headless Chrome recorded each page's API requests through the DevTools protocol, and a probe read the page's visible state every 250 to 500 ms. Times are seconds after navigation. Claims are synthetic. Nothing below is intended behavior; it is what was recorded.

## 1. The claims queue refreshes every 10 s

`/app/programs/{P}/claims`, 33 s.

| t (s) | event |
|---|---|
| 0.01 | navigate |
| 2.42 | page: "Updated 20:41:28 UTC · refreshes every 10 s while this tab is visible" |
| 12.18 | GET program and GET claims?limit=50 (together) |
| 12.40 | page: "· refreshing…" |
| 12.84 | both 200 |
| 13.00 | page: "Updated 20:41:39 UTC" |
| 22.84 | GET program and GET claims (together); 200 at 22.89 |
| 32.89 | GET program and GET claims (together); 200 at 33.04 |

The first refresh comes 10 s after the server-rendered page, then every 10 s. The refresh pauses while the tab is hidden; that is React Query's `refetchIntervalInBackground: false`, unit-tested, and not observed here.

## 2. A claimant's status page refreshes until the decision

`/claim/<token>/status` at 390 px. The claim was submitted with three photos and the worker was started 5 s after the page opened.

| t (s) | event |
|---|---|
| 0.04 | GET the status page (server-rendered) |
| 1.31 | page: "We received your claim" |
| about 5 | the worker starts and decides the claim within a second |
| 16.43 | the page's own refresh (15 s after it loaded) |
| 16.70 | page: "Your claim was approved" |

The page refreshes every 15 s while the claim is `submitted`, at most 8 times, and stops once it is decided. The claimant sees "approved" about 11 s after the decision, which is the refresh interval, not a delay in the API.

## 3. Claim detail waits for the first run (new in the review)

`/app/claims/{id}` for a claim submitted while no worker ran; the worker started at 8.0 s. Before the review, this page never polled a claim without a run.

| t (s) | event |
|---|---|
| 0.73 | page: status "Submitted", Latest run panel present |
| 2.58 | GET claim (200) and GET trace (404: no run yet) |
| 4.64 | the same pair |
| 6.68 | the same pair |
| 8.00 | the worker starts |
| 8.70 | GET claim and GET trace, both 200 |
| 8.79 | page: status "Flagged", the four reviewer actions enabled |
| 8.79 to 26 | no further requests: a run exists and is not running, so the page stops |

Every 2 s, as designed (`waitsForWorker`, `WAIT_POLL_MS`), capped at 120 s after load. The claim was flagged because it reused photos from an earlier claim, which the duplicate check caught.

## 4. A reviewer's action that applies at once

`/app/claims/{id}` for a claim the rules-only worker had flagged (its run finished, so nothing waits on the worker).

| t (s) | event |
|---|---|
| 1.73 | page: "Flagged", actions enabled |
| 4.25 | click Approve (opens the inline confirmation) |
| 5.03 | click Confirm approve; POST .../actions |
| 5.24 | 200; GET claim and GET trace at once |
| 5.25 | both 200 |
| 5.28 | page: "Approved", flash "Approve: done. The status went from flagged to approved." |

The action was applied 0.21 s after the click and the page showed it 0.04 s later.

## 5. Billing after a Stripe return

`/app/billing/return` (the new `BILLING_RETURN_URL` page), 70 s.

| t (s) | event |
|---|---|
| 0.76 | two GET usage requests at the same instant, both 200 |
| 0.96 | page: "Back from Stripe. The plan below changes only after Stripe confirms the payment…" |
| 3.98 to 61.73 | GET usage every 3.0 s (20 requests), each answered in 20 to 70 ms |
| 61.73 to 70 | no further requests |

The poll stops one minute after the page loads, as designed. The duplicate request at 0.76 s is most likely the development build mounting the page twice (`reactStrictMode` is on); a production build was not traced, so that is not confirmed.

## Not observed live

- **A reviewer's action queued behind a paused run.** That path needs the AI agent's pause-for-a-person (a LangGraph interrupt). The rules-only worker finishes its runs, so trace 4 shows the direct path. A live agent run costs model money, and the offline fake model returns only scripted test answers. The backend side of the queued path was observed live in `M2-adjudication.md` (C1 and C4: review submitted at 06:36:07.1, applied by the worker at 06:36:08.0). The page side, with buttons disabled while queued, a 2 s poll, and the "still waiting" message at 60 s, is covered by `tests/console-review-fixes.test.tsx`.
- **A hidden tab** (no visibility emulation in headless Chrome), **a failed photo upload and Try again**, and **the Stripe Connect return**. These are unit-tested in `tests/portal-review-fixes.test.tsx` and `tests/portal-components.test.tsx`.
