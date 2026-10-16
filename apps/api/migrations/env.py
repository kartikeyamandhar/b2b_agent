"""Alembic environment. Runs as recallops_owner (DATABASE_URL_OWNER) unless -x dburl=... is given."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from recallops.db.engine import sqlalchemy_url
from recallops.settings import get_settings


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("dburl")
    return sqlalchemy_url(override or get_settings().database_url_owner)


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
