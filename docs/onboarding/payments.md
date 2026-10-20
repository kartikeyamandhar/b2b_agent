# Payments handoff: integrating components 1, 2 and 4

Use this guide when wiring product screens, auth, claim processing, schema or jobs to the payments build.
Read `CLAUDE.md` first, then your receiving-owner section below. Follow only the linked operational material
needed for that task. Payments runs in Stripe test mode for the life of this project.

## Start here

1. Read [contract changes](../contracts/CHANGES.md), especially items 13–16 and 20–29. Use
   [OpenAPI](../contracts/openapi.json) for exact request/response types; examples below explain their semantics.
2. Confirm the target database has migration `0006_payments`. The [backend section](#backend-owner) explains the
   already-migrated development database caveat. Use [runtime prerequisites](payments-runtime.md#prerequisites)
   when configuring Stripe or starting payment processes.
3. Implement your owner section and record its acceptance evidence. The final [acceptance record](#acceptance-record)
   separates receiving-owner sign-off from the payments tests that already passed.

The API, entitlement policy, signed webhooks, durable metering, payout processing, migration and development
console are implemented. The [validation trace](../traces/M-payments-stripe-validation.md) records sandbox
Checkout/worker resumption, Portal, delinquency/recovery, metering, quantity changes, transfers, crash recovery,
reversal and exports. It also distinguishes provider checks, local tests and owner-confirmed settings.
Product UI, login integration, upstream refund correctness and deployment require the receiving owners below.
CODEOWNERS still contains placeholder handles; the team must name receiving people before merge.
For folder boundaries, the tested migration draft and shared-file adoption, use the
[ownership review map](../contracts/payments-integration-review.md). Shared changes await backend acceptance.

## UI owner

Build in `apps/web`. Use `apiFetch` with the login owner's verified firm/role context. The existing
[development console](../../apps/api/src/recallops/api/static/dev_console.html) demonstrates the calls and redirects.
The browser uses the API; Stripe secret keys stay on the backend.

| User action / screen | Existing interface | Integration behavior |
| --- | --- | --- |
| Upgrade firm | Admin `POST /v1/billing/checkout`, no body → `{url}` | Navigate to the returned hosted Checkout URL. After return, refresh `GET /v1/usage`; only webhook-reconciled access enables Growth. |
| Manage subscription | Admin `POST /v1/billing/portal`, no body → `{url}` | Navigate to Customer Portal for payment methods, invoices and cancellation. Program quantity remains controlled by RecallOps. |
| Billing / limits / upgrade prompt | `GET /v1/usage` → `Usage` | Render `plan`, `effective_plan`, `subscription_status`, `limits`, `invoices`, `billing_sync_error` plus existing usage totals. Base access prompts on `effective_plan`; commercial plan and subscription status are separate. A null configured limit means unlimited. |
| Claimant chooses Stripe | Single-claim token `POST /portal/{token}/payout`, body `{"method":"stripe_connect"}` → `PortalView` | Navigate to nullable `connect_onboarding_url` when returned. Preserve the original claim context across return/refresh. Request a fresh link with the same POST when necessary; reopening the return URL does not establish readiness or delivery. |
| Claimant chooses check | Same payout POST, body `{"method":"check_export","mailing_address":"<synthetic address>"}` | Display the returned `payout_status`. The backend selects the existing check-export path. |
| Refund progress / reversal | `GET /v1/claims/{id}` → nullable `payout: PayoutView` and audit entries | Render payout status, `stripe_transfer_id` and cumulative `stripe_reversed_cents`. Keep the original delivery visible after a reversal and surface its audit alert. |
| Admin check export | `POST /v1/payouts/exports`, `GET /v1/payouts/exports`, `GET /v1/payouts/exports/{batch_id}.csv` | A creation result can be null when no payout is eligible. Download the stored batch; the backend owns claiming, amount totals and delivery transitions. |

Money fields are integer cents; `model_cost_usd` is a decimal string. A `transferred` refund proves a Connect
transfer, not bank arrival. Treat `transferring` as unresolved/in flight and `held` as awaiting reviewer action;
show the available status and audit information rather than offering another payment for that claim.
Account Links are ephemeral bearer URLs: keep them out of persistent application records and analytics.
Configure product return/refresh URLs through the settings listed in `.env.example`; dev-console defaults are
reference behavior, not product routes the UI must adopt.

Errors use RFC 7807 with `detail` and `correlation_id`. Honor the actual endpoint response: 400/422 require
correcting input, 401/403 require valid auth/role, 402 prompts for the required plan or supported check path,
404 means inaccessible/missing resource, 409 requires refreshing state, and 503 requires a recoverable error
screen with the correlation ID. Preserve operation/claim identity when recovering an uncertain request.

**UI acceptance:** demonstrate upgrade → refreshed Growth access, Portal → refreshed billing state, Connect
return/refresh → same claimant, check CSV download, and claim detail showing transfer/reversal information.
Show one access-limit error and confirm that a redirect alone never marks a firm paid or a claimant delivered.
Record the tested API revision and screenshots/trace; the receiving UI owner signs off on the product screens.

## Backend owner

1. Review/apply [migration 0006](../../apps/api/migrations/versions/0006_payments.py) against the
   [schema contract](../contracts/payments-schema-request.md). ORM declarations alone do not install the forced
   RLS, grants, immutable facts or identity resolvers. The API stays `recallops_app`; jobs use `recallops_internal`.
   Local databases upgraded from an earlier copy of this unmerged revision need the reviewed corrected function
   definition or rebuilding; Alembic will not rerun an already-recorded revision. The current local development/
   test databases received the correction; another checkout must verify its own database.
2. Keep the existing integration seams in `apps/api/src/recallops`:

   | Caller | Existing seam | Required behavior |
   | --- | --- | --- |
   | Program open/close | `programs.py`, `billing.service.mark_quantity_dirty` | Keep limit checks and quantity-version changes in the program transaction. The billing job synchronizes Stripe after commit. |
   | Claim worker/result effects | `claims/worker.py`, `claims/effects.py`, `BillingGate.record_usage(ctx, claim_id, run_id, model_cost_usd, session=...)` | Preserve claim/run identity, first-result accounting and all run-cost deltas. The backend deduplicates claims per UTC month. |
   | Actions requiring paid access | `BillingGate.check_entitlement(ctx, action, claim_id=None)` | Use the existing actions `open_program`, `adjudicate_claim`, `create_payout`. Preserve runtime `PlanBilling` and explicit test doubles. |
   | Approved refund / reviewer decision | `DbPayoutLedger.create_pending` and transition methods, optional caller-owned `session` | Commit approval and payout creation together. Pass the approved integer-cent amount and reviewer evidence. Keep claim-before-payout lock order; dispatch stays in the payment processor. |

3. Accept upstream fixed/receipt-capped refund calculations and above-threshold reviewer evidence. Stripe sends
   the approved ledger amount; payments does not evaluate notice prose. Decide historical usage reconciliation
   from durable adjudication records; prior aggregate totals alone cannot establish billable eligibility.

**Backend acceptance:** migration/constraints work under the app role; tenancy, money/export, usage and recovery
regressions pass; approved amounts and reviewer evidence are independently validated. Record a decision for old
usage (reconciled, or explicitly excluded with evidence). Preserve the existing delivery states and terminal
reversal behavior while integrating changes from the main branch.

## Login owner

1. Keep billing Checkout, Portal and export management behind verified firm-admin authorization. `GET /v1/usage`
   remains a firm read. Resolve the firm through the existing `AuthProvider`/`FirmContext` seam.
2. Implement the existing single-claim verifier seam for `/portal/{token}/*`. The claimant needs no firm login;
   its verified token supplies firm and claim identity. Retain stored-token-hash checks and portal rate limits.
3. Enforce reviewer-seat admission using the effective limits exposed in `Usage`, coordinated with the UI owner.
   Payment policy provides the limits; it does not implement membership admission.
4. Leave webhook tenancy with its verified Stripe identity resolvers and signature verification. A webhook is a
   provider request, not a logged-in firm's request. Preserve independent test-key enforcement in API and jobs.

**Login acceptance:** reviewer users cannot manage billing/exports; a valid admin can; invalid/expired/cross-claim
and cross-firm tokens fail; signed provider events still resolve their own firm; reviewer-seat limits follow
current effective access. Run the existing auth/security/tenancy tests plus the login owner's provider tests.

## Operations owner

Use [runtime prerequisites and resource setup](payments-runtime.md#prerequisites), then
[local processes](payments-runtime.md#local-processes-and-user-paths). Configure both snapshot and Accounts v2
thin destinations with their own secrets, the pinned API version, test prices, Portal configuration and product
return URLs. Select hosts for the adjudication worker, billing job and payout processor; no hosting was provisioned.

Use [recovery rules](payments-runtime.md#recovery-rules) for unresolved transfers, reporting ambiguity, holds and
reversals. Name an operator for these cases before unattended jobs run. Monitor stale `transferring` rows,
`billing_sync_error`, webhook errors and audit alerts. The test-account 24-hour invoice grace setting was
owner-confirmed; verify it separately in each target sandbox. Resource creation, funding, transfers, reversal
and cleanup require the scope of approval documented in the runtime guide.

**Operations acceptance:** record destination/job configuration and named reconciliation ownership; demonstrate
an event reaching the intended receiver and jobs progressing after restart. Use the opt-in isolated acceptance
harness only with approved sandbox operations. Retain its manifest until review; cleanup is restricted to its
own validated resources. Deployment is a separate action.

## Acceptance record

Use the [validation trace](../traces/M-payments-stripe-validation.md) for completed checks, dates, fixture
boundaries, skips and validation outputs. Prior payments verification is evidence for the build, not acceptance
of another owner's integration.

Receiving-owner acceptance remains open until each owner records:

`Owner name | role | tested commit/diff | acceptance checks above | evidence link | open blockers | date`

Use actual names when the team assigns them; role labels here do not imply another teammate has accepted.
A consumer changes the API contract only through backend-owner coordination and an updated committed OpenAPI.
Completion means every receiving role is accepted or has an explicit named blocker, not merely that payments
unit tests pass.

## Failure modes

A redirect treated as payment success can grant access or claim delivery prematurely; refresh authoritative API state.
Retrying/exporting an uncertain transfer can deliver twice; preserve its identity and follow reconciliation rules.
Losing firm/claim context on onboarding return can target the wrong record; verify the original token and tenant.
