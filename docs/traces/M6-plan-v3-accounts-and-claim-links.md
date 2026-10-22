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

## 3. The live site after the rollout (2026-10-22)

Rollout: the worker and both scheduled jobs suspended (the worker stopped beating about 3 minutes after the suspend
call, so the first wipe pass found a fresh heartbeat and stopped; a second pass finished), production wiped except the
recall archive (51 recalls) and the operator's Clerk account, migration 0009 applied, PR 16 merged (`1da17b5`), the
API deployed, the worker and jobs resumed and deployed, Clerk organizations turned off. Then a test company
(`kettle.works.demo+clerk_test@example.com`, created through Clerk's Backend API) and the operator drove the live API
with real Clerk session tokens; the claimant used the public routes. Times are the client's clock (UTC) unless
marked as an API log line.

| time | what happened | answer |
|---|---|---|
| 07:36:28.283 | the company's first `GET /v1/me` creates its company (not set up, Free) | 200 |
| 07:36:28.584 | `POST /v1/onboarding` on Free | 200 |
| 07:36:28.971 | `GET /v1/recalls?q=27024` finds the recall by its number | 200 |
| 07:36:29.314 | `POST /v1/access` for 27024 | 201, requested |
| 07:36:34.591 | the operator's `GET /v1/me`: operator, no company | 200 |
| 07:36:34.742 | the operator's list: Kettle Works Demo, Free, 0 products, a test address, verified | 200 |
| 07:36:35.376 | the operator approves: the product opens with its claim link | 200, product open |
| 07:37:12.828 | the claimant reads the product's public link | 200 |
| 07:37:13.055 | the claimant starts a claim | 201 |
| 07:37:13.876 to 07:37:14.693 | label, destruction, and receipt photos | 200 each |
| 07:37:14.927 | submit | 200, submitted |
| 07:37:15.551 | (agent run) the live worker starts the AI agent's run | |
| 07:37:29.383 | (agent run) the agent approves: 11 steps, 3 model calls, 2 photo reads, 0.001687 USD | |
| 07:37:37.664 | the claimant's page: approved, refund pending, choose by 2026-11-05 | 200 |
| 07:38:02.415 | the claimant chooses a bank transfer | 503 (see below) |
| 07:38:30.424 | the claimant chooses a mailed check | 200, pending_export |
| 07:38:42.656 | a second recall request on Free | 402, "Free covers one product. Upgrade to Growth to add another." |
| 07:40:31.930 | (API log) `POST /v1/billing/checkout` | 200 |
| 07:40:41.743 to 07:40:42.782 | (API log) Stripe delivers `invoice.paid` (200), `checkout.session.completed` and `customer.subscription.created` (503 each: the firm lock was busy with the first event) | |
| 07:41:03.449 | (API log) Stripe redelivers `checkout.session.completed` | 200 |
| 07:41:03.554 | the company: Growth, active, no product limit | 200 |
| 07:41:04.041 | the second recall request again, on Growth | 201 |
| 07:41:04.279 | the claim: paid, its payout exported (the check-export job, started by hand right after the check choice) | 200 |

The Growth Checkout was paid in headless Chrome on Stripe's hosted page with the test card 4242 4242 4242 4242 ($62.15
prorated to the end of October, then $199.00 a month).

**Not working yet: bank transfers.** Choosing a bank transfer answered 503 because Stripe refused to create the
claimant's account: `non_connect_platform_accounts_v2_access_blocked`. The Stripe sandbox has not been onboarded as a
Connect platform with Accounts v2. That is a dashboard step for the account owner (Connect onboarding at
dashboard.stripe.com/connect, and Accounts v2 under Settings, early access), not a code change. Before plan v3 the
free plan never offered bank transfers, so this had never run on the live site. The claimant's page keeps the mailed
check, which worked end to end.
