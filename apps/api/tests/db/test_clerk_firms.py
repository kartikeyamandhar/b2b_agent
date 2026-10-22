"""One account = one company (plan v3, migration 0009): an account's first sign-in creates its company as
recallops_app, idempotently; another account gets its own; the operator never gets one; the app role still cannot read
firms outside its context, and only sign-in and jobs may change which account a company belongs to."""

from __future__ import annotations

import threading
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from recallops.auth.clerk import NEW_FIRM_NAME, ClerkAuthProvider, ClerkPrincipal
from recallops.auth.provider import OperatorHasNoFirm
from recallops.db.session import tenant_session
from recallops.models import Firm
from recallops.settings import Settings
from recallops.tenancy import FirmContext

pytestmark = pytest.mark.db


def provider(engine: Engine, **over: str) -> ClerkAuthProvider:
    settings = Settings(
        auth_provider="clerk",
        clerk_issuer="https://test-instance.clerk.accounts.dev",
        clerk_jwks_url="",
        platform_operator_user_ids=over.get("operators", ""),
        platform_operator_emails="",
    )
    return ClerkAuthProvider(settings, jwks_client=object(), engine=engine)


def principal(user: str) -> ClerkPrincipal:
    return ClerkPrincipal(user_id=user, firm_id=None, role="admin")


def test_first_sight_creates_one_company_per_account(clean_db: Engine, app_engine_test: Engine) -> None:
    first, again = provider(app_engine_test), provider(app_engine_test)  # two processes, no shared cache
    a1 = first.firm_context(principal("user_a1"))
    a2 = again.firm_context(principal("user_a1"))
    b1 = first.firm_context(principal("user_b1"))
    assert a1.firm_id == a2.firm_id != b1.firm_id
    assert (a1.role, b1.role) == ("admin", "admin")
    with clean_db.connect() as conn:
        firms = conn.execute(text("SELECT clerk_user_id, name, onboarded_at FROM firms ORDER BY 1")).all()
        members = conn.execute(text("SELECT clerk_user_id, role FROM firm_members ORDER BY 1")).all()
    assert [tuple(r) for r in firms] == [("user_a1", NEW_FIRM_NAME, None), ("user_b1", NEW_FIRM_NAME, None)]
    assert [tuple(r) for r in members] == [("user_a1", "admin"), ("user_b1", "admin")]


def test_the_operator_never_gets_a_company(clean_db: Engine, app_engine_test: Engine) -> None:
    with pytest.raises(OperatorHasNoFirm):
        provider(app_engine_test, operators="user_op").firm_context(principal("user_op"))
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM firms")).scalar_one() == 0


def test_the_app_role_sees_no_firm_without_context_and_the_functions_are_not_public(
    clean_db: Engine, app_engine_test: Engine
) -> None:
    provider(app_engine_test).firm_context(principal("user_a1"))
    with app_engine_test.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM firms")).scalar_one() == 0
        for signature in ("recallops_firm_for_clerk_user(text, text)", "recallops_firm_of_clerk_user(text)"):
            public = conn.execute(
                text("SELECT has_function_privilege('public', :f, 'EXECUTE')"), {"f": signature}
            ).scalar_one()
            assert public is False, signature
        old = conn.execute(text("SELECT to_regprocedure('recallops_firm_for_clerk_org(text, text)')")).scalar_one()
    assert old is None  # organizations are gone with their function


def test_a_company_cannot_move_itself_to_another_account(clean_db: Engine, app_engine_test: Engine) -> None:
    """The app role may update its own firm row, but never the account it belongs to: a firm that took another
    account's id would receive that account's first sign-in (plan v3 red team)."""
    ctx = provider(app_engine_test).firm_context(principal("user_a1"))
    company = FirmContext(ctx.firm_id, "user_a1", "admin")
    with pytest.raises(DBAPIError, match="set by sign-in only"), tenant_session(company, app_engine_test) as session:
        firm = session.get(Firm, ctx.firm_id)
        assert firm is not None
        firm.clerk_user_id = "user_victim"
    with tenant_session(FirmContext(ctx.firm_id, "user_a1", "admin"), app_engine_test) as session:
        firm = session.get(Firm, ctx.firm_id)
        assert firm is not None
        firm.name = "Acme Recalls"  # the name is the company's to change
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT name, clerk_user_id FROM firms")).one() == ("Acme Recalls", "user_a1")


def test_the_app_role_cannot_insert_a_company_bound_to_an_account(clean_db: Engine, app_engine_test: Engine) -> None:
    """The guard covered UPDATE only, so the app role could insert a firm pre-bound to another account and receive
    that account's first sign-in (plan v3 review)."""
    from uuid import uuid4

    planted = FirmContext(uuid4(), "user_attacker", "admin")
    with pytest.raises(DBAPIError, match="set by sign-in only"), tenant_session(planted, app_engine_test) as session:
        session.execute(
            text("INSERT INTO firms (id, name, clerk_user_id) VALUES (:f, 'Trap', 'user_victim')"),
            {"f": planted.firm_id},
        )
    victim = provider(app_engine_test).firm_context(principal("user_victim"))
    assert victim.firm_id != planted.firm_id


def test_concurrent_first_sign_ins_create_one_company(clean_db: Engine, app_engine_test: Engine) -> None:
    """Twelve processes see the account for the first time at once (no shared cache): one company, no errors
    (ON CONFLICT in the resolver function)."""
    n = 12
    barrier, lock = threading.Barrier(n), threading.Lock()
    results: list[object] = []

    def sign_in(_: int) -> None:
        mine = provider(app_engine_test)
        barrier.wait()
        try:
            out: object = mine.firm_context(principal("user_race")).firm_id
        except Exception as exc:  # noqa: BLE001 - every failure is collected and asserted below
            out = exc
        with lock:
            results.append(out)

    threads = [threading.Thread(target=sign_in, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert [r for r in results if not isinstance(r, UUID)] == []
    assert len(set(results)) == 1 and len(results) == n
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM firms WHERE clerk_user_id = 'user_race'")) == 1
        assert conn.execute(text("SELECT clerk_user_id FROM firm_members")).scalars().all() == ["user_race"]
