# Onboarding: payments owner (component 3, Stripe test mode only)

You own firm billing (Stripe Billing, Checkout, Customer Portal, webhooks, entitlements) and claimant payouts through Connect Express, per 03-payments.md. The backend owns the money tables, the ledger state machine, and the export fallback; you plug into two Python protocols and three routes. Read **docs/contracts/CHANGES.md** items 13 to 16 first: the ledger protocol changed.

## What exists (backend M2 to M5)

- **Tables** (only the backend owner adds migrations; request changes with a decisions-log note): `payouts` (status pending, pending_export, onboarding, transferring, transferred, failed, exported, held; `idempotency_key` is the row id; `connect_account_id`, `export_batch`, `hold_requested`), `export_batches` (the check/ACH batch CSV, stored in Postgres), `stripe_events` (event id is the primary key: your idempotency guard), `usage_meters`, and `firms.plan` / `firms.stripe_customer_id`.
- **Ledger** (`payouts/ledger.py` protocol, `payouts/db_ledger.py` implementation, `payouts/fake.py` for unit tests):
  - `create_pending(ctx, claim_id, amount_cents, method, session=None)`: the backend calls it inside the approval's transaction. A free plan's payouts start in `pending_export` (03-payments.md section 5).
  - `claim_for_transfer(ctx, payout_id)`: call it immediately before `stripe.Transfer.create(idempotency_key=payout_id)`. It moves the row to `transferring` and raises `NotPayable` unless the claim is approved and the payout is pending, onboarding, or failed. This is what stops a held payout from being paid.
  - `mark_onboarding(ctx, payout_id, connect_account_id)`, `mark_transferred(ctx, payout_id, transfer_id)` (from transferring; from held it is recorded as a late result with an alert, never refused; the claim becomes paid), `mark_failed(ctx, payout_id, error)` (one transaction; after 3 failures the row flips to pending_export; a hold requested during the transfer lands it in held), `to_export`, `mark_exported`.
  - Every transition locks the claim row, then the payout row, and writes audit_log. Keep that order.
- **Export fallback** (`payouts/export.py`, `make export-payouts`, and `POST /v1/payouts/exports` for an admin): one transaction per firm claims `pending_export` rows of approved claims with `FOR UPDATE SKIP LOCKED`, stores a formula-safe CSV in `export_batches`, marks the payouts exported and the claims paid.
- **Billing seam** (`billing/interface.py`, yours): `BillingGate.check_entitlement(ctx, action)` for `open_program`, `adjudicate_claim`, `create_payout`, and `record_usage`. `billing/fake.py` (`AllowAllBilling`) allows everything. The worker skips an over-limit firm for one polling interval instead of blocking every firm.
- **Routes still 501 for you**: `POST /internal/stripe/webhook`, `POST /v1/billing/checkout`, `POST /v1/billing/portal`, and `POST /portal/{token}/payout` with `method: stripe_connect` (the check path works). Contracts are in docs/contracts/openapi.json; replace the handlers in `api/routes.py` (the backend owner reviews that file).
- `payouts/stripe_connect.py` is your placeholder: the only module allowed to import both `stripe` (pinned 16.0.0) and the ledger.

## The seam, concretely

1. Implement `BillingGate` (plan matrix in 03-payments.md section 2). Over-limit is a 402 problem detail; never block reads.
2. Implement the webhook as `recallops_app` (the API never uses the internal role: `tests/api/test_security.py` fails if API code imports it). Verify the signature, then in one transaction: `SELECT recallops_record_stripe_event(id, type, payload)` (false means a replay: return 200 and change nothing), apply the state change through the ledger, `SELECT recallops_mark_stripe_event_processed(id)`. Resolve the firm with `recallops_firm_by_stripe_customer(customer)`, never from a header.
3. Implement `stripe_connect.py`: `claim_for_transfer` first, then the transfer with `idempotency_key = payouts.id`, then `mark_transferred` or `mark_failed` from the webhook.
4. Every money state change writes audit_log; amounts are integer cents.

## Run the backend locally

```
cp .env.example .env            # Stripe TEST keys in .env only, never in code or fixtures
docker compose up -d && make setup && make seed
make api                        # http://127.0.0.1:8000/docs
stripe listen --forward-to localhost:8000/internal/stripe/webhook   # Stripe CLI, test mode
```

## Tests that must stay green

`make check` (ruff, mypy strict, pytest with the 85 percent coverage gate in CI). In particular `tests/db/test_ledger_retrieval_worker.py` (state machine, holds, in-flight transfers), `tests/db/test_money_paths.py` (concurrent exports pay once, a flagged claim's payout is never exported, firm skipping), `tests/api/test_api.py::test_errors_are_problem_details_and_payments_stubs_stay_501` and the stripe_connect 501 in `tests/api/test_m2_flows.py` (update both when you replace a stub), and the tenancy suite.

## Three commands to verify your work

```
make check                                                    # lint, types, all tests
stripe trigger checkout.session.completed                     # with stripe listen running: the webhook applies once
stripe events resend <evt_id>                                 # the replay is a no-op (one stripe_events row)
```
