"""Database dependencies for routes. Tenant routes get a session already bound to the caller's firm (RLS active)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from recallops.auth.deps import FirmDep
from recallops.db.engine import app_engine
from recallops.db.session import tenant_session


def tenant_db(ctx: FirmDep) -> Iterator[Session]:
    with tenant_session(ctx) as session:
        yield session


def public_db() -> Iterator[Session]:
    """Public reference tables (recalls) through the app role, with no firm context."""
    with Session(app_engine()) as session, session.begin():
        yield session


TenantDb = Annotated[Session, Depends(tenant_db)]
PublicDb = Annotated[Session, Depends(public_db)]
