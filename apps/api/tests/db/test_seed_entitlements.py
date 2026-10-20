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
