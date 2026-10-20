"""Daily closed-day meter reporting and convergent program-quantity synchronization."""

import argparse
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import Engine, func, select, text

from recallops.billing.client import PaymentError, client, firm_lock, test_object
from recallops.billing.models import BillingAccount, ClaimUsage, UsageReport
from recallops.billing.service import audit, quantity, reconcile_subscription
from recallops.db.session import tenant_session
from recallops.logs import configure, get_logger, log_event
from recallops.models import Firm
from recallops.settings import Settings, get_settings
from recallops.tenancy import FirmContext

log = get_logger("billing.jobs")


def sync_quantity(engine: Engine, settings: Settings, ctx: FirmContext, sdk: Any) -> None:
    with tenant_session(ctx, engine) as session:
        account = session.get(BillingAccount, ctx.firm_id)
        if not account or not account.subscription_id:
            return
        firm = session.get(Firm, ctx.firm_id)
        if firm is None or firm.plan == "enterprise":
            return
        sub_id = account.subscription_id
        reconcile_subscription(session, ctx, sdk, settings, sub_id)
        session.flush()
        if account.subscription_status not in ("active", "past_due"):
            return
        version, item = account.quantity_version, account.program_item_id
        n = quantity(session, ctx)
        if version == account.quantity_synced_version:
            return
        # Versioned payloads must survive program changes while a provider request is in flight.
        if account.quantity_request is None or account.quantity_request.get("version") != version:
            account.quantity_request = {"version": version, "quantity": n, "item": item}
        request = account.quantity_request
    sdk.subscription_items.update(
        request["item"],
        {"quantity": request["quantity"], "proration_behavior": "create_prorations"},
        options={"idempotency_key": f"quantity:{sub_id}:{version}"},
    )
    with tenant_session(ctx, engine) as session:
        account = session.get(BillingAccount, ctx.firm_id)
        assert account is not None
        account.quantity_synced_version = version
        account.last_sync_error = None
        audit(session, ctx, "billing_quantity_synced", sub_id, None, {"quantity": n, "version": version})


def report_day(engine: Engine, settings: Settings, ctx: FirmContext, day: date, sdk: Any) -> str:
    now = datetime.now(UTC)
    if day >= now.date() or (day == now.date() - timedelta(days=1) and now.hour == 0 and now.minute < 15):
        return "not_closed"
    with tenant_session(ctx, engine) as session:
        row = session.get(UsageReport, (ctx.firm_id, day))
        if row and row.reported_at:
            return "reported"
        if day < now.date() - timedelta(days=34):
            raise PaymentError("usage is outside the reporting window; operator reconciliation required")
        if row and now - row.started_at >= timedelta(hours=23):
            row.last_error = "unknown reporting outcome; operator reconciliation required"
            return "unresolved"
        if row is None:
            firm = session.get(Firm, ctx.firm_id)
            if not firm or not firm.stripe_customer_id:
                return "no_customer"
            start = datetime.combine(day, datetime.min.time(), UTC)
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(ClaimUsage)
                    .where(
                        ClaimUsage.firm_id == ctx.firm_id,
                        ClaimUsage.growth_billable.is_(True),
                        ClaimUsage.counted_at >= start,
                        ClaimUsage.counted_at < start + timedelta(days=1),
                    )
                )
                or 0
            )
            request: dict[str, Any] = {
                "event_name": settings.stripe_meter_event_name,
                "identifier": f"{ctx.firm_id}:{day.isoformat()}",
                "timestamp": int((start + timedelta(hours=23, minutes=59, seconds=59)).timestamp()),
                "payload": {"stripe_customer_id": firm.stripe_customer_id, "value": str(count)},
            }
            row = UsageReport(firm_id=ctx.firm_id, day=day, request=request, started_at=now)
            session.add(row)
        request = row.request
    response = sdk.billing.meter_events.create(request, options={"idempotency_key": f"usage:{ctx.firm_id}:{day}"})
    test_object(response)
    with tenant_session(ctx, engine) as session:
        row = session.get(UsageReport, (ctx.firm_id, day))
        assert row is not None
        row.reported_at, row.last_error = datetime.now(UTC), None
        audit(session, ctx, "billing_usage_reported", str(day), None, {"claims": request["payload"]["value"]})
    return "reported"


def tick(engine: Engine, settings: Settings, day: date | None = None) -> int:
    sdk = client(settings)
    with engine.connect() as conn:
        firms = (
            conn.execute(text("SELECT id FROM firms WHERE stripe_customer_id IS NOT NULL ORDER BY id")).scalars().all()
        )
    attention = 0
    for firm in firms:
        ctx = FirmContext(UUID(str(firm)), "billing-job", "admin")
        try:
            with firm_lock(engine, ctx.firm_id):
                sync_quantity(engine, settings, ctx, sdk)
                target = day or datetime.now(UTC).date() - timedelta(days=1)
                with tenant_session(ctx, engine) as session:
                    source_days = session.scalars(
                        select(func.date(func.timezone("UTC", ClaimUsage.counted_at)))
                        .where(ClaimUsage.firm_id == ctx.firm_id, ClaimUsage.growth_billable.is_(True))
                        .distinct()
                    ).all()
                    pending_days = session.scalars(
                        select(UsageReport.day).where(
                            UsageReport.firm_id == ctx.firm_id, UsageReport.reported_at.is_(None)
                        )
                    ).all()
                days = {day} if day else {*source_days, *pending_days, target}
                for current in sorted(d for d in days if d is not None and d <= target):
                    try:
                        outcome = report_day(engine, settings, ctx, current, sdk)
                        if outcome == "unresolved":
                            raise PaymentError("usage reporting outcome needs operator reconciliation")
                    except (PaymentError, stripe.StripeError) as exc:
                        attention += 1
                        with tenant_session(ctx, engine) as session:
                            account = session.get(BillingAccount, ctx.firm_id)
                            if account:
                                account.last_sync_error = (
                                    str(exc) if isinstance(exc, PaymentError) else type(exc).__name__
                                )
                        log_event(
                            log,
                            "billing_report_failed",
                            level=40,
                            firm_id=str(firm),
                            day=str(current),
                            error=type(exc).__name__,
                        )
        except (PaymentError, stripe.StripeError) as exc:
            attention += 1
            with tenant_session(ctx, engine) as session:
                account = session.get(BillingAccount, ctx.firm_id)
                if account:
                    account.last_sync_error = type(exc).__name__
            log_event(log, "billing_job_failed", level=40, firm_id=str(firm), error=type(exc).__name__)
    return attention


def main() -> None:
    from recallops.db.engine import internal_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--day", type=date.fromisoformat)
    args = parser.parse_args()
    settings = get_settings()
    configure(settings.log_level)
    while True:
        attention = tick(internal_engine(), settings, args.day)
        if not args.loop:
            raise SystemExit(1 if attention else 0)
        time.sleep(settings.payment_poll_s)


if __name__ == "__main__":
    main()
