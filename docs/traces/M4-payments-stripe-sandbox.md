# Payments migration and Stripe validation: 2026-10-20

This trace supersedes the missing-schema/network limitations in the earlier
[offline](M4-payments-local-validation.md) and [Postgres setup](M4-payments-postgres-setup.md) traces.
The later bounded sandbox acceptance below supersedes the initial read-only limitations.
All provider objects were in Stripe **test mode**, and all claimant/usage data was synthetic. Timestamps are UTC.

## Authorization and schema

The owner explicitly authorized writing/applying the migration and Stripe test-account checks, then authorized
the Accounts v2 revision after the provider rejected legacy Express creation. The owner completed the platform
profile and hosted synthetic onboarding. Later, automatic approval review rejected the proposed combined
funding/transfer/reversal command. The owner selected **read-only Stripe checks only**; the rejected command did
not execute, and no funding, transfer, or reversal was performed by this validation session.

Migration `0006_payments` was applied to both `recallops` and `recallops_test`. At 18:45:32.133861, a catalog query
confirmed revision `0006_payments` and all five payment tables. The migration adds payout recovery facts,
immutable request/money protections, unique provider identities, forced tenant RLS, restricted grants, and the
narrow Connect resolver. A separate empty `recallops_migration_check` database passed upgrade → downgrade to
0005 → upgrade. The development database holding runtime identities was not downgraded.

The focused local regression selection passed **60 tests in 4.11 seconds**, including real Postgres-backed
usage deduplication/recovery, entitlement changes, concurrent quantity increments, caller-owned ledger event
transactions, tenant isolation, immutable request/money boundaries, Accounts v2 onboarding reuse, thin account
notification replay/readiness, and payment API contracts. Stripe is mocked in this local regression selection.
Ruff and strict mypy checks passed; the final broader coverage result is recorded below.

## Real Stripe observations

| UTC time | Action and observed result |
| --- | --- |
| 18:30:20.569 | Authenticated test balance retrieval passed. No account identifier or secret was printed. |
| 18:30:22.125 | Bootstrap created/verified the product, meter, program/claim prices, and restricted Portal configuration. IDs were stored in gitignored `.env`. |
| 18:30:22.690 | A second bootstrap returned the same resource identities. |
| 18:32:21.622 | Application Checkout created customer `cus_VTfAjzUpQMY8D8` and one session; a repeat call returned the same session. Local access remained free. |
| 18:32:21.899 | Application Customer Portal session creation passed. Its bearer URL was not committed. |
| 18:32:25.744 | A synthetic paid subscription `sub_1UShpjEV89QA26e2y8axCxaR` was active and reconciled to local growth access. |
| 18:33:01.256 | Actual provider event `evt_1UShplEV89QA26e2eyIsGN1y`, fetched through the API and signed by the local harness, processed once then returned replayed. This was not Stripe-forwarded delivery. |
| 18:33:03.513 | Closed-day usage submission for 2026-10-19 and its local repeat both returned reported; the saved snapshot contained 1,001 eligible claims. |
| 18:33:03.859 | Initial invoice preview still showed zero metered claims, demonstrating asynchronous aggregation. |
| 18:33:04.147 | Legacy account creation rejected by this platform; no connected account was created by that request. |
| 18:34:36.528 | Accounts v2 application-loss recipient creation rejected until platform profile resolution. |
| 18:34:47.941 | A compatibility probe using Stripe-collected losses was rejected: recipient configuration requires application fee/loss responsibility. No alternate funds flow was installed. |
| 18:36:11.846 | After the owner changed the platform profile, the provider required claimant contact email. Synthetic contact data was supplied locally. |
| 18:36:12.515 | Stripe meter summary showed 1,001.0; invoice preview showed $199 program charge, 1,000 zero-cost units, and one 25-cent unit. |
| 18:36:39.304 | Application Accounts v2 creation and hosted onboarding link succeeded for `acct_1UShtnEV89th11FT`. The owner subsequently completed the hosted test flow. |
| 18:41:35.328 | Read-only retrieval confirmed recipient stripe_transfers and payouts both active; available simulated platform balance was 280 cents. No funds were added or moved. |

The provider subscription was created and paid through Stripe's test API using `pm_card_visa`, then reconciled
through application code. The hosted Checkout UI was **not** completed; the earlier open Checkout was expired
before the separate subscription test. The subscription was backdated one day to exercise closed-day metering.
The 1,001 synthetic adjudication/usage identities were seeded as persisted source records; this did not run
1,001 actual model adjudications. The subscription/test account remain available for inspection, because the
owner's subsequent read-only instruction also precludes provider cleanup changes.

Actual read-only provider tests passed **2 tests in 1.46 seconds**:

```powershell
# From apps/api; credentials/configuration are read from the ignored .env, never printed.
$env:RUN_STRIPE_TESTS='1'
$env:STRIPE_TEST_ACCOUNT_ID='acct_1UShtnEV89th11FT'
.venv/Scripts/python.exe -m pytest tests/manual/test_stripe_readonly.py -q
```

These validate test-mode balance access, actual graduated price configuration, Portal restrictions, and the
completed Accounts v2 recipient's capability readiness. They perform no provider writes. Default tests skip
them and force fake Stripe keys.

## Accounts v2 contract change

New onboarding creates a recipient configuration with an Express dashboard and application-collected fees/losses
on stable API version `2026-09-30.endive`. No preview version or legacy creation fallback is installed. Readiness
uses included recipient capability state. Thin account notifications use their related account ID and the
separate `STRIPE_CONNECT_WEBHOOK_SECRET`; Billing/transfer snapshots retain their existing secret/version.
Local tests caught and fixed use of the SDK's snapshot parser on thin notifications; signature verification
now uses the SDK's raw-body signature helper for both formats before tenant resolution.

## Broader local validation

The first migrated suite measured **84.6571%**, with 406 passed, one skip and two failures. The failures were
Windows CRLF conversion of frozen evidence and a test writing a UTF-8 fixture with the system's cp1252 default.
All 403 frozen claim/label files were verified against the committed block hashes and restored to their original
LF bytes; `.gitattributes` preserves those bytes. Evaluation content and manifests were not changed. The test
now explicitly writes UTF-8. Both regressions passed after those corrections.

The final broad run excludes unavailable Neo4j tests, two additional API tests that require Neo4j despite not
having its marker, and two existing Windows storage limitations (symlink privilege and POSIX `os.geteuid`).
This selection must not be described as full Linux CI. The cached CPSC feed is also absent, producing one skip.

Final measured run: **410 passed, 1 skipped, 24 deselected in 116.32 seconds; 85.37% coverage**, with
`--cov-fail-under=85` enforced and passing. The 24 deselections comprise the graph selection, opt-in provider
tests, and four explicit exclusions. Provider tests were run separately as described above; they are not
included in the coverage measurement. Final Ruff, formatting (171 files) and strict mypy (163 files) passed.
A final malformed-JSON webhook guard also passed its focused regression after the broad run.

```powershell
# From apps/api; default fixtures force fake provider keys.
.venv/Scripts/python.exe -m pytest -q -m 'not graph and not stripe_test' --deselect tests/unit/test_storage.py::test_local_storage_refuses_a_key_that_resolves_outside_the_root --deselect tests/unit/test_storage.py::test_local_storage_wraps_os_errors --deselect tests/api/test_m2_flows.py::test_flagged_claim_waits_and_the_review_resumes_the_same_run --deselect tests/api/test_m2_flows.py::test_the_graph_is_reported_and_its_absence_is_a_fixed_503 --basetemp ../../.tools/pytest-final-migrated-coverage --cov=recallops --cov-report=json:../../.tools/final-migrated-coverage.json --cov-report=term --cov-fail-under=85 --tb=short --maxfail=3
```

## Initial acceptance gates (superseded by the sandbox run below)

No test transfer or reversal was executed, per the owner's read-only selection. Therefore actual provider
transfer creation, crash recovery, transfer-versus-hold timing, partial reversal delivery, and transfer/export
exclusivity remain unverified against Stripe, although local database/fake-provider regressions cover the
relevant invariants. Platform balance is insufficient for the 4,999-cent synthetic refund without funding.

Stripe-forwarded snapshot/thin webhooks, hosted Checkout completion, actual delinquency/recovery, real quantity
sync/proration, clean-clone setup, the full graph suite and deployment are still outstanding. Upstream approved
amount calculation/reviewer evidence and historical usage backfill still require backend review. No real money
or consumer identity was used, and no Git commit/push/deployment was made.

## Failure modes

Unknown transfer outcomes must stay isolated from export and unsafe replay after key expiry. Thin and snapshot
events use different secrets; a destination misconfiguration must fail closed rather than update a tenant.
Holds before dispatch must block it; in-flight success must remain recorded and alerted.


## Bounded sandbox acceptance — 19:07 to 19:32 UTC

The owner explicitly approved official test-tool downloads and replaced read-only mode with isolated sandbox
subscriptions, up to three recipients/$49.99 transfers, one $10 reversal and at most $1,000 simulated funding,
conditional on no real money. All API objects checked test mode. No paid hosting or live-money operation ran.
Run `83da2bbf-e015-4a10-a130-84fa70171583` used only `recallops_payments_acceptance`; old development/Stripe
fixtures were not repurposed for its payouts. The owner completed Checkout and all three hosted recipient forms.
Stripe CLI 1.54.1 forwarded Billing/transfer snapshots and Accounts v2 thin notifications with separate secrets.
Secrets, full event bodies and hosted bearer URLs remain ignored; identities below are synthetic test resources.

| UTC | Observed result |
| --- | --- |
| 19:07:16 | Dedicated acceptance fixture initialized; main firm at the seeded free 100-claim limit. |
| 19:07:30–31 | Snapshot and thin listeners both ready; loopback API and normal adjudication worker started. |
| 19:14:16 | Main claim remained submitted; another firm's claim reached needs_info, proving the blocked firm did not starve it. |
| 19:14:49.733 | Genuine Stripe invoice.paid event `evt_1USiUnEV89QA26e2G15e7m0V` reached the receiver, reconciled subscription/customer and committed Growth/invoice/audit state. CLI observed HTTP 200 at 19:14:50.325. |
| 19:14:49–50 | Concurrent Checkout/subscription-created events received HTTP 503 because the firm advisory lock was occupied. They did not commit partial event effects. Genuine invoice.paid, rather than the browser return, granted access. CLI forwarding does not prove Stripe destination retry scheduling. |
| 19:14:51.180–51.265 | The previously blocked claim's worker run started and completed as needs_info. Start was under two seconds after webhook receipt and under one second after its HTTP 200, within the configured two-second poll. |
| 19:17:31 | Persisted genuine invoice event replayed with a locally regenerated valid signature: replayed; audit count unchanged. This replay is distinct from the original CLI-forwarded delivery. |
| 19:19:13–14 | Application program open/close operations converged Stripe quantity 1 → 2 → 1 on `sub_1USiUmEV89QA26e2oj9KG0E0`. |
| 19:21:24 | Isolated test-clock invoice `in_1USiayEV89QA26e2Q9okMdnn` recorded attempt_count 1; effective access remained Growth. |
| 19:22:21 | One simulated funding PaymentIntent `pi_3USic5EV89QA26e20arJc9nf` succeeded for 100,000 cents. A later provider listing confirmed exactly one funding intent tagged to this run. |
| 19:22:34 | First payout's confirmed insufficient-balance rejection reconciled to pending_export. The original UUID/key was preserved; no alternate-key repayment was attempted. |
| 19:22:45–49 | Test-clock scheduled retry increased provider attempt_count to 2 and applied Free. Paying the same invoice with a working test method restored Growth. Manual retries after the first do not increase Stripe's schedule counter; the harness now advances the clock for the scheduled retry. |
| 19:23:04 | Second payout child terminated after committed claim_for_transfer and before any transfer request. |
| 19:23:25 | Restart reconciled/sent the same immutable request once: `tr_1USid7EV89QA26e2I19dR8JP`, 4,999 USD cents to its stored recipient. |
| 19:23:39 | Genuine transfer.reversed notification recorded 1,000 reversed cents and one alert. Payout stayed transferred; no export/repayment eligibility reopened. |
| 19:24:08–21 | Clock subscription scheduled cancellation retained Growth; advancing past effective cancellation produced canceled/Free. |
| 19:24:23 | Fallback CSV download contained exactly the first payout for 4,999 cents; sha256 `4afbdddce7c1352a3967b14b94d3ce06c10531c0875973677c1335e5ba6c499b`. Console displayed the batch and amount. |
| 19:26:45 | Third payout child terminated after Stripe created `tr_1USigLEV89QA26e21GVpOXHe`, before local response recording. |
| 19:26:55 | Restart converged to transferred. Final provider listing found exactly one matching transfer; this proves recovery without assuming whether event delivery or listing won the race. |
| 19:26:59 / 19:30 | Persisted snapshot/thin/transfer/reversal events replayed with new local signatures; all returned replayed and audit totals stayed unchanged. |
| 19:31:45 | Actual invoice preview contained four program-proration lines; rapid open/close produced net zero cents. |

Final delivery facts: three payout_created events, two payout_transferred events, one payout_exported, one
payout_transfer_reversed. The exported payout has no transfer ID; both transferred payouts have no export batch.
There were two actual $49.99 sandbox transfers, one $10 reversal, three recipient accounts, one simulated $1,000
funding operation. The first of the three fixtures exercised confirmed-failure export instead of normal transfer;
normal delivery was observed on the restarted second fixture. No fourth Connect fixture/account was created. A separate local-only free-firm check payout was added later, with zero Stripe calls or provider funds movement.
A Connect transfer is the tested delivery boundary; no bank arrival is claimed.

### Defects exposed and corrected

- Stripe's configured API rejects the obsolete PaymentIntent payment_method_types argument. Funding now uses
  managed payment methods plus the configured return URL. The original rejected funding request produced no
  funding success; the corrected request used a distinct operation identity. Exactly one actual funding intent
  for the run was confirmed afterward. Future runs use only the corrected request.
- The unmerged migration's immutability trigger also froze payout.method, blocking a confirmed failed transfer
  from selecting check export. It now permits only failed → pending_export/check_export without a transfer ID;
  original request, amount, currency, destination, tenant and claim remain immutable. A real-Postgres regression
  protects that transition. The corrected function was applied to the isolated acceptance/Linux databases and the previously migrated local development/test databases;
  an empty clean-checkout database subsequently migrated through the corrected 0006 from scratch. Older local
  databases that already recorded revision 0006 need the reviewed definition or rebuilding before testing it.
- An early harness loop slept after every blocked-firm result and starved the next firm. The production worker
  already avoids this; acceptance now runs its actual run_forever loop. Production scheduling was unchanged.

### Validation and reproducibility

The fresh local clone used the current uncommitted deliverable overlay and `.env.example`, with no real keys,
old `.venv`, scratch artifacts or acceptance database records copied. Thus it is a fresh checkout of the proposed
diff, not a claim that an uncommitted branch can already be cloned from GitHub.

- Frozen Linux dependencies: 81 packages checked. Empty `recallops_payments_clean` bootstrapped and all migrations
  executed; Alembic reported `0006_payments (head)`.
- `make check` from that checkout, with caches in /tmp, actual pinned Neo4j and Postgres, and
  PYTEST_ADDOPTS adding `--cov-fail-under=85`: **435 passed, 7 skipped in 111.70 s; 89.29% line coverage**.
  Ruff passed (165 source/test/assets files formatted); strict mypy passed (164 source files). No tests deselected.
  Six skips are explicitly opt-in Stripe tests, run separately below; one requires the absent gitignored CPSC feed.
- Sandbox acceptance assertions: **3 passed in 3.02 s** (final rerun after Portal and free-plan export). Genuine event identities, replay audit effects, worker
  resumption and one transfer/export delivery per fixture were checked against the isolated DB/provider.
- Read-only provider checks: **3 passed in 1.94 s**, including a durable assertion that the earlier persisted
  1,001 eligible-claim fixture's invoice preview still totals exactly 25 cents of claim overage. One upstream
  httpx raw-body deprecation warning was emitted; checks passed. No additional usage was submitted by this check.
- Focused latest Postgres usage regressions: **7 passed in 2.24 s**, including UTC month rollover, no retroactive
  free usage billing, permanent report receipts and quantity convergence after a failed sync plus intervening changes.

The container setup executed the frozen dependency sync, bootstrap/migrations and `make check` steps in isolation.
A real uvicorn child subsequently started from the fresh checkout with example-only credentials: /healthz and
/dev/console both returned HTTP 200. Only that test child was terminated afterward. Final current-tree Linux
Ruff/formatting (165 files) and strict mypy (164 files) passed after the last harness additions.
The exact all-in-one README `make setup` was not run inside the container because it needs the host Docker CLI;
host Compose services were started separately. Product UI, cloud deployment and real-money delivery were not tested.

### Remaining owner gates

At 19:34:28, the owner had completed the hosted Customer Portal changes. The Portal visibly showed a new
synthetic Mastercard as default and service ending November 1. Provider retrieval confirmed active status and
cancel_at exactly equal to both subscription item period ends; cancel_at_period_end was false, illustrating
why the effective provider date, not that flag alone, is the observable contract. Local Growth remained active.
The payment-method listing did not return the card, so its update is established by the visible provider Portal
and owner action, not that API list. The owner confirmed the 24-hour finalization grace setting in the Dashboard;
it was not independently inspected by API. Test-clock cancellation/recovery are independently verified above. Fresh local usage success does not establish historical aggregate backfill.
At 19:35:48 the other, free firm selected check export through its claimant endpoint and the admin export
endpoint produced one 4,999-cent CSV row. Payout `99256033-1861-4ec3-b877-175497793961` has no Connect account or
transfer ID; batch `e3d87ffa-4942-49ed-8dc1-c89d1a39c2c7` was visible in the free-plan console and its Download CSV
control was exercised. API content/identity/amount assertions passed. This additional fixture is entirely local
and did not consume another Stripe recipient or transfer. In-flight/pre-dispatch holds and competing money
operations remain real-Postgres/controlled-provider regressions, not manufactured live provider races.

Receiving UI/backend/login/operations owners must accept the checklist in payments-runtime.md. Correct upstream
refund amounts/reviewer evidence, product UI wiring, reviewer-seat admission, job placement/monitoring and manual
reconciliation ownership are external receiving-owner gates. No cleanup deletion, commit, push, merge or deployment
was performed. Sandbox resources and ignored manifests remain available for review and safe later cleanup.
