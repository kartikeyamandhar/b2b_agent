"""Shared fixtures. Database tests run against the recallops_test database (make setup creates it).

Tests never call a model: LLM_MODE is forced to fake and the API key is removed from the environment.
Graph tests always use a local Neo4j (NEO4J_TEST_URI, default bolt://localhost:57687) and a namespace starting with
"test" (NEO4J_TEST_NAMESPACE, default test), whatever .env says; anything else stops the run.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

os.environ["LLM_MODE"] = "fake"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.setdefault("APP_ENV", "test")
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
os.environ["STRIPE_PUBLISHABLE_KEY"] = "pk_test_fake"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake"
os.environ["STRIPE_CONNECT_WEBHOOK_SECRET"] = "whsec_fake"
os.environ["STRIPE_API_VERSION"] = "2026-09-30.endive"
# Company verification (migration 0008) is on by default everywhere and refused off in prod. The suite runs with it
# off so its fixtures can make firms and programs freely; tests/api/test_recall_access.py turns it on.
os.environ["ACCESS_APPROVAL"] = "off"
os.environ["STRIPE_PROGRAM_PRICE_ID"] = ""
os.environ["STRIPE_CLAIM_PRICE_ID"] = ""
os.environ["STRIPE_PORTAL_CONFIGURATION_ID"] = ""

os.environ["NEO4J_URI"] = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:57687")
os.environ["NEO4J_NAMESPACE"] = os.environ.get("NEO4J_TEST_NAMESPACE", "test")

from recallops.db.engine import build_engine
from recallops.settings import Settings, get_settings

if TYPE_CHECKING:
    from recallops.fraud.graph import Neo4jGraphStore

# Checked before anything reads settings: graph tests never touch a remote graph or a non-test namespace.
if (urlsplit(os.environ["NEO4J_URI"]).hostname or "") not in {"localhost", "127.0.0.1", "::1"}:
    pytest.exit(f"refusing to run tests against a non-local Neo4j: {os.environ['NEO4J_URI']}", returncode=2)
if not os.environ["NEO4J_NAMESPACE"].startswith("test"):
    pytest.exit(f"NEO4J_TEST_NAMESPACE must start with 'test', not {os.environ['NEO4J_NAMESPACE']!r}", returncode=2)
GRAPH_TEST_NAMESPACES = (os.environ["NEO4J_NAMESPACE"], os.environ["NEO4J_NAMESPACE"] + "_b")  # purged per test

TEST_DB = os.environ.get("RECALLOPS_TEST_DB", "recallops_test")  # a second checkout can use its own database


def _point_settings_at_test_db() -> None:
    """Everything that reads settings (API engines, seed, tracing) uses the test database and temp storage."""
    base = Settings()
    for name in ("database_url", "database_url_internal", "database_url_owner"):
        url = make_url(getattr(base, name)).set(database=TEST_DB).render_as_string(hide_password=False)
        os.environ[name.upper()] = url
    os.environ.setdefault("STORAGE_DIR", str(Path(tempfile.gettempdir()) / "recallops-test-storage"))
    get_settings.cache_clear()


_point_settings_at_test_db()

# Delete order respects foreign keys. Runs as recallops_internal (BYPASSRLS).
CLEAN_ORDER = (
    "billing_invoices",
    "claim_usage",
    "run_usage_costs",
    "usage_reports",
    "billing_accounts",
    "document_chunks",
    "agent_steps",
    "agent_runs",
    "notifications",
    "registrations",
    "payouts",
    "export_batches",
    "claim_photos",
    "fraud_clusters",
    "mpr_reports",
    "documents",
    "claims",
    "recall_programs",
    "recall_access",
    "audit_log",
    "usage_meters",
    "firm_members",
    "firms",
    "feed_runs",
    "recalls",
    "stripe_events",
    "worker_heartbeats",
    "rate_limits",
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
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))  # test reset tooling only
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    yield internal_engine_test


@pytest.fixture(scope="session")
def _neo4j_store() -> Iterator[Neo4jGraphStore]:
    from recallops.fraud.graph import Neo4jGraphStore
    from recallops.fraud.graph_admin import ensure_schema

    store = Neo4jGraphStore(get_settings())
    ensure_schema(store)
    yield store
    store.close()


@pytest.fixture()
def graph_store(_neo4j_store: Neo4jGraphStore) -> Iterator[Neo4jGraphStore]:
    """The local Neo4j store, with every test namespace emptied before and after the test. Mark users graph."""
    from recallops.fraud.graph import reset_breakers
    from recallops.fraud.graph_admin import purge_namespace

    reset_breakers()
    for ns in GRAPH_TEST_NAMESPACES:
        purge_namespace(_neo4j_store, ns)
    yield _neo4j_store
    reset_breakers()
    for ns in GRAPH_TEST_NAMESPACES:
        purge_namespace(_neo4j_store, ns)
