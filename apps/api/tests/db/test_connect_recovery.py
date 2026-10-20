"""Real ledger transactions surrounding a fake Stripe boundary; requires the backend payment schema."""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import stripe
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from recallops.billing import webhooks
from recallops.db.session import tenant_session
from recallops.models import Payout
from recallops.payouts import stripe_connect as connect
from recallops.payouts.db_ledger import DbPayoutLedger, hold_for_claim
from recallops.settings import get_settings
from tests.db.test_ledger_retrieval_worker import _firm_program_claim

pytestmark = pytest.mark.db


def test_success_after_a_hold_is_recorded_once_and_never_exported(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE firms SET plan = 'enterprise' WHERE id = :f"), {"f": ctx.firm_id})
        conn.execute(text("UPDATE claims SET remedy = 'refund' WHERE id = :c"), {"c": claim})
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, ref.payout_id, "acct_synthetic")
    sdk = MagicMock()
    sdk.accounts.retrieve.return_value = stripe.StripeObject.construct_from(
        {
            "livemode": False,
            "configuration": {
                "recipient": {
                    "capabilities": {
                        "stripe_balance": {"stripe_transfers": {"status": "active"}, "payouts": {"status": "active"}}
                    }
                }
            },
        },
        "sk_test_fake",
    )

    def send(request: Any, **_: Any) -> Any:
        with tenant_session(ctx, clean_db) as session:
            session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
            assert hold_for_claim(session, ctx, claim, "concurrent reviewer hold") == "transferring"
        return stripe.StripeObject.construct_from(
            {"id": "tr_synthetic", "livemode": False, "amount_reversed": 0, **request}, "sk_test_fake"
        )

    sdk.transfers.create.side_effect = send
    monkeypatch.setattr(connect, "client", lambda _: sdk)
    monkeypatch.setattr(connect, "connect_client", lambda _: sdk)
    assert connect.process_payout(clean_db, get_settings(), ctx, ref.payout_id) == "transferred"
    assert connect.process_payout(clean_db, get_settings(), ctx, ref.payout_id) == "transferred"
    sdk.transfers.create.assert_called_once()
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM audit_log WHERE action = 'payout_paid_despite_hold'")) == 1
        assert conn.scalar(text("SELECT count(*) FROM export_batches")) == 0


def test_unknown_outcome_past_window_stays_transferring(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, ref.payout_id, "acct_synthetic")
    ledger.claim_for_transfer(ctx, ref.payout_id)
    with tenant_session(ctx, clean_db) as session:
        p = session.get(Payout, ref.payout_id)
        assert p is not None
        p.transfer_started_at = datetime.now(UTC) - timedelta(days=2)
        p.transfer_request = {"amount": 4999}
    sdk = MagicMock()
    sdk.transfers.list.return_value.auto_paging_iter.return_value = iter([])
    monkeypatch.setattr(connect, "client", lambda _: sdk)
    monkeypatch.setattr(connect, "connect_client", lambda _: sdk)
    assert connect.process_payout(clean_db, get_settings(), ctx, ref.payout_id) == "unresolved"
    sdk.transfers.create.assert_not_called()
    assert ledger.get(ctx, ref.payout_id).ref.status == "transferring"


def test_connect_resolver_is_narrow_and_dispatched_money_is_frozen(clean_db: Engine, app_engine_test: Engine) -> None:
    ctx, program, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    payout = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, payout.payout_id, "acct_resolver")
    with Session(app_engine_test) as session, session.begin():
        assert session.scalar(text("SELECT recallops_firm_by_connect_account('acct_resolver')")) == ctx.firm_id
        assert session.scalar(text("SELECT count(*) FROM payouts")) == 0
        assert session.scalar(text("SELECT recallops_firm_by_connect_account('acct_missing')")) is None

    ledger.claim_for_transfer(ctx, payout.payout_id)
    with tenant_session(ctx, app_engine_test) as session:
        row = session.get(Payout, payout.payout_id)
        assert row is not None
        row.transfer_request = {"amount": 4999, "transfer_group": str(program)}
        row.transfer_started_at = datetime.now(UTC)
    for sql in (
        "amount_cents = 5000",
        "connect_account_id = 'acct_other'",
        "transfer_request = '{}'",
        "transfer_started_at = now()",
    ):
        with (
            pytest.raises(DBAPIError, match=r"immutable|first request time"),
            tenant_session(ctx, app_engine_test) as session,
        ):
            session.execute(text(f"UPDATE payouts SET {sql} WHERE id = :p"), {"p": payout.payout_id})


def test_confirmed_failure_can_select_check_export_without_changing_transfer_identity(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    payout = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, payout.payout_id, "acct_fallback")
    ledger.claim_for_transfer(ctx, payout.payout_id)
    request = {"amount": 4999, "currency": "usd", "destination": "acct_fallback"}
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Payout, payout.payout_id)
        assert row
        row.transfer_request = request
    ledger.mark_failed(ctx, payout.payout_id, "balance_insufficient")
    ledger.to_export(ctx, payout.payout_id)
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Payout, payout.payout_id)
        assert row and row.status == "pending_export" and row.method == "check_export"
        assert row.transfer_request == request and row.stripe_transfer_id is None


def test_accounts_v2_onboarding_reuses_account_and_refreshes_ephemeral_links(
    clean_db: Engine, app_engine_test: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE firms SET plan = 'enterprise' WHERE id = :f"), {"f": ctx.firm_id})
        conn.execute(
            text(
                "UPDATE claims SET remedy='refund', claimant=jsonb_build_object('email','avery@example.com') WHERE id=:c"
            ),
            {"c": claim},
        )
    ledger = DbPayoutLedger(app_engine_test)
    payout = ledger.create_pending(ctx, claim, 4999)
    core = MagicMock()
    core.accounts.create.return_value = stripe.StripeObject.construct_from(
        {"id": "acct_v2", "object": "v2.core.account", "livemode": False}, "sk_test_fake"
    )
    core.account_links.create.side_effect = [
        MagicMock(url="https://connect.stripe.com/first"),
        MagicMock(url="https://connect.stripe.com/refresh"),
    ]
    monkeypatch.setattr(connect, "connect_client", lambda _: core)
    assert connect.onboarding(app_engine_test, get_settings(), ctx, claim).endswith("/first")
    assert connect.onboarding(app_engine_test, get_settings(), ctx, claim).endswith("/refresh")
    core.accounts.create.assert_called_once()
    assert core.account_links.create.call_count == 2
    assert ledger.get(ctx, payout.payout_id).ref.status == "onboarding"
    assert core.accounts.create.call_args.args[0]["configuration"]["recipient"]["capabilities"]["stripe_balance"][
        "stripe_transfers"
    ]["requested"]


def test_thin_account_notification_reconciles_current_readiness_once_without_dispatch(
    clean_db: Engine, app_engine_test: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(app_engine_test)
    payout = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, payout.payout_id, "acct_thin")
    core = MagicMock()
    core.accounts.retrieve.return_value = stripe.StripeObject.construct_from(
        {
            "id": "acct_thin",
            "object": "v2.core.account",
            "livemode": False,
            "metadata": {"firm_id": str(ctx.firm_id), "payout_id": str(payout.payout_id)},
            "configuration": {
                "recipient": {
                    "capabilities": {
                        "stripe_balance": {"stripe_transfers": {"status": "active"}, "payouts": {"status": "active"}}
                    }
                }
            },
        },
        "sk_test_fake",
    )
    monkeypatch.setattr(webhooks, "connect_client", lambda _: core)
    settings = get_settings().model_copy(update={"stripe_connect_webhook_secret": SecretStr("whsec_thin_test")})
    raw = json.dumps(
        {
            "id": "evt_thin_test",
            "object": "v2.core.event",
            "livemode": False,
            "type": "v2.core.account[configuration.recipient].capability_status_updated",
            "related_object": {"id": "acct_thin", "type": "v2.core.account"},
        }
    ).encode()
    at = str(int(time.time()))
    signature = (
        f"t={at},v1="
        + hmac.new(
            settings.stripe_connect_webhook_secret.get_secret_value().encode(), at.encode() + b"." + raw, hashlib.sha256
        ).hexdigest()
    )
    assert webhooks.receive(app_engine_test, settings, raw, signature)["status"] == "processed"
    assert webhooks.receive(app_engine_test, settings, raw, signature)["status"] == "replayed"
    core.accounts.retrieve.assert_called_once()
    with tenant_session(ctx, app_engine_test) as session:
        row = session.get(Payout, payout.payout_id)
        assert row is not None and row.connect_ready and row.status == "onboarding"
        assert row.transfer_request is None and row.stripe_transfer_id is None
    core.transfers.create.assert_not_called()
