# Payments local validation: 2026-10-20

Status: implementation and offline validation only. No real Stripe customer, subscription, Connect account,
transfer, funding operation, or meter event was created. No migration was added or edited. No deployment or
Git commit was made. This record does not establish payment-component acceptance.

## Environment and checks

Python 3.13.12 and the locked dependencies were downloaded after explicit user approval. Validation used the
project virtual environment on Windows. FastAPI TestClient needed execution outside the restricted sandbox
because the sandbox blocked its local socket pair; this did not authorize external Stripe calls.

| Check | Observed result |
| --- | --- |
| Ruff check, `src tests migrations ../../docs/assets/src`, from `apps/api` | Passed |
| Ruff format check for the same paths | 169 files already formatted |
| Strict mypy with `--platform linux` (CI platform) | Passed, 162 source files |
| Offline unit tests plus payment API and development-console tests | 307 passed, 1 skipped, 2 deselected |
| OpenAPI drift | Passed within payment API tests; committed schema has 32 paths |
| Billing bootstrap, billing jobs, payout runner `--help` | Passed; no provider calls |
| Development-console inline JavaScript parse via Node | Passed, one inline script |
| `git diff --check` | Passed |
| Offline coverage | 57.33%; the 85% CI gate is not established |

The skipped test requires an absent cached CPSC feed. Two existing storage tests were deselected because they
require Windows symlink privileges and POSIX `os.geteuid`, respectively. Seven frozen adjudicator files were
verified against the committed frozen hashes and restored to their exact committed LF bytes; `.gitattributes`
now preserves those bytes. No frozen adjudication logic, golden data, or freeze manifest was changed.

The offline command was:

```powershell
# From apps/api
.venv/Scripts/python.exe -m pytest tests/unit tests/api/test_payments.py tests/api/test_dev_console.py -q -m 'not db and not graph' --deselect tests/unit/test_storage.py::test_local_storage_refuses_a_key_that_resolves_outside_the_root --deselect tests/unit/test_storage.py::test_local_storage_wraps_os_errors --basetemp ../../.tools/pytest-final --cov=recallops --cov-report=json:../../.tools/offline-coverage.json --cov-report= --tb=short
```

After that run, a nullable Stripe invoice-parent extraction was hardened. Direct wire-shape probes passed for
null parent, null subscription details, and a real nested subscription identity. Final focused checks are
also complete: all 38 payment unit/API and development-console tests passed in 2.01 seconds after that change.
Ruff, formatting, and strict mypy passed again. The broad test count above describes the observed broad run.

## Mocked HTTP trace

These requests exercised the local ASGI app with synthetic service results and development authentication.
They establish route authorization, response/error shape, and signature rejection. They establish neither
database transactions nor Stripe integration. All timestamps below are UTC on 2026-10-20.

| Time | Request | Result | Correlation ID |
| --- | --- | --- | --- |
| 17:59:33.897 | POST `/v1/billing/checkout` | 200, synthetic Checkout URL | `9f44d82d-5079-4659-80a7-99cb58290b63` |
| 17:59:33.900 | POST `/v1/billing/portal` | 200, synthetic Portal URL | `5d5e1cda-eff6-4d5c-886c-944f7dee5b9a` |
| 17:59:33.932 | POST `/v1/billing/checkout` | 503, simulated missing configuration | `c3a6bcb1-44be-46cf-bf60-6a4914a16b07` |
| 17:59:33.966 | Reviewer POST `/v1/billing/checkout` | 403 | `fe36b71e-4d8f-4600-833d-ee04bd7742fc` |
| 17:59:33.967 | Reviewer POST `/v1/billing/portal` | 403 | `0595488c-87db-42d0-850c-3e324d79d519` |
| 17:59:34.005 | Unsigned POST `/internal/stripe/webhook` | 400 | `421695f5-baa1-4210-881c-651be8bdb60e` |

That isolated API run passed all five tests in 1.68 seconds.

## Outstanding acceptance evidence

Update: subsequent [Postgres setup and smoke checks](M4-payments-postgres-setup.md) established local connectivity
and existing firms isolation. The payment database test still stops on missing payment DDL. The paragraph below
records the earlier implementation run, before that setup.

The configured local Postgres endpoint was not reachable successfully during validation. Database tests were
not completed, and the backend-owned schema request is not implemented in this checkout. This prevents
claiming verified RLS, database concurrency, transactional webhook rollback, usage persistence, or crash recovery.

The backend owner must supply [the schema](../contracts/payments-schema-request.md) and validate approved refund
amount/reviewer evidence. Then run the database suite, `make check`, and the complete CI coverage gate. Follow
[the runtime guide](../onboarding/payments-runtime.md) for approved real test-account Checkout, delinquency,
metering, onboarding, transfer, recovery, hold, reversal, and export traces. Stripe CLI forwarding and the
clean-clone instructions have not been executed. Product UI integration and reviewer-seat admission remain
owner handoffs.

## Failure modes

An ambiguous transfer must remain unresolved rather than being exported or resent after key expiry, preventing
duplicate delivery. Webhook replay/order must preserve one transactional outcome and current provider state.
Holds must block pre-claim dispatch while in-flight successes remain recorded and alerted.
