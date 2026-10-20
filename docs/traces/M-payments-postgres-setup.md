# Local Postgres setup and validation — 2026-10-20

This supplements the earlier [offline validation](M-payments-local-validation.md). The user explicitly approved
Docker Desktop installation, the pinned Postgres image download, and local testing. No Stripe account calls,
new payment migration, deployment, or restart of Windows occurred.

## Installed and running

- Docker Desktop 4.96.0 installed through winget; Docker engine 29.9.1 verified running on WSL2.
- Microsoft WSL 2.7.13.0 installed after Windows administrator elevation.
- Repository image `pgvector/pgvector:0.8.6-pg16` downloaded and Compose service `db` started healthy.
- Container `recallops-db-1` publishes only `127.0.0.1:55432` to container port 5432.
- Existing bootstrap SQL created `recallops` and `recallops_test`, the three application roles, and pgvector 0.8.6.
- The five committed Alembic migrations applied successfully to both databases; head is `0005_m5_campaigns`.
- A gitignored `.env` configures local database URLs using `127.0.0.1` and fake LLM mode. The initial migration
  connection using `localhost` stalled; direct IPv4 loopback connected successfully, so subsequent checks use it.
- Neo4j was not installed or started. No Docker volumes were removed; Postgres remains running.

## Tests and probes

The focused payment unit/API/development-console suite passed **38 tests in 2.44 seconds**. These use mocked
Stripe boundaries and establish no provider-side outcome.

A direct psycopg smoke check against `recallops_test` passed:

- API role has `rolsuper=false`, `rolbypassrls=false`, and owns zero public tables.
- With no tenant context, a firms query returns zero rows.
- Firm A's context sees only A; an update targeting B changes zero rows. DELETE is denied by the API role's
  table privileges, which are stricter than a row-filtered delete. Firm B remains unchanged.
- After rollback, transaction-local tenant context clears and firms are again invisible.
- Synthetic smoke firms were deleted after the check. No consumer data was used.

The scratch smoke script first encountered the existing role restrictions on DELETE and migration metadata;
it was corrected to accept denied DELETE and read the Alembic revision as its owner. No application permissions
were broadened. An interrupted probe's synthetic rows were cleaned up by the final successful run.

Payment database test command (from `apps/api`):

```powershell
.venv/Scripts/python.exe -m pytest tests/db/test_billing_usage.py -q --maxfail=1 --tb=short --basetemp ../../.tools/pytest-postgres-billing
```

Observed result: **one fixture setup error**, before the test body:

```text
psycopg.errors.UndefinedTable: relation "billing_invoices" does not exist
DELETE FROM billing_invoices
```

Catalog checks confirm all five proposed payment tables are absent: `billing_accounts`, `billing_invoices`,
`claim_usage`, `run_usage_costs`, `usage_reports`. Payment recovery fields and resolver still require the
[backend-owned schema implementation](../contracts/payments-schema-request.md). No temporary tables,
`create_all`, permission bypass, or migration changes were used to manufacture a passing result.

## Next acceptance boundary

Postgres connectivity and the existing roles/schema are now established locally. The broader payment database
suite, new-table RLS/grants, concurrent runners, usage durability, transactional webhook state, and recovery
remain unverified until the owner migration is supplied. The 85% coverage gate and real Stripe test-mode flows
remain outstanding. These smoke results cover the firms boundary, not every tenant table or payment operation.

## Failure modes

Deleting the Docker volume would discard the local databases; normal stop/start should preserve it. Tests must
continue to target `recallops_test`. Missing payment DDL blocks fixture setup; weakening RLS or fabricating
partial tables would hide the migration gap and provide misleading payment-safety evidence.
