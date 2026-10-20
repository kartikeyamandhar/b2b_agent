# Payments runtime and sandbox verification

Stripe **test mode only**. There is no production-money mode. No live Stripe account requests were made during
implementation. For integration responsibilities and receiving-owner acceptance, start with the [payments handoff](payments.md).

## Prerequisites

1. Apply Alembic migration `0006_payments` to development/test databases. It implements
   [the schema contract](../contracts/payments-schema-request.md); historical usage reconciliation and approved
   refund-amount correctness remain backend responsibilities. Do not call migrations from payment modules.
2. Put test keys and endpoint secrets in `.env`; never paste them into logs or fixtures. The API and both jobs
   reject non-test key prefixes through Settings. All Stripe responses/events must be test-mode objects.
3. Keep the API version at `2026-09-30.endive`. Configure snapshot webhook delivery to match, using
   STRIPE_WEBHOOK_SECRET. Accounts v2 thin notifications are unversioned and use STRIPE_CONNECT_WEBHOOK_SECRET.
   Configure both listener/destination secrets independently; never reuse a secret from another CLI session.
4. Obtain explicit approval before resource creation, funding, account calls, or deploying. An opt-in command
   flag is not itself approval. Dependency downloads alone do not authorize Stripe account operations.

## Test resource setup

From the repository root, after approval:

```bash
make billing-bootstrap
# Copy only the returned price/configuration IDs into .env.
make billing-bootstrap ARGS="--fund-key <unique-test-funding-operation-id>"
stripe listen \
  --events checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted,invoice.paid,invoice.payment_failed,transfer.created,transfer.reversed \
  --forward-to localhost:8000/internal/stripe/webhook \
  --forward-connect-to localhost:8000/internal/stripe/webhook
# In another terminal, use its signing secret for STRIPE_CONNECT_WEBHOOK_SECRET:
stripe listen \
  --events 'v2.core.account.updated,v2.core.account[configuration.recipient].updated,v2.core.account[configuration.recipient].capability_status_updated,v2.core.account[requirements].updated' \
  --forward-to localhost:8000/internal/stripe/webhook
```

The fund option makes a 100,000-cent **test** PaymentIntent using `pm_card_bypassPending`. It never runs implicitly.
Confirm the resulting test available balance in Stripe before claiming transfer readiness. Reusing the funding
key is safe only inside Stripe's retention window; do not rerun an old funding operation without checking Stripe.

Configure Accounts v2 thin notifications separately from Billing/transfer snapshots. Verify these CLI
options against the installed CLI's `stripe listen --help`; Stripe CLI 1.54.1 was installed and both selections were verified during acceptance.
The listener uses the account's default snapshot API version; set that test-account version to
`2026-09-30.endive` before forwarding, or use a configured test webhook destination with that version. Do not
use `--latest`: the receiver deliberately rejects events whose API version differs from the pinned contract.
Configure a 24-hour subscription-invoice finalization grace period in the Stripe test Billing settings before
the usage demo. This Dashboard setting is not silently changed by bootstrap.

The Customer Portal configuration permits payment-method management, invoice history, and cancellation at period
end. Program quantity is controlled by RecallOps; in-portal quantity/price changes are disabled.

Connect creates Accounts v2 recipients with `dashboard=express`, `identity.country=US`, and
`defaults.responsibilities` fees/losses collectors set to `application`. The platform profile must permit
loss responsibility for separate charges/transfers; changing to Stripe-collected losses breaks this funds flow.
See [Stripe's account configuration guidance](https://docs.stripe.com/connect/risk-management/connected-accounts-with-managed-risk).
Claimant email must already be present. The account response must include `configuration.recipient`; readiness
requires both recipient Stripe-balance transfer and payout capability statuses to be active. Account Links use
the Accounts v2 `use_case.account_onboarding` flow. Legacy Accounts v1 creation/readiness events are removed.

## Local processes and user paths

```bash
make api
make worker                 # normal adjudication; use existing fake/replay settings unless separately approved
make billing-jobs ARGS="--loop"
make connect-payouts ARGS="--loop"
# One-shot catch-up/diagnosis:
make billing-jobs ARGS="--day YYYY-MM-DD"
make connect-payouts
make export-payouts
```

The two payment loops default to 30-second polling. They are independent from the adjudicator. Run them on an
explicitly chosen worker host; this change does not provision paid services or claim that hosting is configured.

Open `/dev/console#/payouts`. Checkout and Customer Portal navigate to Stripe URLs. Use test card
`4242 4242 4242 4242`, a future expiry and test CVC for the success path. Connect uses Stripe-hosted US Express
onboarding, synthetic identity, and test bank routing `110000000`, account `000123456789`, SSN test values
`0000` (last four) or `000000000` if the hosted test form requests them. These values come from the [Stripe Connect testing guide](https://docs.stripe.com/connect/testing); confirm any additional fields in the
hosted test form. The owner completed synthetic hosted onboarding during acceptance; no real consumer identity was used.

Connect return/refresh goes back to the dev console. A same-tab sessionStorage entry retains claim context and
is removed on return. A missing context requires reopening the original claim link. Returning alone never marks
the account ready. Account Links are short-lived, returned only from the payout POST, and never persisted.

## Recovery rules

- A committed payout and successful `claim_for_transfer` precede every transfer request. Parameters and UUID key
  are immutable. Integer cents remain integers through the provider boundary.
- A timeout, transport error, 409, or 5xx remains transferring. Reconciliation fully paginates transfers for the
  account/program and checks local metadata, amount, currency and destination. Same-key replay stops before 24h;
  after 23h, an unknown outcome requires operator review. An empty list never authorizes check export.
- Rate limiting before endpoint execution is retryable at one then five minutes; three confirmed non-execution
  failures select export. Cached/terminal rejections select export without fake additional attempts. Credential
  errors require operator attention and do not export the refund.
- Holds before claim prevent dispatch. An in-flight hold is recorded as hold_requested; real success is recorded
  with an alert, and confirmed failure lands held. Only reviewer reapproval releases a hold.
- Partial/full reversals record cumulative reversed cents and audit alerts. They never reopen transfer/export
  eligibility. MPR remains first-delivery based, not a measure of net funds after reversal.
- Closed-day usage is snapshotted at/after 00:15 UTC. Successful receipts are permanent. An unknown reporting
  outcome outside the safe retry window must be checked against Stripe; never blindly resend it.
- A report accepted by Stripe is an ingestion receipt, not proof it reached an invoice. Confirm asynchronous
  meter totals and invoice preview before declaring billing acceptance.

## Verification for another environment

Run `make check` and CI's 85% coverage gate after applying migration 0006. Capture timestamped real test-account
traces for Checkout/replay, two failed collection attempts/recovery, 1,001 eligible claims (25 cents overage),
program quantity convergence, Express onboarding/transfer, process interruption/reconciliation, held transfer,
reversal alert, and check export. Include sanitized provider IDs, local payout/audit/event IDs, and row counts.

Do not fabricate these traces from mocks. See [the current validation trace](../traces/M-payments-stripe-validation.md).
For explicitly approved read-only Stripe checks, from apps/api run:

```powershell
$env:RUN_STRIPE_TESTS='1'
$env:STRIPE_TEST_ACCOUNT_ID='<completed synthetic account ID>'
.venv/Scripts/python.exe -m pytest tests/manual/test_stripe_readonly.py -q
```

Keys and price/configuration IDs are loaded from gitignored `.env`; tests never print keys. The default test run
skips these network checks and forces fake Stripe credentials. On 2026-10-20 the owner approved the bounded acceptance run below, replacing read-only mode. That approval does not authorize unrestricted future runs.


## Reproducible isolated acceptance

Stripe CLI 1.54.1, Python 3.13.12 and the pinned Postgres/Neo4j images were used on 2026-10-20.
Use a dedicated local database named `recallops_payments_acceptance`, bootstrap its three roles with
`apps/api/sql/bootstrap_roles.sql`, and apply Alembic through `0006_payments` as the owner role. Never point
this harness at ordinary development data. It validates loopback database hosts and fixes the database name.
Keep test keys/prices in the root ignored `.env`. Default pytest runs make no Stripe calls.

From `apps/api` (PowerShell; set `PYTHONPATH=src` or use `uv run --frozen python`):

```powershell
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance init --allow-writes
# Copy the returned run UUID, not any key or onboarding URL.
$run='<run UUID>'
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance listen --run $run
# Separate terminals, same run:
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance serve --run $run
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance worker --run $run
```

If Stripe CLI is outside PATH, set `STRIPE_CLI_PATH` to its installed executable. Authentication is passed in
the child environment, never CLI arguments. The listener captures its two signing secrets into ignored
`endpoints.env`; start the API only after both readiness messages. Restart it when listener secrets change.
The API binds only `127.0.0.1:18000`. The worker uses the normal production loop with a two-second test poll.
The harness's sole database contains only this acceptance run; payment commands still target a registered
payout explicitly, instead of running a database-wide money dispatcher.

Open `http://127.0.0.1:18000/dev/console?firm=<firm UUID>#payouts` and upgrade with Checkout. A human completes
the hosted test-card form. Verify access and the genuine event, then continue the approved operations:

```powershell
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance verify --run $run
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance quantity --run $run --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance delinquency --run $run --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance cancellation --run $run --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance onboarding --run $run --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance fund --run $run --allow-writes
# Complete each hosted synthetic recipient before its dispatch. Indexes are 0, 1, 2.
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance payout --run $run --index 0 --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance payout --run $run --index 1 --crash before_send --allow-writes
# The deliberate child termination is expected. Re-run without --crash to reconcile the same payout.
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance payout --run $run --index 1 --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance payout --run $run --index 2 --crash after_success --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance payout --run $run --index 2 --allow-writes
.venv/Scripts/python.exe -m tests.manual.test_stripe_acceptance reverse --run $run --index 1 --allow-writes
$env:RUN_STRIPE_ACCEPTANCE='1'
$env:STRIPE_ACCEPTANCE_RUN_ID=$run
.venv/Scripts/python.exe -m pytest tests/manual/test_stripe_acceptance.py -q
```

`--allow-writes` is a guard, not authorization. Obtain approval for another run. The funding command adds at
most one 100,000-cent simulated PaymentIntent per run and skips it if the test balance already suffices.
Keep its persisted state for reconciliation. Never delete state to retry an uncertain write. All three
synthetic refunds use 4,999 cents and separate recipients; the reversal action permits one 1,000-cent
operation across the run. Fault injection exists only in a child test process, never production settings.
Hosted URLs stay in ignored files and expire; re-run onboarding to refresh them, retaining the accounts.

The actual first acceptance payout hit a confirmed insufficient-balance rejection before funding. Its cached
failure correctly selected check export after the constraint fix; it was not retried with another key.
`export --run $run --allow-writes` verifies that run's fallback CSV. Future successful runs need not manufacture
a failure: real-Postgres regressions cover confirmed-failure export, uncertain outcomes, holds and concurrency.
The acceptance delivery test accepts either a verified transfer or an export for each payout and forbids both.
`free-export --run $run --allow-writes` additionally verifies the other firm's free-plan check route and CSV with
only local fixture writes; it creates no Stripe account, transfer or charge.

Stripe's invoice `attempt_count` follows automatic retries. Manual retries after the first do not increment it;
the test-clock scenario advances to the provider's next scheduled attempt. It never counts webhook deliveries.
The existing 1,001-claim fixture can be rechecked read-only by setting `STRIPE_TEST_METER_CUSTOMER` and
`STRIPE_TEST_METER_SUBSCRIPTION` with `RUN_STRIPE_TESTS=1`, then running `test_stripe_readonly.py`.
This checks invoice aggregation independently of the durable local reporting-receipt regression.

## Integration and owner acceptance

Use the [payments handoff](payments.md) for UI, backend, login and operations integration steps and acceptance
criteria. It is the authoritative receiving-owner checklist; this file owns runtime commands and recovery.

Cleanup is deliberately not automatic: retain the run manifest until evidence is accepted. Only this run's
customer/subscription/test-clock/recipient identities may be cleaned up, after validating their run metadata;
retain local event/audit/reporting facts for the trace. Existing earlier billing fixtures are outside this run.
No cleanup command or provider deletion was executed during the acceptance run.
