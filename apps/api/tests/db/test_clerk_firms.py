"""First sign-in of a Clerk organization creates its firm, as recallops_app (migration 0007), idempotently; a second
organization gets its own firm; the member row mirrors the token's role; and the app role still cannot read firms
outside its context."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from recallops.auth.clerk import ClerkAuthProvider, ClerkPrincipal
from recallops.settings import Settings
from recallops.tenancy import Role

pytestmark = pytest.mark.db


def provider(engine: Engine) -> ClerkAuthProvider:
    settings = Settings(auth_provider="clerk", clerk_issuer="https://test-instance.clerk.accounts.dev")
    return ClerkAuthProvider(settings, jwks_client=object(), engine=engine)


def principal(org: str, user: str, role: Role) -> ClerkPrincipal:
    return ClerkPrincipal(user_id=user, firm_id=None, role=role, org_id=org, org_slug=f"{org}-slug")


def test_first_sight_creates_one_firm_and_mirrors_roles(clean_db: Engine, app_engine_test: Engine) -> None:
    first, again = provider(app_engine_test), provider(app_engine_test)  # two processes, no shared cache
    a1 = first.firm_context(principal("org_a", "user_1", "admin"))
    a2 = again.firm_context(principal("org_a", "user_1", "admin"))
    a3 = again.firm_context(principal("org_a", "user_2", "reviewer"))
    b1 = first.firm_context(principal("org_b", "user_3", "admin"))
    assert a1.firm_id == a2.firm_id == a3.firm_id != b1.firm_id
    assert (a3.role, b1.role) == ("reviewer", "admin")

    # the role changes in Clerk: the mirror follows (a new cache key, so it is written again)
    first.firm_context(principal("org_a", "user_2", "admin"))

    with clean_db.connect() as conn:
        firms = conn.execute(text("SELECT clerk_org_id, name FROM firms ORDER BY clerk_org_id")).all()
        members = conn.execute(
            text(
                "SELECT f.clerk_org_id, m.clerk_user_id, m.role FROM firm_members m "
                "JOIN firms f ON f.id = m.firm_id ORDER BY 1, 2"
            )
        ).all()
    assert [tuple(r) for r in firms] == [("org_a", "org_a-slug"), ("org_b", "org_b-slug")]
    assert [tuple(r) for r in members] == [
        ("org_a", "user_1", "admin"),
        ("org_a", "user_2", "admin"),
        ("org_b", "user_3", "admin"),
    ]


def test_the_app_role_sees_no_firm_without_context_and_the_function_is_not_public(
    clean_db: Engine, app_engine_test: Engine
) -> None:
    provider(app_engine_test).firm_context(principal("org_a", "user_1", "admin"))
    with app_engine_test.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM firms")).scalar_one() == 0
        public = conn.execute(
            text("SELECT has_function_privilege('public', 'recallops_firm_for_clerk_org(text, text)', 'EXECUTE')")
        ).scalar_one()
    assert public is False
