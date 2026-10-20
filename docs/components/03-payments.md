# Component 3: Payments (Stripe test mode only)

Owner: firm Billing and claimant Connect integration. The backend owns migrations, approved refund amounts,
ledger state transitions and export batches. The UI/login owners own product UI and reviewer-seat admission.
Read CLAUDE.md first, then contracts/CHANGES.md items 13-16 and 20-29.
When consuming this component from UI, auth, backend or operations, follow the role-specific
[payments handoff](../onboarding/payments.md).

## Money in: subscriptions

Billing code lives in apps/api/src/recallops/billing. Free: one open program, 100 adjudicated claims per UTC
calendar month, one reviewer seat, export-only refunds. Growth: 19,900 cents/month per open program with a
one-program minimum, plus 25 cents per eligible claim above 1,000/month. Enterprise is manually managed,
unlimited, and never automatically subscribed. Values are Settings defaults, mirrored into test prices.

Checkout creates/reuses a customer and durable pending session; Customer Portal manages payment details and
period-end cancellation. Return navigation grants no entitlement. A paid active subscription grants growth;
first collection failure retains access, second failed attempt on an unpaid subscription invoice applies free
entitlements. Incomplete, incomplete_expired, unpaid, paused, canceled and unexpected trialing are free.
Reconcile current provider state, never count webhook deliveries as collection attempts. Manual enterprise
access is preserved. Existing data remains readable.

Program open/close persists a quantity version in the program transaction. Billing jobs converge Stripe quantity
to max(1, open programs), with prorations on the next invoice. Checkout anchors the next full cycle to UTC month
start and prorates the initial period. Do not offer provider-side quantity editing.

Usage counts each claim once per UTC month at its first persisted result, including human review. Resumes,
retries and reopening that month add no claim; model costs retain per-run deltas. Only growth-time claims are
reported to Stripe. The daily closed-day report freezes request/identity and retains receipts permanently.
The included 1,000 units are applied by Stripe's graduated price, once per month. Configure 24-hour invoice
finalization grace; verify asynchronous meter ingestion reached the invoice before claiming billing success.

## Money out: approved claimant refunds

payouts/stripe_connect.py is the only module importing both Stripe and the payout ledger. A payout exists inside
the approval transaction before any Stripe request. It sends only the approved integer-cent amount; fixed and
receipt-capped calculation and above-threshold reviewer evidence must be correct upstream.

Growth creates/reuses a US Accounts v2 recipient with an Express dashboard and returns an ephemeral Account Link.
Claimant email is required. Recipient stripe_balance.stripe_transfers and stripe_balance.payouts must both be
active. Fees/losses are application-collected, which the Stripe platform profile must permit. Account events update
readiness; a polling job rechecks capability, plan, amount, approval and holds before claiming a transfer.
The claim lock precedes the payout lock. Commit claim_for_transfer before sending the immutable request with
idempotency_key=payout UUID and transfer_group=program UUID. No transfer is sent in the webhook transaction.

States remain pending, pending_export, onboarding, transferring, transferred, failed, exported, held. Growth
waits pending/onboarding until ready, explicitly choosing a check, or confirmed failure. An existing waiting check
remains a check after an upgrade. Delivered states remain terminal. A reversal updates cumulative reversed cents
and an audit alert, never automatic repayment; MPR remains first-delivery based.

Unknown outcomes (timeout, transport error, 409, 5xx) stay transferring, outside export. Reconcile success from a
verified response/event or fully paginated matching transfers. Same-key replay is limited to 23 hours; after that
manual reconciliation is required even if no transfer appears in a list. Retry only confirmed non-execution
failures, with one/five-minute backoff and three-failure export limit. Terminal rejection can select export early;
cached responses are not invented extra attempts. Credential failures require operator attention.

A hold before claiming blocks transfer. A hold after claiming sets hold_requested: confirmed failure lands held,
real success is recorded with an alert. Only reviewer reapproval releases a hold. Every money change is audited.
The fallback is the existing transactionally stored export_batches CSV, never an authoritative disk file.

## Webhooks and security

Use raw-body signature verification and matching 2026-09-30.endive snapshot API version. Accounts v2 thin
notifications are unversioned and use a separate STRIPE_CONNECT_WEBHOOK_SECRET; Billing/transfer snapshots use
STRIPE_WEBHOOK_SECRET. Resolve the related account ID and retrieve current recipient capabilities. Reject live keys at
Settings validation in API and jobs, and live provider objects at every money boundary. The API uses recallops_app.
Customer/Connect account resolution uses narrow SECURITY DEFINER functions, followed by transaction-local RLS.
Never resolve tenancy from headers or trust metadata without checking the committed payout/account.

Event insert, state application, audit and processed_at share one transaction. Replay is a no-op. Serialize by
firm and retrieve current provider objects to handle reordered subscription/account/transfer events. An old
subscription cannot replace a newer active subscription. Failed application rolls back the event guard too.

## Verification and current dependencies

Migration `0006_payments` installs [the payment schema](../contracts/payments-schema-request.md), following the
owner's explicit authorization. See [payments-runtime.md](../onboarding/payments-runtime.md) for commands and
[the Stripe/database validation trace](../traces/M-payments-stripe-validation.md) for actual evidence and limits.
Sandbox transfer/reversal delivery, forwarded webhooks and fresh-checkout validation are recorded in that trace.
Receiving-owner acceptance and deployment remain separate gates; use the handoff to record integration sign-off.
