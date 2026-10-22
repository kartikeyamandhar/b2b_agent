# M6 trace: one account, one company; one claim link per product (plan v3, 2026-10-22)

What changed (CHANGES.md item 57): a Clerk account is one company with no organizations; the operator has no company
and works in one window at `/operator`; approving a request creates and opens the company's product; each product has
one public claim link (`/file/<token>`) that starts claims with their own private links; refunds offer a bank transfer
or a mailed check on every plan; plans are chosen at sign-up.

## 1. The flows, observed locally

Run: the production build of the website (`next start`) three times against one local API (`make api`,
`AUTH_PROVIDER=dev`, `ACCESS_APPROVAL=required`, the operator `dev-operator`), on the development database after
`make seed`: the demo company (port 3000), the operator (3001), and a company that has not set itself up (3002).
Headless Chrome drove the pages; each click is scripted, about 2.5 s after the page loaded. Times are the API's
`http_request` and decision log lines (UTC, 2026-10-22), with each request's own duration.

**A claimant starts a claim from a product's public link (390 px wide).**

| time | what happened | answer | took (ms) |
|---|---|---|---:|
| 06:44:52.519 | `/file/<token>` renders on the server: `GET /portal/intake/<token>` | 200 | 11.3 |
| 06:44:55.151 | "Start my claim" pressed: the draft claim is written (`intake_claim_started`) | | |
| 06:44:55.153 | `POST /portal/intake/<token>` answers with the claim's private link | 201 | 11.9 |
| 06:44:55.177 | `router.replace` to `/claim/<claim token>`, rendered on the server: `GET /portal/<token>` | 200 | 13.7 |

From the press to the claimant's own claim page: 26 ms of API time. The page showed step 1 of 6 and the "Your private
claim link" card; Back skips `/file`. Earlier, the same page with a link version that was never issued answered 404
(06:44:24.182, 11.0 ms) and showed "This claim link is not active".

**A new company sets itself up and asks for a recall (1440 px).**

| time | what happened | answer | took (ms) |
|---|---|---|---:|
| 06:45:21.181 | `/app` renders: `GET /v1/me` says not set up, so the setup page renders in place | 200 | 19.9 |
| 06:45:24.127 | "Continue" on Free: `company_onboarded` | | |
| 06:45:24.137 | `POST /v1/onboarding` | 200 | 22.0 |
| 06:45:24.156 | full navigation to Recall access: `GET /v1/access`, `GET /v1/me`, `GET /v1/access` | 200 | 5.7, 7.0, 4.3 |
| 06:45:55.576 | "Request access" on a recall: `access_requested` | | |
| 06:45:55.583 | `POST /v1/access` | 201 | 21.6 |
| 06:45:55.604 | the page refreshes its server data: `GET /v1/me`, `GET /v1/access` (twice) | 200 | 8.5, 6.2, 4.6 |

After the request the page showed it as "Waiting for review" and the plan-full prompt, then worded "Your plan covers
one product at a time, and it is in use" with the other "Request access" buttons still enabled; the review then made
the sentence cover a waiting request and disabled those buttons. The first recall it asked for (27025) belonged to another company, so the operator's
approve answered 409 at 06:46:17.343 and 06:46:53.444 (14.0 and 8.3 ms). That is when the operator window still
offered Approve on such a request; after the review it is disabled with the reason beside it. The operator rejected
it (06:47:03.838, 200, 8.5 ms) and the company asked for another recall (06:47:08.392, 201, 15.8 ms).

**The operator approves; the product opens with its link.**

| time | what happened | answer | took (ms) |
|---|---|---|---:|
| 06:47:28.343 | `/operator` renders for the operator (a company account is sent to `/app`): `GET /v1/operator/access` | 200 | 20.7 |
| 06:47:31.012 | "Approve": the decision, the product created from the notice and opened (`product_started`, `open`) | | |
| 06:47:31.019 | `POST /v1/operator/access/<id>` returns the outcome | 200 | 60.3 |

The window showed "Approved. The product is open and its claim link is ready." The company's next page view
(`/app`, 390 px) listed the product, open, with its own `intake_url`; `GET /v1/programs` reported
`takes_new_claims: true`.

## 2. What tests observe and the browser cannot

Each of these ran against the test database (`apps/api/tests`):

- **Worker fairness** (`test_companies_take_turns_and_a_draft_product_does_not_run`): three claims from one company
  and one from another are leased with the quiet company's claim among the first two, the oldest first within each
  company, and a draft product's claim never.
- **A spent model budget** (`test_a_spent_budget_falls_back_to_rules_and_closes_the_run_it_stopped`): the first tick
  raises `BudgetExhausted`, releases the claim with its attempt undone, and sets the fallback for an hour; the second
  tick closes the half-finished agent run as failed (`budget_fallback`) and the rules checker decides; a third tick
  finds nothing. With the fix disabled the claim stayed submitted and the test failed.
- **The agent unavailable** (`test_an_approval_the_rules_checker_reaches_without_the_agent_waits_for_a_person`): past
  the product's daily agent quota, a claim the rules checker would approve waits for a person (`under_review`,
  `hold_reason: agent_unavailable`) with no payout row. With the fix disabled it was approved and paid.
- **Refund deadlines** (`test_a_refund_nobody_chose_a_way_to_receive_becomes_a_check`,
  `test_a_hold_released_late_gives_the_claimant_a_fresh_window`): an unchosen refund past 14 days and a bank setup
  past 30 days become checks on the next payment tick; a refund a person releases after 20 days gets a fresh window.
- **The draft sweep** (`test_the_sweeper_skips_a_draft_someone_holds`): a draft whose row another transaction holds
  (a submit in progress) is skipped with its photo. Without the row lock the sweep blocked on that row.

## 3. Not observed yet

The same flows on the live site, with Clerk sign-in, a Stripe bank transfer, and Growth Checkout: added after the
rollout below.
