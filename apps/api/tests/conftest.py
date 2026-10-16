"""Shared fixtures. Database tests run against the recallops_test database (make setup creates it).

Tests never call a model: LLM_MODE is forced to fake and the API key is removed from the environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

os.environ["LLM_MODE"] = "fake"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("APP_ENV", "test")

from recallops.db.engine import build_engine
from recallops.settings import get_settings

TEST_DB = "recallops_test"

# Delete order respects foreign keys. Runs as recallops_internal (BYPASSRLS).
CLEAN_ORDER = (
    "agent_steps",
    "agent_runs",
    "payouts",
    "claim_photos",
    "fraud_clusters",
    "mpr_reports",
    "documents",
    "claims",
    "recall_programs",
    "audit_log",
    "usage_meters",
    "firm_members",
    "firms",
    "feed_runs",
    "recalls",
    "stripe_events",
)


def test_url(libpq_url: str) -> str:
    return make_url(libpq_url).set(database=TEST_DB).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def app_engine_test() -> Iterator[Engine]:
    engine = build_engine(test_url(get_settings().database_url))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def internal_engine_test() -> Iterator[Engine]:
    engine = build_engine(test_url(get_settings().database_url_internal))
    yield engine
    engine.dispose()


@pytest.fixture()
def clean_db(internal_engine_test: Engine) -> Iterator[Engine]:
    """Empty every table before the test; the engine returned is the internal (BYPASSRLS) one."""
    with internal_engine_test.begin() as conn:
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    yield internal_engine_test
