# Onboarding: payments owner (component 3, Stripe test mode only)

You own firm billing (Stripe Billing, Checkout, Customer Portal, webhooks, entitlements) and claimant payouts (Connect Express, export fallback), per 03-payments.md. The backend owns the money tables and the ledger state; you plug into two Python protocols and one route.

## What exists today (end of M1)

- **Tables** (backend migrations; request schema changes with a note in the CLAUDE.md decisions log, only the backend owner adds migrations): `payouts` (with `idempotency_key` generated as the row id, so a transfer can never get a fresh key), `stripe_events` (event id is the primary key, which is your idempotency guard), `usage_meters`, and `firms.plan` / `firms.stripe_customer_id`. `stripe_events` is readable only by the internal (jobs) role, not by the API role.
- **Ledger seam**: `apps/api/src/recallops/payouts/ledger.py` defines `PayoutLedger` with `create_pending`, `mark_onboarding`, `mark_transferred`, `mark_failed`, `mark_exported`. The backend calls `create_pending` on approval, before any Stripe call (invariant 9). `payouts/fake.py` (`FakeLedger`) allows every transition and logs it; the backend implements the real state machine on the `payouts` table in M4, and your code calls the same methods.
- **Billing seam**: `apps/api/src/recallops/billing/interface.py` defines `BillingGate` with `check_entitlement(ctx, action)` (actions `open_program`, `adjudicate_claim`, `create_payout`) and `record_usage(ctx, claims_adjudicated, model_cost_usd)`. `billing/fake.py` (`AllowAllBilling`) always allows. The `billing/` package is yours (CODEOWNERS).
- **Routes**: `POST /internal/stripe/webhook`, `POST /v1/billing/checkout` and `POST /v1/billing/portal` are registered stubs returning 501; their contract is in docs/contracts/openapi.json. Replace the handlers in `apps/api/src/recallops/api/routes.py` (the backend owner reviews that file).
- `apps/api/src/recallops/payouts/stripe_connect.py` is a placeholder that is yours: the only module allowed to import both `stripe` (pinned 16.0.0) and the ledger.
- Settings already declare `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_API_VERSION` with fake values in .env.example. The dev auth provider refuses to start if any Stripe or Clerk value contains `_live_`, and that check is tested.

## The seam, concretely

1. Implement `BillingGate` in `billing/` (plan matrix from 03-payments.md section 2). Over-limit becomes HTTP 402 with a problem detail; never block reads.
2. Implement the webhook handler: verify the signature, then in one transaction insert into `stripe_events` (a conflict on id means a replay: commit nothing, return 200), apply the state change, set `processed_at`. Use the internal engine (`recallops.db.engine.internal_engine`); the webhook derives the firm from the Stripe customer or account id, never from a header.
3. Implement `stripe_connect.py` against `PayoutLedger`; transfers use `idempotency_key = payouts.id`.
4. Every money state change writes `audit_log`. Amounts are integer cents.

## Run the backend locally

```
cp .env.example .env            # put Stripe TEST keys in .env only, never in code or fixtures
docker compose up -d && make setup && make seed
make api                        # http://127.0.0.1:8000/docs
stripe listen --forward-to localhost:8000/internal/stripe/webhook   # Stripe CLI, test mode
```

## Tests that must stay green

`make check` (ruff, mypy strict, pytest with at least 85 percent coverage). In particular `tests/api/test_api.py::test_payment_fakes_allow_and_log`, `test_dev_provider_refuses_live_keys_and_prod`, `test_stubs_return_problem_details` (update it when you replace a stub), and the tenancy suite `tests/db/test_tenancy.py`. Put your tests under `apps/api/tests/` (unit tests without network; Stripe integration tests marked slow and run manually).

## Three commands to verify your work

```
make check                                                    # lint, types, all tests, coverage gate
stripe trigger checkout.session.completed                     # with stripe listen running: webhook applies once
stripe events resend <evt_id>                                 # the replay must be a no-op (one stripe_events row)
```
