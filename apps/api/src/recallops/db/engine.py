"""Engines per database role (CLAUDE.md invariant 2): app for the API, internal for jobs."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from recallops.settings import get_settings

CONNECT_TIMEOUT_S = 5


def sqlalchemy_url(libpq_url: str) -> str:
    """Settings hold libpq URLs; SQLAlchemy needs the psycopg (v3) driver name."""
    return make_url(libpq_url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def build_engine(libpq_url: str) -> Engine:
    return create_engine(
        sqlalchemy_url(libpq_url),
        pool_pre_ping=True,
        hide_parameters=True,  # a database error never logs row values (claimant names, addresses)
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "application_name": "recallops"},
    )


@lru_cache(maxsize=4)
def app_engine(url: str | None = None) -> Engine:
    return build_engine(url or get_settings().database_url)


@lru_cache(maxsize=4)
def internal_engine(url: str | None = None) -> Engine:
    return build_engine(url or get_settings().database_url_internal)
