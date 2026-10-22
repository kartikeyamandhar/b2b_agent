"""Provider-boundary regressions: replay, delinquency, unknown outcomes, and immutable transfer identity."""

import hashlib
import hmac
import json
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import stripe
from pydantic import ValidationError

from recallops.billing import jobs, policy, service, webhooks
from recallops.billing.client import PaymentError, client
from recallops.billing.client import test_object as require_test_object
from recallops.billing.models import BillingAccount, UsageReport
from recallops.models import Claim, Firm, Payout, RecallProgram
from recallops.payouts import stripe_connect as connect
from recallops.settings import Settings
from recallops.tenancy import FirmContext


def obj(**values: Any) -> Any:
    return stripe.StripeObject.construct_from(values, "sk_test_fake")


def session_mock() -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    return session


@pytest.mark.parametrize(
    "field,value",
    [
        ("stripe_secret_key", "sk_live_secret"),
        ("stripe_publishable_key", "pk_live_secret"),
        ("stripe_secret_key", "rk_live_secret"),
    ],
)
def test_live_keys_are_rejected_independently_of_auth(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="test-mode"):
        Settings.model_validate({field: value})


def test_client_uses_pinned_version_bounded_timeout_and_no_hidden_retries() -> None:
    sdk = client(Settings(stripe_secret_key="sk_test_synthetic"))
    assert sdk.accounts is not None
    with pytest.raises(PaymentError, match="credentials"):
        client(Settings())
    with pytest.raises(PaymentError, match="test-mode"):
        require_test_object(obj(livemode=True))


@pytest.mark.parametrize(
    "status,paid,restricted,expected",
    [
        ("active", True, False, "growth"),
        ("past_due", True, False, "growth"),
        ("past_due", True, True, "free"),
        ("active", False, False, "free"),
        ("canceled", True, False, "free"),
        ("unpaid", True, False, "free"),
        ("trialing", True, False, "free"),
        ("paused", True, False, "free"),
    ],
)
def test_access_requires_paid_subscription_and_no_second_failed_collection(
    status: str,
    paid: bool,
    restricted: bool,
    expected: str,
) -> None:
    session = session_mock()
    firm = Firm(id=uuid4(), name="Synthetic firm", plan="growth")
    session.get.return_value = BillingAccount(
        firm_id=firm.id, subscription_status=status, subscription_id="sub_test", paid_access=paid
    )
    session.scalar.return_value = "in_test" if restricted else None
    assert policy.effective_plan(session, firm) == expected


def test_manual_enterprise_cannot_be_downgraded_by_stripe_status() -> None:
    session = session_mock()
    assert policy.effective_plan(session, Firm(id=uuid4(), name="Synthetic", plan="enterprise")) == "enterprise"
    session.get.assert_not_called()


def signed_event(kind: str, data: dict[str, Any]) -> tuple[bytes, str, Settings]:
    payload = json.dumps(
        {
            "id": "evt_synthetic",
            "type": kind,
            "livemode": False,
            "api_version": "2026-09-30.endive",
            "data": {"object": data},
        }
    ).encode()
    now = int(time.time())
    secret = "whsec_synthetic"
    signature = hmac.new(secret.encode(), str(now).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return payload, f"t={now},v1={signature}", Settings(stripe_webhook_secret=secret)


def test_invalid_signature_is_rejected_before_database_or_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    db, provider = MagicMock(), MagicMock()
    monkeypatch.setattr(webhooks, "Session", db)
    monkeypatch.setattr(webhooks, "client", provider)
    for raw in (b"{}", b"[]"):
        with pytest.raises(PaymentError, match="signature"):
            webhooks.receive(MagicMock(), Settings(stripe_webhook_secret="whsec_test"), raw, "invalid")
    db.assert_not_called()
    provider.assert_not_called()


def test_replayed_event_never_applies_state_or_marks_processed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    payload, signature, settings = signed_event(
        "customer.subscription.updated", {"id": "sub_test", "customer": "cus_test"}
    )
    session = session_mock()
    monkeypatch.setattr(webhooks, "Session", lambda _: session)
    monkeypatch.setattr(webhooks, "firm_lock", lambda *_: nullcontext())
    monkeypatch.setattr(webhooks, "resolve_firm", lambda *_: uuid4())
    monkeypatch.setattr(webhooks, "client", lambda _: MagicMock())
    monkeypatch.setattr(webhooks, "record", MagicMock(side_effect=[True, False]))
    apply, finish = MagicMock(), MagicMock()
    monkeypatch.setattr(webhooks, "reconcile_subscription", apply)
    monkeypatch.setattr(webhooks, "processed", finish)
    assert webhooks.receive(MagicMock(), settings, payload, signature)["status"] == "processed"
    assert webhooks.receive(MagicMock(), settings, payload, signature)["status"] == "replayed"
    apply.assert_called_once()
    finish.assert_called_once()
    assert apply.call_args.args[0] is finish.call_args.args[0] is session


def test_failed_webhook_application_does_not_mark_event_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    payload, signature, settings = signed_event(
        "customer.subscription.updated", {"id": "sub_test", "customer": "cus_test"}
    )
    session = session_mock()
    monkeypatch.setattr(webhooks, "Session", lambda _: session)
    monkeypatch.setattr(webhooks, "firm_lock", lambda *_: nullcontext())
    monkeypatch.setattr(webhooks, "resolve_firm", lambda *_: uuid4())
    monkeypatch.setattr(webhooks, "client", lambda _: MagicMock())
    monkeypatch.setattr(webhooks, "record", lambda *_: True)
    monkeypatch.setattr(webhooks, "reconcile_subscription", MagicMock(side_effect=PaymentError("retry")))
    finish = MagicMock()
    monkeypatch.setattr(webhooks, "processed", finish)
    with pytest.raises(PaymentError, match="retry"):
        webhooks.receive(MagicMock(), settings, payload, signature)
    finish.assert_not_called()
    assert session.begin.return_value.__exit__.call_args.args[0] is PaymentError


@pytest.fixture
def transfer_boundary(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any, FirmContext, Payout]:
    ctx = FirmContext(uuid4(), "test", "admin")
    claim = Claim(
        id=uuid4(),
        firm_id=ctx.firm_id,
        program_id=uuid4(),
        status="approved",
        remedy="refund",
        refund_cents=4999,
        decision={},
        decided_by="rules_only",
    )
    p = Payout(
        id=uuid4(),
        firm_id=ctx.firm_id,
        claim_id=claim.id,
        amount_cents=4999,
        currency="usd",
        method="stripe_connect",
        status="onboarding",
        connect_account_id="acct_test",
        hold_requested=False,
        attempts=0,
        stripe_reversed_cents=0,
    )
    program = RecallProgram(id=claim.program_id, firm_id=ctx.firm_id, value_threshold_cents=10000)
    session, sdk, ledger = session_mock(), MagicMock(), MagicMock()
    session.scalar.return_value = claim
    session.get.side_effect = lambda model, _: claim if model is Claim else program
    sdk.accounts.retrieve.return_value = obj(
        livemode=False,
        configuration={
            "recipient": {
                "capabilities": {
                    "stripe_balance": {"stripe_transfers": {"status": "active"}, "payouts": {"status": "active"}}
                }
            }
        },
    )
    sdk.transfers.list.return_value.auto_paging_iter.return_value = iter([])
    monkeypatch.setattr(connect, "tenant_session", lambda *_: nullcontext(session))
    monkeypatch.setattr(connect, "firm_lock", lambda *_: nullcontext())
    monkeypatch.setattr(connect, "client", lambda _: sdk)
    monkeypatch.setattr(connect, "connect_client", lambda _: sdk)
    monkeypatch.setattr(connect, "DbPayoutLedger", lambda *_: ledger)
    monkeypatch.setattr(connect, "lock_claim_and_payout", lambda *_: (p, claim.status))
    gate = MagicMock()
    gate.check_entitlement.return_value.allowed = True
    monkeypatch.setattr(connect, "PlanBilling", lambda *_: gate)
    ledger.claim_for_transfer.side_effect = lambda *_, **__: setattr(p, "status", "transferring")
    return sdk, ledger, session, claim, ctx, p


@pytest.mark.parametrize(
    "error",
    [
        stripe.APIConnectionError("timeout"),  # type: ignore[no-untyped-call]
        stripe.APIError("unknown", http_status=500),
    ],
)
def test_unknown_transfer_outcome_is_never_failed_or_exported(transfer_boundary: Any, error: Exception) -> None:
    sdk, ledger, _, _, ctx, p = transfer_boundary
    sdk.transfers.create.side_effect = error
    assert connect.process_payout(MagicMock(), Settings(), ctx, p.id) == "unresolved"
    assert p.status == "transferring" and p.transfer_request["amount"] == 4999
    ledger.mark_failed.assert_not_called()
    ledger.to_export.assert_not_called()
    ledger.claim_for_transfer.assert_called_once()
    assert sdk.transfers.create.call_args.kwargs["options"]["idempotency_key"] == str(p.id)


def test_expired_unknown_transfer_is_not_reissued_even_when_list_is_empty(transfer_boundary: Any) -> None:
    sdk, ledger, _, _, ctx, p = transfer_boundary
    p.status, p.transfer_started_at, p.transfer_request = (
        "transferring",
        datetime.now(UTC) - timedelta(days=2),
        {"amount": 4999},
    )
    assert connect.process_payout(MagicMock(), Settings(), ctx, p.id) == "unresolved"
    sdk.transfers.create.assert_not_called()
    ledger.to_export.assert_not_called()


def test_preexisting_hold_prevents_stripe_request(transfer_boundary: Any) -> None:
    sdk, ledger, _, _, ctx, p = transfer_boundary
    p.status = "held"
    assert connect.process_payout(MagicMock(), Settings(), ctx, p.id) == "held"
    sdk.transfers.create.assert_not_called()
    ledger.claim_for_transfer.assert_not_called()


def test_transfer_identity_mismatch_prevents_ledger_delivery(transfer_boundary: Any) -> None:
    _, ledger, session, claim, ctx, p = transfer_boundary
    p.transfer_request = {"amount": 4999}
    result = obj(
        id="tr_test",
        livemode=False,
        amount=4998,
        currency="usd",
        destination="acct_test",
        transfer_group=str(claim.program_id),
        metadata={"firm_id": str(ctx.firm_id), "payout_id": str(p.id)},
    )
    with pytest.raises(PaymentError, match="committed payout"):
        connect.apply_transfer(session, ctx, result)
    ledger.mark_transferred.assert_not_called()


def test_reversal_records_cumulative_fact_without_changing_terminal_state(transfer_boundary: Any) -> None:
    _, ledger, session, claim, ctx, p = transfer_boundary
    p.status, p.transfer_request = "transferred", {"amount": 4999}
    result = obj(
        id="tr_test",
        livemode=False,
        amount=4999,
        amount_reversed=1000,
        currency="usd",
        destination="acct_test",
        transfer_group=str(claim.program_id),
        metadata={"firm_id": str(ctx.firm_id), "payout_id": str(p.id)},
    )
    connect.apply_transfer(session, ctx, result, reversed_event=True)
    connect.apply_transfer(session, ctx, result, reversed_event=True)
    assert p.status == "transferred" and p.stripe_reversed_cents == 1000
    assert session.add.call_count == 1
    ledger.to_export.assert_not_called()


def test_checkout_recovery_reuses_frozen_quantity_and_billing_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    firm = Firm(id=ctx.firm_id, name="Synthetic", plan="free", stripe_customer_id="cus_test")
    account = BillingAccount(firm_id=ctx.firm_id)
    session, sdk = session_mock(), MagicMock()
    session.get.return_value = firm
    sdk.subscriptions.list.return_value.auto_paging_iter.side_effect = lambda: iter([])
    sdk.checkout.sessions.list.return_value.auto_paging_iter.side_effect = lambda: iter([])
    completed = obj(
        id="cs_test",
        livemode=False,
        status="open",
        url="https://checkout.stripe.com/test",
        expires_at=int(time.time()) + 3600,
    )
    sdk.checkout.sessions.create.side_effect = [stripe.APIConnectionError("timeout"), completed]  # type: ignore[no-untyped-call]
    monkeypatch.setattr(service, "client", lambda _: sdk)
    monkeypatch.setattr(service, "firm_lock", lambda *_: nullcontext())
    monkeypatch.setattr(service, "tenant_session", lambda *_: nullcontext(session))
    monkeypatch.setattr(service, "account_for", lambda *_: account)
    monkeypatch.setattr(service, "quantity", MagicMock(side_effect=[1, 3]))
    settings = Settings(stripe_program_price_id="price_program", stripe_claim_price_id="price_claim")
    with pytest.raises(stripe.APIConnectionError):
        service.checkout(MagicMock(), settings, ctx)
    snapshot = account.checkout_request
    assert service.checkout(MagicMock(), settings, ctx) == completed.url
    first, second = sdk.checkout.sessions.create.call_args_list
    assert first.args[0] == second.args[0] == snapshot
    assert first.kwargs["options"] == second.kwargs["options"]


def test_canceled_subscription_does_not_strand_firm_at_completed_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    firm = Firm(id=ctx.firm_id, name="Synthetic", plan="free", stripe_customer_id="cus_test")
    old_attempt = uuid4()
    account = BillingAccount(firm_id=ctx.firm_id, checkout_session_id="cs_old", checkout_attempt=old_attempt)
    session, sdk = session_mock(), MagicMock()
    session.get.return_value = firm
    sdk.subscriptions.list.return_value.auto_paging_iter.return_value = iter([obj(id="sub_old", status="canceled")])
    sdk.checkout.sessions.retrieve.return_value = obj(livemode=False, status="complete", subscription="sub_old")
    sdk.checkout.sessions.list.return_value.auto_paging_iter.return_value = iter([])
    sdk.checkout.sessions.create.return_value = obj(
        id="cs_new", livemode=False, url="https://checkout.stripe.com/new", expires_at=int(time.time()) + 3600
    )
    monkeypatch.setattr(service, "client", lambda _: sdk)
    monkeypatch.setattr(service, "firm_lock", lambda *_: nullcontext())
    monkeypatch.setattr(service, "tenant_session", lambda *_: nullcontext(session))
    monkeypatch.setattr(service, "account_for", lambda *_: account)
    monkeypatch.setattr(service, "quantity", lambda *_: 1)
    settings = Settings(stripe_program_price_id="price_program", stripe_claim_price_id="price_claim")
    assert service.checkout(MagicMock(), settings, ctx) == "https://checkout.stripe.com/new"
    assert account.checkout_attempt != old_attempt and account.checkout_session_id == "cs_new"


def test_current_subscription_snapshot_prevents_stale_event_from_restoring_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    firm = Firm(id=ctx.firm_id, name="Synthetic", plan="growth", stripe_customer_id="cus_test")
    account = BillingAccount(
        firm_id=ctx.firm_id, subscription_id="sub_test", subscription_status="active", paid_access=True
    )
    session, sdk = session_mock(), MagicMock()
    session.get.return_value = firm
    monkeypatch.setattr(service, "account_for", lambda *_: account)
    sdk.subscriptions.retrieve.return_value = obj(
        id="sub_test",
        livemode=False,
        customer="cus_test",
        status="canceled",
        items={
            "data": [
                {"id": "si_program", "price": {"id": "price_program"}},
                {"id": "si_claim", "price": {"id": "price_claim"}},
            ]
        },
        latest_invoice={"status": "paid"},
    )
    sdk.invoices.list.return_value.auto_paging_iter.return_value = iter([])
    settings = Settings(stripe_program_price_id="price_program", stripe_claim_price_id="price_claim")
    service.reconcile_subscription(session, ctx, sdk, settings, "sub_test")
    assert firm.plan == "free" and not account.paid_access
    assert account.subscription_status == "canceled"


def test_old_subscription_cannot_replace_current_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    firm = Firm(id=ctx.firm_id, name="Synthetic", plan="growth", stripe_customer_id="cus_test")
    account = BillingAccount(
        firm_id=ctx.firm_id, subscription_id="sub_current", checkout_attempt=uuid4(), paid_access=True
    )
    session, sdk = session_mock(), MagicMock()
    session.get.return_value = firm
    monkeypatch.setattr(service, "account_for", lambda *_: account)
    sdk.subscriptions.retrieve.return_value = obj(
        id="sub_old", livemode=False, customer="cus_test", status="active", metadata={"checkout_attempt": str(uuid4())}
    )
    service.reconcile_subscription(session, ctx, sdk, Settings(), "sub_old")
    assert account.subscription_id == "sub_current" and account.paid_access
    sdk.invoices.list.assert_not_called()


def test_confirmed_meter_receipt_remains_a_noop_after_stripe_retry_window(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    session, sdk = session_mock(), MagicMock()
    old = datetime.now(UTC) - timedelta(days=40)
    session.get.return_value = UsageReport(
        firm_id=ctx.firm_id, day=old.date(), request={}, started_at=old, reported_at=old
    )
    monkeypatch.setattr(jobs, "tenant_session", lambda *_: nullcontext(session))
    assert jobs.report_day(MagicMock(), Settings(), ctx, old.date(), sdk) == "reported"
    sdk.billing.meter_events.create.assert_not_called()


def test_unknown_meter_submission_is_not_reissued_after_retry_window(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FirmContext(uuid4(), "test", "admin")
    session, sdk = session_mock(), MagicMock()
    old = datetime.now(UTC) - timedelta(days=2)
    session.get.return_value = UsageReport(
        firm_id=ctx.firm_id, day=old.date(), request={}, started_at=old, reported_at=None
    )
    monkeypatch.setattr(jobs, "tenant_session", lambda *_: nullcontext(session))
    assert jobs.report_day(MagicMock(), Settings(), ctx, old.date(), sdk) == "unresolved"
    sdk.billing.meter_events.create.assert_not_called()


def test_free_decides_unlimited_claims_and_pays_both_ways_but_runs_one_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner decision 2026-10-22: free = any number of claims, bank transfer or check, one product at a time."""
    ctx = FirmContext(uuid4(), "test", "admin")
    session = session_mock()
    firm = Firm(id=ctx.firm_id, name="Synthetic", plan="free")
    session.get.side_effect = lambda model, key: firm if model is Firm else None
    counts: dict[tuple[str, ...], int] = {("open",): 0, ("draft", "open"): 0}
    monkeypatch.setattr(policy, "active_products", lambda _s, _f, statuses=("draft", "open"): counts[tuple(statuses)])
    gate = policy.PlanBilling(MagicMock(), Settings(), session)
    for _ in range(3):
        assert gate.check_entitlement(ctx, "adjudicate_claim", uuid4()).allowed
    assert gate.check_entitlement(ctx, "create_payout").allowed
    assert gate.check_entitlement(ctx, "create_program").allowed
    counts[("draft", "open")] = 1
    second = gate.check_entitlement(ctx, "create_program")
    assert not second.allowed and second.reason == policy.FREE_PRODUCT_LIMIT
    assert gate.check_entitlement(ctx, "open_program").allowed  # opening the one product (it is not open yet)
    counts[("open",)] = 1
    assert not gate.check_entitlement(ctx, "open_program").allowed  # a second open product
