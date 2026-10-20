"""Signed Stripe events applied atomically through recallops_app and transaction-local tenant context."""

import json
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from recallops.billing.client import PaymentError, client, connect_client, data, firm_lock, object_id, test_object
from recallops.billing.service import invoice_subscription, reconcile_subscription
from recallops.db.session import set_firm
from recallops.logs import get_logger, log_event
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("billing.webhooks")
SUBSCRIPTION_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.created",
    "invoice.paid",
    "invoice.payment_failed",
}
ACCOUNT_EVENTS = {
    "v2.core.account.updated",
    "v2.core.account[configuration.recipient].updated",
    "v2.core.account[configuration.recipient].capability_status_updated",
    "v2.core.account[requirements].updated",
}
CONNECT_EVENTS = ACCOUNT_EVENTS | {"transfer.created", "transfer.reversed"}


def receive(engine: Engine, settings: Settings, payload: bytes, signature: str | None) -> dict[str, str]:
    if not signature:
        raise PaymentError("a configured Stripe signature is required", 400)
    if len(payload) > 1_000_000:
        raise PaymentError("Stripe webhook body is too large", 413)
    try:
        event: dict[str, Any] = json.loads(payload)
        if not isinstance(event, dict):
            raise ValueError("Stripe webhook must be an object")
        secret = (
            settings.stripe_connect_webhook_secret
            if event.get("object") == "v2.core.event"
            else settings.stripe_webhook_secret
        )
        if secret == "whsec_fake":
            raise PaymentError("a configured Stripe signature is required", 400)
        stripe.WebhookSignature.verify_header(payload, signature, secret, 300)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise PaymentError("invalid Stripe webhook signature or payload", 400) from exc
    test_object(event)
    kind, event_id = str(event["type"]), str(event["id"])
    if kind not in ACCOUNT_EVENTS and event.get("api_version") != settings.stripe_api_version:
        raise PaymentError("Stripe webhook API version does not match the pinned version", 400)
    obj: dict[str, Any] = (
        {"id": (event.get("related_object") or {}).get("id")} if kind in ACCOUNT_EVENTS else event["data"]["object"]
    )
    if kind in ACCOUNT_EVENTS and not obj.get("id"):
        raise PaymentError("Stripe account notification is missing its related account", 400)
    log_event(log, "stripe_webhook_received", stripe_event_id=event_id, stripe_event_type=kind)
    with Session(engine) as session, session.begin():
        firm_id = resolve_firm(session, kind, obj)
    if firm_id is None:
        if kind in SUBSCRIPTION_EVENTS | CONNECT_EVENTS and obj.get("metadata", {}).get("firm_id"):
            raise PaymentError("Stripe object is awaiting its committed firm linkage")
        with Session(engine) as session, session.begin():
            if not record(session, event):
                return {"status": "replayed"}
            processed(session, event_id)
        return {"status": "ignored"}
    ctx = FirmContext(UUID(str(firm_id)), "stripe-webhook", "admin")
    with firm_lock(engine, ctx.firm_id), Session(engine) as session, session.begin():
        if not record(session, event):
            return {"status": "replayed"}
        set_firm(session, ctx.firm_id)
        sdk: Any = client(settings) if kind not in ACCOUNT_EVENTS else None
        if kind in SUBSCRIPTION_EVENTS:
            if kind == "checkout.session.completed":
                current = sdk.checkout.sessions.retrieve(str(obj["id"]))
                test_object(current)
                if data(current).get("client_reference_id") != str(ctx.firm_id) or object_id(
                    current.customer
                ) != object_id(obj.get("customer")):
                    raise PaymentError("Checkout firm linkage mismatch", 400)
                sub_id = object_id(data(current).get("subscription"))
            elif kind.startswith("customer.subscription."):
                sub_id = str(obj["id"])
            else:
                current = sdk.invoices.retrieve(str(obj["id"]))
                test_object(current)
                sub_id = invoice_subscription(current)
            if sub_id:
                reconcile_subscription(session, ctx, sdk, settings, sub_id)
        elif kind in ACCOUNT_EVENTS:
            from recallops.payouts.stripe_connect import account_updated

            account_updated(
                session,
                ctx,
                connect_client(settings).accounts.retrieve(str(obj["id"]), {"include": ["configuration.recipient"]}),
            )
        elif kind.startswith("transfer.") and kind in CONNECT_EVENTS:
            from recallops.payouts.stripe_connect import apply_transfer

            # The current transfer includes cumulative reversals even if transfer.created arrives last.
            apply_transfer(session, ctx, sdk.transfers.retrieve(str(obj["id"])), reversed_event=True)
        processed(session, event_id)
    return {"status": "processed"}


def resolve_firm(session: Session, kind: str, obj: Any) -> UUID | None:
    if kind in SUBSCRIPTION_EVENTS:
        customer = object_id(obj.get("customer"))
        if customer:
            found = session.scalar(text("SELECT recallops_firm_by_stripe_customer(:customer)"), {"customer": customer})
            return UUID(str(found)) if found else None
    if kind in CONNECT_EVENTS:
        account = str(obj["id"]) if kind in ACCOUNT_EVENTS else object_id(obj.get("destination"))
        if account:
            found = session.scalar(text("SELECT recallops_firm_by_connect_account(:account)"), {"account": account})
            return UUID(str(found)) if found else None
    return None


def record(session: Session, event: dict[str, Any]) -> bool:
    return bool(
        session.scalar(
            text("SELECT recallops_record_stripe_event(:id, :kind, CAST(:body AS jsonb))"),
            {"id": event["id"], "kind": event["type"], "body": json.dumps(event)},
        )
    )


def processed(session: Session, event_id: str) -> None:
    session.execute(text("SELECT recallops_mark_stripe_event_processed(:id)"), {"id": event_id})
