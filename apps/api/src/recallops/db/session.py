"""Transaction-scoped sessions. A tenant session sets app.firm_id with set_config(..., true), the
bind-parameter form of SET LOCAL, so the setting ends with the transaction and never leaks through
the connection pool."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from recallops.db.engine import app_engine, internal_engine
from recallops.tenancy import FirmContext

SET_FIRM_SQL = text("SELECT set_config('app.firm_id', :firm_id, true)")


def set_firm(session: Session, firm_id: UUID) -> None:
    if not isinstance(firm_id, UUID):
        raise TypeError("firm_id must be a UUID")
    session.execute(SET_FIRM_SQL, {"firm_id": str(firm_id)})


@contextmanager
def tenant_session(ctx: FirmContext, engine: Engine | None = None) -> Iterator[Session]:
    """API path: recallops_app role, RLS enforced, firm from the verified context."""
    with Session(engine or app_engine(), expire_on_commit=False) as session, session.begin():
        set_firm(session, ctx.firm_id)
        yield session


@contextmanager
def internal_session(engine: Engine | None = None) -> Iterator[Session]:
    """Job path: recallops_internal role (BYPASSRLS). Repositories still filter by firm."""
    with Session(engine or internal_engine(), expire_on_commit=False) as session, session.begin():
        yield session
