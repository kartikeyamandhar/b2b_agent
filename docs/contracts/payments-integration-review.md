# Payments integration: ownership review before merge

> Written by the payments owner for the backend review of PR 6, before the merge; kept as the record of that review. The backend owner adopted the PR on 2026-10-20 with the fixes in CLAUDE.md's decisions log. The source of truth is now the migration (apps/api/migrations/versions/0006_payments.py), docs/components/03-payments.md, and docs/contracts/openapi.json.

This is a review map for the proposed payments change set, prepared 2026-10-20. Recheck the PR diff and
CODEOWNERS when rebasing or changing scope. Task authorization to implement/test shared changes does not record
acceptance by the backend, UI or login teammate. Component-owner sign-off is still required.

## Rules and current differences

- **Contract:** `openapi.json` and CHANGES items 20–28 describe the proposed payment behavior. Review and merge
  contract edits with their API implementation; clients must target that same revision. The OpenAPI drift test
  checks agreement, not teammate approval. Breaking fields/protocols require receiving-owner coordination.
- **Folder:** payments owns `apps/api/src/recallops/billing/` and `payouts/stripe_connect.py`. The other paths below
  remain backend-owned under the current CODEOWNERS rules, including docs and tests. Their presence in this build
  is proposed integration work, not an ownership transfer. UI/login implementation folders are unchanged;
  onboarding document edits still need the documented owner review.
- **Migration:** the user explicitly authorized writing/applying 0006 for local verification after the initial
  schema request. Treat it as a tested draft for the backend owner to adopt, review and deliver. It has not been
  accepted merely because it migrated locally. The original schema request remains in payments-schema-request.md.
- **Data and money:** acceptance used synthetic claimants and Stripe sandbox resources. `.env` and `.tools/` are
  ignored. Credentials remain outside the versioned deliverable; `.env.example` holds fake setup values only.

## Merge path

1. The backend owner reviews/adopts the schema, shared ledger/billing seams, worker/program wiring, API and
   contract changes in this map. For databases created from the earlier unmerged 0006, verify the corrected
   constraint definition; a recorded Alembic version alone does not prove it contains the revised function.
2. Keep one coordinated integration review if the team permits cross-owner contributions. Require the backend
   review for shared paths and payments review for owned paths; preserve the tested code and schema together.
   CODEOWNERS currently has placeholder handles, so the team must assign actual reviewers.
3. If the team requires strictly separate folder contributions, the backend owner first adopts the shared
   prerequisite changes on their branch; payments then rebases onto them. Agree on that split before moving or
   reverting files. A payments-only checkout cannot supply the fields/hooks this build depends on by itself.
4. Run contract drift, tenancy/money regressions and the full check on the integrated revision. Record UI/login/
   operations acceptance through the payments handoff. Notify receiving teammates of the contract change before
   merge through the PR description and the team's chosen channel. Opening the PR does not record approval.

**Ready to merge:** actual reviewers are named; backend has accepted the migration/shared paths; contract and
implementation agree on the reviewed revision; integration checks pass; receiving-owner evidence or named
blockers is recorded. Updating this document alone does not satisfy that gate.

## Payments-owned changes

These are in the folder/file boundary assigned to the payments owner.

| File | Review owner |
| --- | --- |
| [apps/api/src/recallops/billing/bootstrap.py](../../apps/api/src/recallops/billing/bootstrap.py) | Payments |
| [apps/api/src/recallops/billing/client.py](../../apps/api/src/recallops/billing/client.py) | Payments |
| [apps/api/src/recallops/billing/fake.py](../../apps/api/src/recallops/billing/fake.py) | Payments |
| [apps/api/src/recallops/billing/interface.py](../../apps/api/src/recallops/billing/interface.py) | Payments |
| [apps/api/src/recallops/billing/jobs.py](../../apps/api/src/recallops/billing/jobs.py) | Payments |
| [apps/api/src/recallops/billing/models.py](../../apps/api/src/recallops/billing/models.py) | Payments |
| [apps/api/src/recallops/billing/policy.py](../../apps/api/src/recallops/billing/policy.py) | Payments |
| [apps/api/src/recallops/billing/service.py](../../apps/api/src/recallops/billing/service.py) | Payments |
| [apps/api/src/recallops/billing/usage.py](../../apps/api/src/recallops/billing/usage.py) | Payments |
| [apps/api/src/recallops/billing/webhooks.py](../../apps/api/src/recallops/billing/webhooks.py) | Payments |
| [apps/api/src/recallops/payouts/stripe_connect.py](../../apps/api/src/recallops/payouts/stripe_connect.py) | Payments |

## Shared changes requiring backend adoption/review

This list includes integration code, tests, contracts, operational configuration and documentation. For changes
to UI/login onboarding, also obtain the receiving teammate's content acceptance; CODEOWNERS still defaults
those files to the backend reviewer. None of these paths is reassigned to payments.

| File | Review owner |
| --- | --- |
| [.env.example](../../.env.example) | Backend |
| [.gitattributes](../../.gitattributes) | Backend |
| [.gitignore](../../.gitignore) | Backend |
| [CLAUDE.md](../../CLAUDE.md) | Backend |
| [Makefile](../../Makefile) | Backend |
| [README.md](../../README.md) | Backend |
| [apps/api/migrations/versions/0006_payments.py](../../apps/api/migrations/versions/0006_payments.py) | Backend |
| [apps/api/pyproject.toml](../../apps/api/pyproject.toml) | Backend |
| [apps/api/src/recallops/api/app.py](../../apps/api/src/recallops/api/app.py) | Backend |
| [apps/api/src/recallops/api/problems.py](../../apps/api/src/recallops/api/problems.py) | Backend |
| [apps/api/src/recallops/api/routes.py](../../apps/api/src/recallops/api/routes.py) | Backend |
| [apps/api/src/recallops/api/schemas.py](../../apps/api/src/recallops/api/schemas.py) | Backend |
| [apps/api/src/recallops/api/static/dev_console.html](../../apps/api/src/recallops/api/static/dev_console.html) | Backend |
| [apps/api/src/recallops/api/views.py](../../apps/api/src/recallops/api/views.py) | Backend |
| [apps/api/src/recallops/claims/effects.py](../../apps/api/src/recallops/claims/effects.py) | Backend |
| [apps/api/src/recallops/claims/worker.py](../../apps/api/src/recallops/claims/worker.py) | Backend |
| [apps/api/src/recallops/models/__init__.py](../../apps/api/src/recallops/models/__init__.py) | Backend |
| [apps/api/src/recallops/payouts/db_ledger.py](../../apps/api/src/recallops/payouts/db_ledger.py) | Backend |
| [apps/api/src/recallops/payouts/fake.py](../../apps/api/src/recallops/payouts/fake.py) | Backend |
| [apps/api/src/recallops/payouts/ledger.py](../../apps/api/src/recallops/payouts/ledger.py) | Backend |
| [apps/api/src/recallops/programs.py](../../apps/api/src/recallops/programs.py) | Backend |
| [apps/api/src/recallops/seed.py](../../apps/api/src/recallops/seed.py) | Backend |
| [apps/api/src/recallops/settings.py](../../apps/api/src/recallops/settings.py) | Backend |
| [apps/api/tests/api/test_api.py](../../apps/api/tests/api/test_api.py) | Backend |
| [apps/api/tests/api/test_m2_flows.py](../../apps/api/tests/api/test_m2_flows.py) | Backend |
| [apps/api/tests/api/test_payments.py](../../apps/api/tests/api/test_payments.py) | Backend |
| [apps/api/tests/conftest.py](../../apps/api/tests/conftest.py) | Backend |
| [apps/api/tests/db/test_billing_usage.py](../../apps/api/tests/db/test_billing_usage.py) | Backend |
| [apps/api/tests/db/test_connect_recovery.py](../../apps/api/tests/db/test_connect_recovery.py) | Backend |
| [apps/api/tests/db/test_evals_and_clis.py](../../apps/api/tests/db/test_evals_and_clis.py) | Backend |
| [apps/api/tests/db/test_ledger_retrieval_worker.py](../../apps/api/tests/db/test_ledger_retrieval_worker.py) | Backend |
| [apps/api/tests/db/test_money_paths.py](../../apps/api/tests/db/test_money_paths.py) | Backend |
| [apps/api/tests/db/test_tenancy.py](../../apps/api/tests/db/test_tenancy.py) | Backend |
| [apps/api/tests/manual/test_stripe_acceptance.py](../../apps/api/tests/manual/test_stripe_acceptance.py) | Backend |
| [apps/api/tests/manual/test_stripe_readonly.py](../../apps/api/tests/manual/test_stripe_readonly.py) | Backend |
| [apps/api/tests/unit/test_billing.py](../../apps/api/tests/unit/test_billing.py) | Backend |
| [docs/components/03-payments.md](../../docs/components/03-payments.md) | Backend |
| [docs/contracts/CHANGES.md](../../docs/contracts/CHANGES.md) | Backend |
| [docs/contracts/openapi.json](../../docs/contracts/openapi.json) | Backend |
| [docs/contracts/payments-integration-review.md](../../docs/contracts/payments-integration-review.md) | Backend |
| [docs/contracts/payments-schema-request.md](../../docs/contracts/payments-schema-request.md) | Backend |
| [docs/onboarding/login.md](../../docs/onboarding/login.md) | Backend |
| [docs/onboarding/payments-runtime.md](../../docs/onboarding/payments-runtime.md) | Backend |
| [docs/onboarding/payments.md](../../docs/onboarding/payments.md) | Backend |
| [docs/onboarding/ui.md](../../docs/onboarding/ui.md) | Backend |
| [docs/traces/M4-payments-local-validation.md](../../docs/traces/M4-payments-local-validation.md) | Backend |
| [docs/traces/M4-payments-postgres-setup.md](../../docs/traces/M4-payments-postgres-setup.md) | Backend |
| [docs/traces/M4-payments-stripe-sandbox.md](../../docs/traces/M4-payments-stripe-sandbox.md) | Backend |

## Failure modes

Merging only payments files leaves schema/hooks missing; review the dependency set as one working integration.
Regenerating OpenAPI without accepting its breaking changes can leave teammate clients on the wrong contract.
Dropping a locally applied migration file does not undo its database effects; coordinate schema ownership explicitly.
