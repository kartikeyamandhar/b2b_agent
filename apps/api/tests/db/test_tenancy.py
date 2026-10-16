"""Cross-tenant isolation at the database layer (01-backend.md section 12, first acceptance test).

The app role must not be able to see, change, or create another firm's rows, through either the
TenantScoped repositories or raw SQL, and must see nothing at all without a firm context.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from recallops.db.repos import ClaimsRepo, ProgramsRepo, RunsRepo
from recallops.db.session import tenant_session
from recallops.tenancy import FirmContext

pytestmark = pytest.mark.db

TENANT_TABLES = (
    "firm_members",
    "recall_programs",
    "documents",
    "claims",
    "claim_photos",
    "agent_runs",
    "agent_steps",
    "fraud_clusters",
    "payouts",
    "mpr_reports",
    "audit_log",
    "usage_meters",
)


@dataclass(frozen=True)
class FirmRows:
    firm_id: uuid.UUID
    program_id: uuid.UUID
    claim_id: uuid.UUID
    run_id: uuid.UUID


def _seed_firm(conn: object, name: str) -> FirmRows:
    assert hasattr(conn, "execute")
    firm, program, claim, run = (uuid.uuid4() for _ in range(4))
    ids = {"f": firm, "p": program, "c": claim, "r": run, "name": name, "tok": uuid.uuid4().hex}
    statements = [
        "INSERT INTO firms (id, name) VALUES (:f, :name)",
        "INSERT INTO firm_members (firm_id, clerk_user_id, role) VALUES (:f, 'user_' || :name, 'admin')",
        "INSERT INTO recall_programs (id, firm_id, value_threshold_cents) VALUES (:p, :f, 10000)",
        "INSERT INTO documents (firm_id, program_id, kind, title, text) VALUES (:f, :p, 'faq', 't', 'x')",
        "INSERT INTO claims (id, firm_id, program_id, token_hash, status) VALUES (:c, :f, :p, :tok, 'submitted')",
        "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash) "
        "VALUES (:f, :c, 'label', 'p', 's', '0000000000000000')",
        "INSERT INTO agent_runs (id, firm_id, claim_id, program_id, graph) VALUES (:r, :f, :c, :p, 'rules_only')",
        "INSERT INTO agent_steps (run_id, firm_id, idx, node) VALUES (:r, :f, 0, 'check_model')",
        "INSERT INTO fraud_clusters (firm_id, program_id, kind, member_claim_ids) VALUES (:f, :p, 'same_photo', ARRAY[CAST(:c AS uuid)])",
        "INSERT INTO payouts (firm_id, claim_id, amount_cents, method) VALUES (:f, :c, 1000, 'check_export')",
        "INSERT INTO mpr_reports (firm_id, program_id, period_start, period_end, payload, generated_by) "
        "VALUES (:f, :p, '2026-10-01', '2026-10-31', '{}', 'system')",
        "INSERT INTO audit_log (firm_id, actor, action, target_type, target_id) VALUES (:f, 'system', 'seed', 'claim', 'x')",
        "INSERT INTO usage_meters (firm_id, period_start) VALUES (:f, '2026-10-01')",
    ]
    for sql in statements:
        conn.execute(text(sql), ids)
    return FirmRows(firm, program, claim, run)


@pytest.fixture()
def two_firms(clean_db: Engine) -> Iterator[tuple[FirmRows, FirmRows]]:
    with clean_db.begin() as conn:
        a = _seed_firm(conn, "firm_a")
        b = _seed_firm(conn, "firm_b")
    yield a, b


def ctx(rows: FirmRows) -> FirmContext:
    return FirmContext(firm_id=rows.firm_id, user_id="tester", role="admin")


def test_app_role_is_neither_superuser_nor_owner(app_engine_test: Engine) -> None:
    with app_engine_test.connect() as conn:
        role = conn.execute(
            text("SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owned = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user")
        ).scalar_one()
    assert role.rolname == "recallops_app"
    assert role.rolsuper is False
    assert role.rolbypassrls is False
    assert owned == 0


def test_every_firm_id_table_has_rls_enabled_and_forced(app_engine_test: Engine) -> None:
    with app_engine_test.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN information_schema.columns col
                  ON col.table_name = c.relname AND col.table_schema = 'public' AND col.column_name = 'firm_id'
                WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace
                """
            )
        ).all()
    tables = {r.relname for r in rows}
    assert tables == set(TENANT_TABLES)
    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)]
    assert unprotected == []


def test_raw_sql_under_firm_a_sees_no_firm_b_rows(
    app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]
) -> None:
    a, b = two_firms
    with tenant_session(ctx(a), engine=app_engine_test) as session:
        for table in TENANT_TABLES:
            total = session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            foreign = session.execute(
                text(f"SELECT count(*) FROM {table} WHERE firm_id = :b"), {"b": b.firm_id}
            ).scalar_one()
            assert foreign == 0, table
            assert total >= 1, table
        assert session.execute(text("SELECT count(*) FROM firms")).scalar_one() == 1


def test_updates_and_deletes_of_firm_b_affect_zero_rows(
    app_engine_test: Engine, internal_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]
) -> None:
    a, b = two_firms
    with tenant_session(ctx(a), engine=app_engine_test) as session:
        for table in TENANT_TABLES:
            if table == "audit_log":
                continue
            updated = session.execute(
                text(f"UPDATE {table} SET firm_id = firm_id WHERE firm_id = :b"), {"b": b.firm_id}
            )
            deleted = session.execute(text(f"DELETE FROM {table} WHERE firm_id = :b"), {"b": b.firm_id})
            assert updated.rowcount == 0, table  # type: ignore[attr-defined]
            assert deleted.rowcount == 0, table  # type: ignore[attr-defined]
    with internal_engine_test.connect() as conn:
        for table in TENANT_TABLES:
            canary = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE firm_id = :b"), {"b": b.firm_id}
            ).scalar_one()
            assert canary >= 1, f"firm B rows in {table} must survive"


def test_app_role_cannot_delete_audit_rows(app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]) -> None:
    a, _ = two_firms
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(ctx(a), engine=app_engine_test) as session,
    ):
        session.execute(text("DELETE FROM audit_log"))


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO recall_programs (firm_id, value_threshold_cents) VALUES (:b, 1)",
        "INSERT INTO audit_log (firm_id, actor, action, target_type, target_id) VALUES (:b, 'x', 'y', 'z', 'w')",
        "INSERT INTO firms (id, name) VALUES (:b2, 'sneaky')",
    ],
)
def test_inserts_for_another_firm_are_rejected(
    app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows], sql: str
) -> None:
    a, b = two_firms
    with (
        pytest.raises(DBAPIError, match="row-level security"),
        tenant_session(ctx(a), engine=app_engine_test) as session,
    ):
        session.execute(text(sql), {"b": b.firm_id, "b2": uuid.uuid4()})


def test_no_firm_context_sees_nothing(app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]) -> None:
    with Session(app_engine_test) as session, session.begin():
        for table in ("firms", *TENANT_TABLES):
            assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0, table


def test_firm_context_does_not_leak_to_the_next_transaction(
    app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]
) -> None:
    a, _ = two_firms
    with tenant_session(ctx(a), engine=app_engine_test) as session:
        assert session.execute(text("SELECT recallops_current_firm()")).scalar_one() == a.firm_id
    with app_engine_test.connect() as conn:  # the same pooled connection is reused
        assert conn.execute(text("SELECT recallops_current_firm()")).scalar_one() is None
        assert conn.execute(text("SELECT count(*) FROM claims")).scalar_one() == 0


def test_repositories_return_nothing_for_firm_b_ids(
    app_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]
) -> None:
    a, b = two_firms
    with tenant_session(ctx(a), engine=app_engine_test) as session:
        assert ClaimsRepo(session, ctx(a)).get(b.claim_id) is None
        assert ClaimsRepo(session, ctx(a)).get(a.claim_id) is not None
        assert ProgramsRepo(session, ctx(a)).get(b.program_id) is None
        assert ClaimsRepo(session, ctx(a)).list_for_program(b.program_id) == []
        assert RunsRepo(session, ctx(a)).latest_for_claim(b.claim_id) is None
        assert ClaimsRepo(session, ctx(a)).photos(b.claim_id) == []


def test_repository_scoping_holds_even_without_rls(
    internal_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]
) -> None:
    """Layer 1 alone: the internal role bypasses RLS, so only the repository filter protects here."""
    a, b = two_firms
    with Session(internal_engine_test) as session, session.begin():
        assert ClaimsRepo(session, ctx(a)).get(b.claim_id) is None
        assert ProgramsRepo(session, ctx(a)).get(b.program_id) is None


def test_agent_step_firm_must_match_its_run(internal_engine_test: Engine, two_firms: tuple[FirmRows, FirmRows]) -> None:
    a, b = two_firms
    with (
        pytest.raises(DBAPIError, match=r"must equal agent_runs\.firm_id"),
        internal_engine_test.begin() as conn,
    ):
        conn.execute(
            text("INSERT INTO agent_steps (run_id, firm_id, idx, node) VALUES (:r, :f, 1, 'x')"),
            {"r": b.run_id, "f": a.firm_id},
        )


def test_archive_extraction_runs_may_have_no_firm_but_adjudication_runs_may_not(
    internal_engine_test: Engine, clean_db: Engine
) -> None:
    with internal_engine_test.begin() as conn:
        conn.execute(text("INSERT INTO agent_runs (graph) VALUES ('extract_rules')"))
    with pytest.raises(DBAPIError, match="check constraint"), internal_engine_test.begin() as conn:
        conn.execute(text("INSERT INTO agent_runs (graph) VALUES ('adjudicate')"))
