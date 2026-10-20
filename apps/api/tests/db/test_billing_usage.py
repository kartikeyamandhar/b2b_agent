"""Postgres usage identities protect claim limits and reporting against resumes and duplicate work."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import MagicMock

import pytest
import stripe
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from recallops.billing import jobs
from recallops.billing.jobs import report_day
from recallops.billing.models import BillingAccount, BillingInvoice, ClaimUsage, UsageReport
from recallops.billing.policy import PlanBilling
from recallops.billing.service import mark_quantity_dirty
from recallops.db.session import tenant_session
from recallops.models import Firm, UsageMeter
from recallops.settings import get_settings
from recallops.tracing.writer import DbTrace
from tests.db.test_ledger_retrieval_worker import _firm_program_claim

pytestmark = pytest.mark.db


def test_resume_and_reopen_count_one_claim_but_keep_all_run_cost(clean_db: Engine) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    billing = PlanBilling(clean_db, get_settings())
    first = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    first.finish("done")
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.001"))
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.002"))
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.002"))
    reopened = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    reopened.finish("done")
    billing.record_usage(ctx, claim, reopened.run_id, Decimal("0.003"))
    with tenant_session(ctx, clean_db) as session:
        meter = session.scalars(select(UsageMeter).where(UsageMeter.firm_id == ctx.firm_id)).one()
        entries = session.scalars(select(ClaimUsage).where(ClaimUsage.firm_id == ctx.firm_id)).all()
        assert meter.claims_adjudicated == 1 and meter.model_cost_usd == Decimal("0.005")
        assert len(entries) == 1 and not entries[0].growth_billable


def test_rolled_back_accounting_can_be_recovered_once(clean_db: Engine) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    billing = PlanBilling(clean_db, get_settings())
    trace = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    trace.finish("done")
    with pytest.raises(RuntimeError), tenant_session(ctx, clean_db) as session:
        billing.record_usage(ctx, claim, trace.run_id, Decimal("0.001"), session=session)
        raise RuntimeError("crash before usage commit")
    billing.record_usage(ctx, claim, trace.run_id, Decimal("0.001"))
    with tenant_session(ctx, clean_db) as session:
        meter = session.scalars(select(UsageMeter).where(UsageMeter.firm_id == ctx.firm_id)).one()
        assert meter.claims_adjudicated == 1 and meter.model_cost_usd == Decimal("0.001")
        assert meter.period_start == datetime.now(UTC).date().replace(day=1)


def test_upgrade_does_not_retroactively_bill_free_usage_across_utc_months(clean_db: Engine) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    billing = PlanBilling(clean_db, get_settings())
    first = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    first.finish("done")
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE agent_runs SET finished_at='2026-01-31T23:59:59Z' WHERE id=:r"), {"r": first.run_id})
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.001"))
    with tenant_session(ctx, clean_db) as session:
        firm = session.get(Firm, ctx.firm_id)
        assert firm
        firm.plan = "growth"
        session.add(BillingAccount(firm_id=ctx.firm_id, subscription_status="active", paid_access=True))
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.002"))
    reopened = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    reopened.finish("done")
    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE agent_runs SET finished_at='2026-02-01T00:00:00Z' WHERE id=:r"), {"r": reopened.run_id}
        )
    billing.record_usage(ctx, claim, reopened.run_id, Decimal("0.003"))
    with tenant_session(ctx, clean_db) as session:
        entries = session.scalars(
            select(ClaimUsage).where(ClaimUsage.firm_id == ctx.firm_id).order_by(ClaimUsage.period_start)
        ).all()
        meters = session.scalars(
            select(UsageMeter).where(UsageMeter.firm_id == ctx.firm_id).order_by(UsageMeter.period_start)
        ).all()
        assert [e.growth_billable for e in entries] == [False, True]
        assert [m.claims_adjudicated for m in meters] == [1, 1]
        assert [m.model_cost_usd for m in meters] == [Decimal("0.002"), Decimal("0.003")]


def test_closed_day_report_receipt_survives_another_job_run(clean_db: Engine) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    at = datetime.now(UTC) - timedelta(days=2)
    with tenant_session(ctx, clean_db) as session:
        firm = session.get(Firm, ctx.firm_id)
        assert firm
        firm.plan, firm.stripe_customer_id = "growth", "cus_receipt"
        session.add(BillingAccount(firm_id=ctx.firm_id, subscription_status="active", paid_access=True))
    trace = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    trace.finish("done")
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE agent_runs SET finished_at=:at WHERE id=:id"), {"at": at, "id": trace.run_id})
    PlanBilling(clean_db, get_settings()).record_usage(ctx, claim, trace.run_id, Decimal(0))
    sdk = MagicMock()
    sdk.billing.meter_events.create.return_value = stripe.StripeObject.construct_from(
        {"livemode": False}, "sk_test_fake"
    )
    assert report_day(clean_db, get_settings(), ctx, at.date(), sdk) == "reported"
    assert report_day(clean_db, get_settings(), ctx, at.date(), sdk) == "reported"
    sdk.billing.meter_events.create.assert_called_once()
    with tenant_session(ctx, clean_db) as session:
        receipt = session.get(UsageReport, (ctx.firm_id, at.date()))
        assert receipt and receipt.reported_at and receipt.request["payload"]["value"] == "1"
        assert receipt.request["identifier"] == f"{ctx.firm_id}:{at.date()}"


def test_second_unpaid_attempt_restricts_and_settlement_restores_growth(clean_db: Engine) -> None:
    ctx, _, _ = _firm_program_claim(clean_db)
    with tenant_session(ctx, clean_db) as session:
        firm = session.get(Firm, ctx.firm_id)
        assert firm is not None
        firm.plan = "growth"
        session.add(
            BillingAccount(
                firm_id=ctx.firm_id, subscription_id="sub_synthetic", subscription_status="past_due", paid_access=True
            )
        )
        session.add(
            BillingInvoice(
                id="in_synthetic",
                firm_id=ctx.firm_id,
                subscription_id="sub_synthetic",
                status="open",
                attempt_count=1,
                amount_due_cents=19900,
                amount_paid_cents=0,
                currency="usd",
                created_at=datetime.now(UTC),
                period_start=datetime.now(UTC),
                period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
    billing = PlanBilling(clean_db, get_settings())
    assert billing.check_entitlement(ctx, "create_payout").allowed
    with tenant_session(ctx, clean_db) as session:
        invoice = session.get(BillingInvoice, "in_synthetic")
        assert invoice is not None
        invoice.attempt_count = 2
    assert not billing.check_entitlement(ctx, "create_payout").allowed
    assert not billing.check_entitlement(ctx, "create_payout").allowed
    with tenant_session(ctx, clean_db) as session:
        invoice = session.get(BillingInvoice, "in_synthetic")
        assert invoice is not None
        invoice.status, invoice.amount_paid_cents = "paid", 19900
    assert billing.check_entitlement(ctx, "create_payout").allowed


def test_concurrent_program_changes_preserve_each_quantity_sync_request(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, _ = _firm_program_claim(clean_db)
    with tenant_session(ctx, clean_db) as session:
        session.add(BillingAccount(firm_id=ctx.firm_id, quantity_version=0, quantity_synced_version=-1))
    barrier = Barrier(2)

    def change_program() -> None:
        with tenant_session(ctx, clean_db) as session:
            cached = session.get(BillingAccount, ctx.firm_id)
            assert cached is not None and cached.quantity_version == 0
            barrier.wait(timeout=10)
            mark_quantity_dirty(session, ctx)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(change_program) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    with tenant_session(ctx, clean_db) as session:
        account = session.get(BillingAccount, ctx.firm_id)
        assert account is not None and account.quantity_version == 2
        account.subscription_id = "sub_quantity"
        account.subscription_status = "active"
        account.program_item_id = "si_quantity"
    sdk = MagicMock()
    monkeypatch.setattr(jobs, "reconcile_subscription", lambda *_: None)
    sdk.subscription_items.update.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        jobs.sync_quantity(clean_db, get_settings(), ctx, sdk)
    with tenant_session(ctx, clean_db) as session:
        account = session.get(BillingAccount, ctx.firm_id)
        assert account and account.quantity_request and account.quantity_request["version"] == 2
        assert account.quantity_synced_version == -1
        mark_quantity_dirty(session, ctx)
    sdk.subscription_items.update.side_effect = None
    jobs.sync_quantity(clean_db, get_settings(), ctx, sdk)
    with tenant_session(ctx, clean_db) as session:
        account = session.get(BillingAccount, ctx.firm_id)
        assert account and account.quantity_synced_version == account.quantity_version == 3
    keys = [call.kwargs["options"]["idempotency_key"] for call in sdk.subscription_items.update.call_args_list]
    assert len(set(keys)) == 2


def test_usage_requests_cannot_be_rewritten_or_attached_to_another_claim(
    clean_db: Engine, app_engine_test: Engine
) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    other, _, foreign_claim = _firm_program_claim(clean_db)
    trace = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    trace.finish("done")
    PlanBilling(clean_db, get_settings()).record_usage(ctx, claim, trace.run_id, Decimal("0.002"))
    with pytest.raises(DBAPIError, match="permission denied"), tenant_session(ctx, app_engine_test) as session:
        session.execute(text("UPDATE claim_usage SET growth_billable = true WHERE firm_id = :f"), {"f": ctx.firm_id})
    with pytest.raises(DBAPIError, match="cannot decrease"), tenant_session(ctx, app_engine_test) as session:
        session.execute(text("UPDATE run_usage_costs SET recorded_cost = 0 WHERE run_id = :r"), {"r": trace.run_id})
    with pytest.raises(DBAPIError, match="foreign key"), tenant_session(other, app_engine_test) as session:
        session.execute(
            text("INSERT INTO claim_usage VALUES (:f,:c,'2026-10-01',:r,'2026-10-20',false)"),
            {"f": other.firm_id, "c": foreign_claim, "r": trace.run_id},
        )
    with tenant_session(ctx, app_engine_test) as session:
        session.execute(
            text(
                "INSERT INTO usage_reports (firm_id,day,request) VALUES (:f,'2026-10-20',jsonb_build_object('value',1))"
            ),
            {"f": ctx.firm_id},
        )
    with pytest.raises(DBAPIError, match="immutable"), tenant_session(ctx, app_engine_test) as session:
        session.execute(
            text("UPDATE usage_reports SET request = jsonb_build_object('value',2) WHERE firm_id = :f"),
            {"f": ctx.firm_id},
        )
