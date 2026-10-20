# Payments schema request for the backend owner

> Written by the payments owner for the backend review of PR 6, before the merge; kept as the record of that review. The backend owner adopted the PR on 2026-10-20 with the fixes in CLAUDE.md's decisions log. The source of truth is now the migration (apps/api/migrations/versions/0006_payments.py), docs/components/03-payments.md, and docs/contracts/openapi.json.

Status: **tested draft migration `0006_payments`, locally applied; backend-owner adoption/review pending**.
The user authorized local implementation/application on 2026-10-20; migration ownership remains with the backend.
The contract below is implemented through Alembic,
with forced RLS and restricted grants; there is no DDL startup hook or `metadata.create_all`.
See [the validation trace](../traces/M4-payments-stripe-sandbox.md) for passing checks and remaining gates.

Application declarations are in `billing/models.py` and `models.Payout`; the migration installs the constraints,
grants and functions that ORM declarations alone do not install. Run-cost ownership uses a run FK plus a
tenant-consistency trigger; claim usage has a composite run/claim/firm FK.

## Tables

| Table | Keys and columns | Required constraints/defaults |
| --- | --- | --- |
| `billing_accounts` | `firm_id uuid` PK/FK firms; `subscription_id text?`, `subscription_status text?`, `paid_access boolean`; `program_item_id text?`; `checkout_attempt uuid?`, `checkout_started_at timestamptz?`, `checkout_session_id text?`, `checkout_expires_at timestamptz?`, `checkout_request jsonb?`; `customer_started_at timestamptz?`; `quantity_version integer`, `quantity_synced_version integer`, `quantity_request jsonb?`, `last_sync_error text?` | Unique nonnull subscription and Checkout session IDs; paid_access false; quantity versions 0 and -1. The request snapshots are immutable for the same Checkout attempt/quantity version. |
| `billing_invoices` | `id text` PK (Stripe invoice); `firm_id uuid`, `subscription_id text`, `status text`, `attempt_count integer`, `amount_due_cents integer`, `amount_paid_cents integer`, `currency text`, `created_at timestamptz`, `period_start timestamptz`, `period_end timestamptz` | FK firm; nonnegative attempt counts and integer-cent amounts. Index `(firm_id, subscription_id, status)` and `(firm_id, created_at)`. |
| `claim_usage` | PK `(firm_id uuid, claim_id uuid, period_start date)`; `run_id uuid`, `counted_at timestamptz`, `growth_billable boolean` | Composite FKs to the same firm's claim and run; enforce that the run belongs to that claim. period_start is the UTC month start containing counted_at. Immutable first-result identity and eligibility; SELECT/INSERT only for app. Index `(firm_id, counted_at)`, `(firm_id, run_id)`. |
| `run_usage_costs` | `run_id uuid` PK; `firm_id uuid`, `period_start date`, `recorded_cost numeric(10,6)` | Run FK plus same-firm consistency trigger. recorded_cost defaults 0 and cannot be negative or decrease. The first accounting month is immutable. |
| `usage_reports` | PK `(firm_id uuid, day date)`; `request jsonb`, `started_at timestamptz`, `reported_at timestamptz?`, `last_error text?` | FK firm; started_at defaults now(). Persist request before outbound submission. Request and first started_at are immutable, including when reported_at remains null. App/job may update receipts/errors only. |

All five are tenant tables. Enable **and FORCE** RLS with `recallops_current_firm()`, appropriate tenant FK checks,
and minimum grants to recallops_app. The internal job role may enumerate firms but still binds an explicit firm
in each transaction. Migration code must account for FORCE RLS when backfilling; do not silently miss rows.

## Existing tables

- Add a unique partial index on `firms.stripe_customer_id` when nonnull. Report any existing duplicate mappings;
  never arbitrarily choose one firm. Unlinked firms stay free; do not invent paid Stripe identities for seed firms.
- Add nullable `payouts.connect_started_at`, `transfer_started_at`, `transfer_retry_at` (timestamptz),
  `transfer_request` (jsonb); add `connect_ready boolean NOT NULL DEFAULT false` and
  `stripe_reversed_cents integer NOT NULL DEFAULT 0`.
- Unique nonnull `payouts.connect_account_id` and `stripe_transfer_id`; check reversed cents between 0 and amount.
  Index recoverable payout status and retry time. Never backfill an old in-flight transfer as definitely failed.
- Preserve all current payout states and generated idempotency key. Freeze transfer_request once present, and
  amount/currency/destination thereafter. A retryable non-execution error may schedule another attempt with
  identical parameters; an unknown outcome must keep its original transfer_started_at.
- For existing monthly usage, request an explicit backend reconciliation/backfill from durable adjudication
  records. Historical totals alone cannot determine first-result date, run identity, or growth eligibility.
  Do not silently zero usage, fabricate paid eligibility, or claim history is reconciled.

## Narrow Connect resolver

Supply `recallops_firm_by_connect_account(account text) RETURNS uuid`, SECURITY DEFINER, with a fixed safe search
path and fully qualified table references. It must return only the firm owning the unique stored account ID,
never claimant data. Revoke PUBLIC execution; grant execution to recallops_app. The owner needs an explicit
policy to read payouts under FORCE RLS. Keep the API as recallops_app; do not grant BYPASSRLS or global SELECT.

The existing customer resolver and stripe_events record/processed functions remain in use. The webhook sets
transaction-local firm context only after resolving a provider identity. Event insertion, state changes, audit,
and processed_at then share one transaction.

## Owner acceptance

Run the expanded tenancy suite, caller-owned ledger rollback test, billing usage tests, Connect recovery tests,
all existing money/export tests, OpenAPI drift, and the full backend check with the new migration applied.
Verify constraints using recallops_app, not only a privileged fixture. Record migration revision and outputs.

Refund amount finalization is a separate backend prerequisite: the current portal chooses the notice maximum.
Implement and validate fixed/receipt-capped approved amounts and reviewer evidence upstream of the ledger.
Payments sends only the approved integer-cent ledger amount and does not evaluate prose amount rules.
