# Component 3: Payments (Stripe, test mode only)

Owner of: firm subscriptions and metered billing (Stripe Billing), plan entitlements, webhook handling, claimant payouts (Stripe Connect Express), the payout export fallback, and the billing UI pieces. Money state lives in the backend tables defined in 01-backend.md (payouts, stripe_events, usage_meters, firms.plan).

Read CLAUDE.md first. Invariants 8 and 9 govern this component.

## 1. Two money flows, kept separate

Flow A, money in: the firm pays RecallOps. Stripe Billing, Checkout, Customer Portal, webhooks.
Flow B, money out: RecallOps pays a consumer an approved refund on the firm's behalf. Stripe Connect Express in test mode, with a ledger-first design and an export-file fallback.

Never mix the two in code. apps/api/src/recallops/billing/ handles A. apps/api/src/recallops/payouts/stripe_connect.py handles B and is the only module that imports both stripe and the payouts ledger.

## 2. Plans and entitlements

Defined in settings, mirrored as Stripe Products and Prices in test mode by a one-time script (`python -m recallops.billing.bootstrap`), ids stored in settings after creation.

- free: 1 open program, 100 adjudicated claims per month, 1 reviewer seat, no payouts (ledger export only). No card required.
- growth: 199 USD per month per open program (quantity = open programs, updated on program open and close), plus 0.25 USD per adjudicated claim above 1,000 per month (metered price, usage reported daily from usage_meters). Payouts enabled.
- enterprise: not purchasable in-app; a flag set manually. Treated as growth with no limits.

Entitlement checks are a single function `check_entitlement(firm, action)` called by the backend before: opening a program, adjudicating a claim (the worker checks before each run), creating a payout. Over-limit on free returns a 402 with a problem detail that the UI turns into an upgrade prompt. Never block viewing data.

## 3. Checkout and portal

- POST /v1/billing/checkout: creates a Stripe Checkout Session in subscription mode for the growth plan, quantity = current open programs or 1, success and cancel URLs back to /app/billing, client_reference_id = firm id, customer created or reused from firms.stripe_customer_id.
- POST /v1/billing/portal: creates a Customer Portal session for plan changes and cancellation.
- The UI (02) shows plan, usage this period (claims adjudicated of limit, open programs of limit, model spend), and the two buttons.

## 4. Webhooks (idempotent, invariant 8)

Route POST /internal/stripe/webhook. Verify the signature with the endpoint secret. Then, inside one database transaction:
1. INSERT INTO stripe_events (id, type, received_at, payload). If the insert conflicts on id, commit nothing and return 200. This is the idempotency guard.
2. Apply the state change for the event type.
3. Set processed_at. Commit.

Handled events:
- checkout.session.completed: link customer id and subscription id to the firm; set plan growth.
- customer.subscription.updated and deleted: update plan and status; on deleted, set plan free and close payouts capability.
- invoice.paid and invoice.payment_failed: record for the billing page; on payment_failed twice, mark plan past_due and block new programs.
- account.updated (Connect): update the claimant's onboarding status on the payout row.
- transfer.created, transfer.reversed (Connect): update payouts.status.

Usage reporting: a daily job sends the day's adjudicated claim count per firm to the metered price with an idempotency key of firm id plus date. Re-running the job for the same day is a no-op.

Local testing: Stripe CLI `stripe listen --forward-to localhost:8000/internal/stripe/webhook`; `stripe trigger checkout.session.completed` and friends. Document the exact commands in README. Test cards: 4242 4242 4242 4242 for success, 4000 0000 0000 0341 for attach-then-fail.

Runtime trace required: one checkout end to end (session created, redirect, webhook received, plan changed) with timestamps and the stripe_events row, and one replayed webhook proving the no-op.

## 5. Claimant payouts (Connect Express, test mode)

Ledger first (invariant 9). When the adjudication graph approves a refund claim, the backend creates payouts(status=pending, amount_cents from remedy rules, idempotency_key = payouts.id). Nothing in Stripe yet.

Then, by firm plan:
- free: status pending_export. A nightly job writes an export file (CSV, one row per pending_export payout: claim id, name, address, amount) under the firm's storage prefix, marks rows exported, and the console shows a download. This simulates check or ACH batch files.
- growth and enterprise: the portal's payout step creates a Connect Express account for the claimant (test mode), stores the account id on the payout row (status onboarding), and sends the claimant to the Account Link onboarding flow. Stripe test mode accepts test identity data; document the exact test values. On account.updated with payouts enabled, the backend creates a Transfer with idempotency_key = payouts.id and transfer_group = program id, sets status transferred, stores stripe_transfer_id. Failures set status failed with last_error and increment attempts; a retry job retries failed rows up to 3 times with backoff; after that the row flips to pending_export and the fallback takes over.

Transfers come from the platform balance. In test mode, fund the platform balance with a test charge in the bootstrap script so transfers succeed; document this.

Amount rules: refund amount comes from remedy_rules.options[].payout.amount_rule, evaluated by the backend (fixed amount, or receipt amount capped at the notice's price range). A payout above the program's value threshold waits for reviewer approval before any Stripe call (the adjudication graph already interrupts for these; the payout module re-checks).

Runtime trace required: one payout through onboarding to transfer in test mode, and one through the export fallback, with timestamps.

## 6. Security and correctness rules

- Webhook route is exempt from Clerk auth and from tenant scoping; it derives the firm from the event's customer or account id, never from a header.
- All Stripe calls use the Python SDK with the pinned API version set explicitly in settings; never rely on the account default.
- Amounts are integers in cents everywhere. No floats touch money.
- Every money state change writes audit_log.
- Keys: test keys only, from environment. A startup check refuses to boot if a live key prefix is detected.

## 7. Tests

- Unit: entitlement matrix (plan x action), amount rule evaluation, idempotent webhook insert, payout state machine transitions (every legal and illegal edge).
- Integration (against Stripe test mode, marked slow, run manually and in the M3 and M4 milestone checks): checkout session creation, webhook signature verification with a recorded payload, Connect account creation and transfer with a funded test balance.
- Replay: a recorded set of webhook payloads applied twice yields identical database state.

## 8. Acceptance

- A free-plan firm hitting 101 adjudicated claims in a month sees the 402 response and the upgrade prompt; after a test checkout completes, the worker resumes within one polling interval.
- A duplicate webhook delivery changes nothing and returns 200.
- An approved refund claim on growth ends in payouts.status = transferred with a Stripe transfer id visible in the console; on free it ends in an export file row.
- No code path can create a Stripe Transfer without an existing payouts row.
