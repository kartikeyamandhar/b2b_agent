"""The seeded demo firm stays entitled under the real billing policy (backend review of PR 6): before, the seed made
it a growth firm with no paid subscription, which counts as free, so its 500 seeded claims blocked every new one."""

from uuid import uuid4

import pytest
from sqlalchemy import Engine

from recallops import seed
from recallops.billing.policy import PlanBilling
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from tests.conftest import CLEAN_ORDER

pytestmark = pytest.mark.db


def test_a_new_demo_claim_and_a_second_program_are_entitled_after_the_seed(internal_engine_test: Engine) -> None:
    from sqlalchemy import text

    with internal_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    n = get_settings().free_claims_per_month + 5  # more than a free firm may decide in a month
    seed.run(internal_engine_test, n=n)
    billing = PlanBilling(internal_engine_test, get_settings())
    demo = FirmContext(firm_id=seed.DEMO_FIRM_ID, user_id="tester", role="admin")
    assert billing.check_entitlement(demo, "adjudicate_claim", claim_id=uuid4()).allowed
    assert billing.check_entitlement(demo, "open_program").allowed


def test_a_running_worker_never_takes_a_claim_the_seed_has_not_decided(
    internal_engine_test: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed commits its claims as submitted and decides them afterwards. A worker running at the same time (the
    production agent worker during the first deploy, 2026-10-21) would lease them in between and spend model money
    on demo data. Seeded claims carry the seed's lease until their decision is recorded."""
    from sqlalchemy import text

    from recallops.claims.worker import Worker
    from recallops.settings import Settings
    from tests import fakes

    with internal_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    worker = Worker(
        engine=internal_engine_test, settings=Settings(adjudicator="rules_only"), llm=fakes.client([])[0], worker_id="w"
    )
    taken: list[object] = []
    real_insert = seed.insert_claims

    def insert_then_poll(*args: object, **kwargs: object) -> None:
        real_insert(*args, **kwargs)  # type: ignore[arg-type]
        taken.append(worker.lease_claim())  # what a concurrent worker sees before the seed decides

    monkeypatch.setattr(seed, "insert_claims", insert_then_poll)
    seed.run(internal_engine_test, n=6)
    assert taken == [None, None]  # both seeded programs: nothing to lease
    with internal_engine_test.connect() as conn:
        leased = conn.execute(text("SELECT count(*) FROM claims WHERE lease_owner IS NOT NULL")).scalar_one()
    assert leased == 0  # every decided claim's seed lease is released
