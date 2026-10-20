"""Firm subscription operations, serialized with webhook reconciliation by firm identity."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.billing.client import PaymentError, client, data, firm_lock, object_id, test_object
from recallops.billing.models import BillingAccount, BillingInvoice
from recallops.db.session import tenant_session
from recallops.models import AuditLog, Firm, RecallProgram
from recallops.settings import Settings
from recallops.tenancy import FirmContext


def audit(session: Session, ctx: FirmContext, action: str, target: str, before: Any, after: Any) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="billing",
            target_id=target,
            before=before,
            after=after,
        )
    )


def account_for(session: Session, ctx: FirmContext) -> BillingAccount:
    session.execute(
        insert(BillingAccount)
        .values(firm_id=ctx.firm_id, paid_access=False, quantity_version=0, quantity_synced_version=-1)
        .on_conflict_do_nothing()
    )
    return session.scalars(select(BillingAccount).where(BillingAccount.firm_id == ctx.firm_id)).one()


def quantity(session: Session, ctx: FirmContext) -> int:
    n = (
        session.scalar(
            select(func.count())
            .select_from(RecallProgram)
            .where(RecallProgram.firm_id == ctx.firm_id, RecallProgram.status == "open")
        )
        or 0
    )
    return max(1, n)


def mark_quantity_dirty(session: Session, ctx: FirmContext) -> None:
    account_for(session, ctx)
    session.execute(
        update(BillingAccount)
        .where(BillingAccount.firm_id == ctx.firm_id)
        .values(quantity_version=BillingAccount.quantity_version + 1)
    )


def checkout(engine: Engine, settings: Settings, ctx: FirmContext) -> str:
    if not settings.stripe_program_price_id or not settings.stripe_claim_price_id:
        raise PaymentError("Stripe test prices are not configured")
    sdk = client(settings)
    with firm_lock(engine, ctx.firm_id):
        with tenant_session(ctx, engine) as session:
            firm = session.get(Firm, ctx.firm_id)
            if firm is None:
                raise PaymentError("firm not found", 404)
            if firm.plan == "enterprise":
                raise PaymentError("enterprise subscriptions are managed manually", 409)
            account = account_for(session, ctx)
            customer = firm.stripe_customer_id
            customer_started = account.customer_started_at
            if not customer and customer_started is None:
                customer_started = account.customer_started_at = datetime.now(UTC)
        if not customer:
            if customer_started and datetime.now(UTC) - customer_started >= timedelta(hours=23):
                matches = [
                    c
                    for c in sdk.customers.list({"limit": 100}).auto_paging_iter()
                    if data(c).get("metadata", {}).get("firm_id") == str(ctx.firm_id)
                ]
                if len(matches) != 1:
                    raise PaymentError("customer creation needs reconciliation; no new customer was created")
                obj = matches[0]
            else:
                obj = sdk.customers.create(
                    {"metadata": {"firm_id": str(ctx.firm_id)}}, options={"idempotency_key": f"customer:{ctx.firm_id}"}
                )
            test_object(obj)
            customer = str(obj.id)
            with tenant_session(ctx, engine) as session:
                firm = session.get(Firm, ctx.firm_id)
                assert firm is not None
                firm.stripe_customer_id = customer
                audit(session, ctx, "billing_customer_linked", customer, None, {"customer": customer})
        # Expired Checkout retries must not create a second subscription whose first event was delayed.
        subscriptions = list(
            sdk.subscriptions.list({"customer": customer, "status": "all", "limit": 100}).auto_paging_iter()
        )
        if any(s.status not in ("canceled", "incomplete_expired") for s in subscriptions):
            raise PaymentError("a subscription already exists; use Customer Portal", 409)
        with tenant_session(ctx, engine) as session:
            account = account_for(session, ctx)
            attempt, started, session_id = (
                account.checkout_attempt,
                account.checkout_started_at,
                account.checkout_session_id,
            )
        if session_id:
            existing = sdk.checkout.sessions.retrieve(session_id)
            test_object(existing)
            if existing.status == "open":
                return str(existing.url)
            ended = {str(sub.id) for sub in subscriptions}
            completed_ended = existing.status == "complete" and object_id(data(existing).get("subscription")) in ended
            if existing.status != "expired" and not completed_ended:
                raise PaymentError("Checkout completed; await subscription reconciliation", 409)
            attempt = None
        if attempt and started and datetime.now(UTC) - started >= timedelta(hours=23):
            matches = [
                s
                for s in sdk.checkout.sessions.list({"customer": customer, "limit": 100}).auto_paging_iter()
                if data(s).get("metadata", {}).get("checkout_attempt") == str(attempt)
            ]
            if len(matches) == 1 and matches[0].status == "open":
                return str(matches[0].url)
            if len(matches) != 1 or matches[0].status != "expired":
                raise PaymentError("Checkout outcome needs reconciliation")
            attempt = None
        with tenant_session(ctx, engine) as session:
            account = account_for(session, ctx)
            if attempt is None:
                attempt = account.checkout_attempt = uuid4()
                account.checkout_started_at = datetime.now(UTC)
                account.checkout_session_id = None
                account.checkout_request = None
            # Freeze the quantity for retries of this Checkout operation.
            n = quantity(session, ctx)
        metadata = {"firm_id": str(ctx.firm_id), "checkout_attempt": str(attempt)}
        # Stripe retains the original session; on retries recover it before rebuilding quantity-dependent parameters.
        sessions = sdk.checkout.sessions.list({"customer": customer, "limit": 100})
        existing = next(
            (
                s
                for s in sessions.auto_paging_iter()
                if data(s).get("metadata", {}).get("checkout_attempt") == str(attempt)
            ),
            None,
        )
        with tenant_session(ctx, engine) as session:
            account = account_for(session, ctx)
            if account.checkout_request is None:
                account.checkout_request = {
                    "mode": "subscription",
                    "customer": customer,
                    "client_reference_id": str(ctx.firm_id),
                    "success_url": settings.billing_return_url,
                    "cancel_url": settings.billing_return_url,
                    "metadata": metadata,
                    "subscription_data": {
                        "metadata": metadata,
                        "billing_cycle_anchor": int(
                            (
                                datetime.now(UTC).replace(day=28, hour=0, minute=0, second=0, microsecond=0)
                                + timedelta(days=4)
                            )
                            .replace(day=1)
                            .timestamp()
                        ),
                    },
                    "line_items": [
                        {"price": settings.stripe_program_price_id, "quantity": n},
                        {"price": settings.stripe_claim_price_id},
                    ],
                }
            request = account.checkout_request
        obj = existing or sdk.checkout.sessions.create(request, options={"idempotency_key": f"checkout:{attempt}"})
        test_object(obj)
        with tenant_session(ctx, engine) as session:
            account = account_for(session, ctx)
            account.checkout_session_id = str(obj.id)
            account.checkout_expires_at = datetime.fromtimestamp(obj.expires_at, UTC)
            audit(session, ctx, "billing_checkout_created", str(obj.id), None, {"quantity": n})
        return str(obj.url)


def portal(engine: Engine, settings: Settings, ctx: FirmContext) -> str:
    with tenant_session(ctx, engine) as session:
        firm = session.get(Firm, ctx.firm_id)
        if firm is None or not firm.stripe_customer_id:
            raise PaymentError("no Stripe customer exists for this firm", 409)
        customer = firm.stripe_customer_id
    if not settings.stripe_portal_configuration_id:
        raise PaymentError("Stripe Customer Portal is not configured")
    obj = client(settings).billing_portal.sessions.create(
        {
            "customer": customer,
            "return_url": settings.billing_return_url,
            "configuration": settings.stripe_portal_configuration_id,
        }
    )
    return str(obj.url)


def billing_summary(session: Session, ctx: FirmContext, settings: Settings) -> dict[str, Any]:
    from recallops.billing.policy import effective_plan

    firm = session.get(Firm, ctx.firm_id)
    if firm is None:
        raise PaymentError("firm not found", 404)
    account = session.get(BillingAccount, ctx.firm_id)
    plan = effective_plan(session, firm)
    invoices = session.scalars(
        select(BillingInvoice)
        .where(BillingInvoice.firm_id == ctx.firm_id)
        .order_by(BillingInvoice.created_at.desc())
        .limit(10)
    ).all()
    return {
        "plan": firm.plan,
        "effective_plan": plan,
        "subscription_status": account.subscription_status if account else None,
        "billing_sync_error": account.last_sync_error if account else None,
        "limits": {
            "open_programs": settings.free_open_programs if plan == "free" else None,
            "claims_per_month": settings.free_claims_per_month if plan == "free" else None,
            "reviewer_seats": settings.free_reviewer_seats if plan == "free" else None,
        },
        "invoices": [
            {
                "id": i.id,
                "status": i.status,
                "attempt_count": i.attempt_count,
                "amount_due_cents": i.amount_due_cents,
                "amount_paid_cents": i.amount_paid_cents,
                "currency": i.currency,
            }
            for i in invoices
        ],
    }


def invoice_subscription(obj: Any) -> str | None:
    details = (data(obj).get("parent") or {}).get("subscription_details") or {}
    return object_id(details.get("subscription"))


def reconcile_subscription(
    session: Session, ctx: FirmContext, sdk: Any, settings: Settings, subscription_id: str
) -> None:
    firm = session.get(Firm, ctx.firm_id)
    if firm is None:
        raise PaymentError("firm linkage is missing")
    account = account_for(session, ctx)
    before = {
        "plan": firm.plan,
        "status": account.subscription_status,
        "paid_access": account.paid_access,
        "subscription_id": account.subscription_id,
    }
    sub = sdk.subscriptions.retrieve(subscription_id, {"expand": ["latest_invoice"]})
    test_object(sub)
    if object_id(sub.customer) != firm.stripe_customer_id:
        raise PaymentError("subscription customer does not match firm", 400)
    if subscription_id != account.subscription_id:
        if data(sub).get("metadata", {}).get("checkout_attempt") != str(account.checkout_attempt):
            return  # An obsolete subscription must not replace the current one.
        if account.subscription_id:
            prior = sdk.subscriptions.retrieve(account.subscription_id)
            if prior.status not in ("canceled", "incomplete_expired"):
                raise PaymentError("competing subscription needs reconciliation")
        account.subscription_id, account.paid_access = subscription_id, False
        account.quantity_synced_version, account.quantity_request = -1, None
    items = sub["items"]["data"]
    programs = [i for i in items if object_id(i.price) == settings.stripe_program_price_id]
    claims = [i for i in items if object_id(i.price) == settings.stripe_claim_price_id]
    if len(programs) != 1 or len(claims) != 1 or len(items) != 2:
        raise PaymentError("subscription does not contain the configured prices", 400)
    program = programs[0]
    account.program_item_id, account.subscription_status = str(program.id), str(sub.status)
    latest = data(sub).get("latest_invoice")
    if latest and not isinstance(latest, str) and data(latest).get("status") == "paid":
        account.paid_access = True
    if sub.status not in ("active", "past_due"):
        account.paid_access = False
    for inv in sdk.invoices.list(
        {"customer": firm.stripe_customer_id, "subscription": subscription_id, "limit": 100}
    ).auto_paging_iter():
        test_object(inv)
        row = session.get(BillingInvoice, str(inv.id))
        if row is not None and row.firm_id != ctx.firm_id:
            raise PaymentError("invoice ownership mismatch", 400)
        financial_fields = ("status", "attempt_count", "amount_due_cents", "amount_paid_cents", "currency")
        invoice_before = {name: getattr(row, name) for name in financial_fields} if row else None
        values = dict(
            firm_id=ctx.firm_id,
            subscription_id=subscription_id,
            status=str(inv.status or "draft"),
            attempt_count=max(row.attempt_count if row else 0, int(inv.attempt_count)),
            amount_due_cents=int(inv.amount_due),
            amount_paid_cents=int(inv.amount_paid),
            currency=str(inv.currency),
            created_at=datetime.fromtimestamp(inv.created, UTC),
            period_start=datetime.fromtimestamp(inv.period_start, UTC),
            period_end=datetime.fromtimestamp(inv.period_end, UTC),
        )
        if row is None:
            session.add(BillingInvoice(id=str(inv.id), **values))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        invoice_after = {name: values[name] for name in financial_fields}
        if invoice_before != invoice_after:
            audit(session, ctx, "billing_invoice_reconciled", str(inv.id), invoice_before, invoice_after)
    if firm.plan != "enterprise":
        firm.plan = "free" if sub.status in ("canceled", "incomplete_expired") else "growth"
    after = {
        "plan": firm.plan,
        "status": account.subscription_status,
        "paid_access": account.paid_access,
        "subscription_id": account.subscription_id,
    }
    if before != after:
        audit(session, ctx, "billing_subscription_reconciled", subscription_id, before, after)
