"""Regressions for the backend review of PR 6 (Stripe test-mode payments): each test failed on the PR as submitted."""

import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
import stripe
from sqlalchemy import Engine, select, text

from recallops.billing.models import ClaimUsage
from recallops.billing.policy import PlanBilling
from recallops.db.session import tenant_session
from recallops.models import Payout, UsageMeter
from recallops.payouts import stripe_connect as connect
from recallops.payouts.db_ledger import DbPayoutLedger, hold_for_claim, lock_claim_and_payout, transition
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from recallops.tracing.writer import DbTrace
from tests.db.test_ledger_retrieval_worker import _firm_program_claim

pytestmark = pytest.mark.db

READY = {
    "livemode": False,
    "configuration": {
        "recipient": {
            "capabilities": {
                "stripe_balance": {"stripe_transfers": {"status": "active"}, "payouts": {"status": "active"}}
            }
        }
    },
}


def _connect_payout(engine: Engine) -> tuple[FirmContext, UUID, UUID, UUID]:
    ctx, program, claim = _firm_program_claim(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE firms SET plan = 'enterprise' WHERE id = :f"), {"f": ctx.firm_id})
        conn.execute(text("UPDATE claims SET remedy = 'refund' WHERE id = :c"), {"c": claim})
    ledger = DbPayoutLedger(engine)
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.mark_onboarding(ctx, ref.payout_id, f"acct_{uuid4().hex[:16]}")
    return ctx, program, claim, ref.payout_id


def _sdk(monkeypatch: pytest.MonkeyPatch, create: Any) -> MagicMock:
    sdk = MagicMock()
    sdk.accounts.retrieve.return_value = stripe.StripeObject.construct_from(READY, "sk_test_fake")
    sdk.transfers.create.side_effect = create
    sdk.transfers.list.return_value.auto_paging_iter.return_value = iter([])
    monkeypatch.setattr(connect, "client", lambda _: sdk)
    monkeypatch.setattr(connect, "connect_client", lambda _: sdk)
    return sdk


def _transferred(request: Any) -> Any:
    return stripe.StripeObject.construct_from(
        {"id": f"tr_{uuid4().hex[:12]}", "livemode": False, "amount_reversed": 0, **request}, "sk_test_fake"
    )


def _rate_limited() -> stripe.RateLimitError:
    return stripe.RateLimitError(
        "rate limited", http_status=429, json_body={"error": {"type": "rate_limit_error", "message": "slow down"}}
    )


def _invalid_request(message: str, status: int) -> stripe.StripeError:
    """A parsed Stripe rejection (the SDK's InvalidRequestError constructor is untyped)."""
    err: stripe.StripeError = stripe.InvalidRequestError(  # type: ignore[no-untyped-call]
        message, None, http_status=status, json_body={"error": {"type": "invalid_request_error"}}
    )
    return err


def _connection_error() -> stripe.StripeError:
    err: stripe.StripeError = stripe.APIConnectionError("connection reset")  # type: ignore[no-untyped-call]
    return err


def _payout(engine: Engine, ctx: FirmContext, payout_id: UUID) -> Payout:
    with tenant_session(ctx, engine) as session:
        p = session.get(Payout, payout_id)
        assert p is not None
        session.expunge(p)
        return p


def _past_backoff(engine: Engine, payout_id: UUID) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE payouts SET transfer_retry_at = now() - interval '1 minute' WHERE id = :p"), {"p": payout_id}
        )


def test_a_rate_limited_transfer_is_retried_and_paid_once(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry used to rewrite transfer_started_at in a second UPDATE, which the payouts trigger refuses."""
    ctx, _, _, payout_id = _connect_payout(clean_db)
    calls: list[int] = []

    def create(request: Any, **_: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise _rate_limited()
        return _transferred(request)

    _sdk(monkeypatch, create)
    assert connect.process_payout(clean_db, get_settings(), ctx, payout_id) == "failed"
    first_started = _payout(clean_db, ctx, payout_id).transfer_started_at
    assert first_started is not None
    _past_backoff(clean_db, payout_id)
    assert connect.process_payout(clean_db, get_settings(), ctx, payout_id) == "transferred"
    paid = _payout(clean_db, ctx, payout_id)
    assert paid.status == "transferred" and len(calls) == 2
    assert paid.transfer_started_at == first_started  # the first dispatch anchors the replay window


def test_three_rate_limits_fall_back_to_a_check_without_crashing(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _, _, payout_id = _connect_payout(clean_db)

    def create(request: Any, **_: Any) -> Any:
        raise _rate_limited()

    _sdk(monkeypatch, create)
    results = []
    for _ in range(3):
        results.append(connect.process_payout(clean_db, get_settings(), ctx, payout_id))
        _past_backoff(clean_db, payout_id)
    assert results == ["failed", "failed", "pending_export"]
    assert _payout(clean_db, ctx, payout_id).method == "check_export"


def test_one_failing_payout_does_not_stop_the_connect_job(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, first = _connect_payout(clean_db)
    _, _, _, second = _connect_payout(clean_db)
    seen: list[UUID] = []

    def process(engine: Engine, settings: Any, ctx: FirmContext, payout_id: UUID) -> str:
        seen.append(payout_id)
        if payout_id == first:
            raise RuntimeError("a database error on one row")
        return "transferred"

    monkeypatch.setattr(connect, "process_payout", process)
    assert connect.tick(clean_db, get_settings()) == 1
    assert seen == [first, second]


@pytest.mark.parametrize(
    ("exc", "confirmed"),
    [
        (stripe.APIError("Invalid response body from API", http_body="<html>", http_status=200), False),
        (stripe.APIError("proxy page", http_body="<html>", http_status=403), False),
        (_connection_error(), False),
        (stripe.APIError("server", http_status=502, json_body={"error": {"type": "api_error"}}), False),
        (stripe.IdempotencyError("in use", http_status=400, json_body={"error": {"type": "idempotency_error"}}), False),
        (_invalid_request("busy", 409), False),
        (_invalid_request("insufficient funds", 400), True),
    ],
)
def test_only_a_parsed_4xx_counts_as_a_confirmed_rejection(exc: stripe.StripeError, confirmed: bool) -> None:
    assert connect.confirmed_rejection(exc) is confirmed


def test_an_unreadable_success_response_stays_transferring_and_is_never_exported(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stripe 16.0.0 raises APIError with the real status (here 200) when it cannot read the body."""
    ctx, _, _, payout_id = _connect_payout(clean_db)

    def create(request: Any, **_: Any) -> Any:
        raise stripe.APIError("Invalid response body from API: <html>", http_body="<html>", http_status=200)

    _sdk(monkeypatch, create)
    assert connect.process_payout(clean_db, get_settings(), ctx, payout_id) == "unresolved"
    p = _payout(clean_db, ctx, payout_id)
    assert p.status == "transferring" and p.method == "stripe_connect"


def test_a_reapproved_connect_payout_with_an_account_returns_to_onboarding(clean_db: Engine) -> None:
    """Released to pending, it waited for an onboarding that had already happened; the Connect job skips pending."""
    ctx, _, claim, payout_id = _connect_payout(clean_db)
    ledger = DbPayoutLedger(clean_db)
    with tenant_session(ctx, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
        assert hold_for_claim(session, ctx, claim, "reviewer override") == "held"
        # A confirmed earlier failure left the dispatched request on the row.
        session.execute(
            text("UPDATE payouts SET transfer_request = CAST(:r AS jsonb) WHERE id = :p"),
            {"r": '{"amount": 4999}', "p": payout_id},
        )
        session.execute(text("UPDATE claims SET status = 'approved' WHERE id = :c"), {"c": claim})
    assert ledger.create_pending(ctx, claim, 4999).status == "onboarding"
    # The claimant can still switch to a mailed check: the trigger allows it from onboarding, not only from failed.
    with tenant_session(ctx, clean_db) as session:
        p, _ = lock_claim_and_payout(session, ctx, payout_id)
        transition(session, ctx, p, "pending_export", method="check_export")
    assert _payout(clean_db, ctx, payout_id).status == "pending_export"


def test_a_reapproved_connect_payout_without_an_account_returns_to_pending(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ledger.create_pending(ctx, claim, 4999)
    with tenant_session(ctx, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
        hold_for_claim(session, ctx, claim, "reviewer override")
        session.execute(text("UPDATE claims SET status = 'approved' WHERE id = :c"), {"c": claim})
    assert ledger.create_pending(ctx, claim, 4999).status == "pending"


def test_a_confirmed_failure_fallback_to_a_check_holds_a_shared_destination(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The automatic switch to a mailed check passes the same shared-address hold as a claimant's own choice."""
    ctx, program, claim, payout_id = _connect_payout(clean_db)
    home = {"address_line1": "1 Shared Ave", "city": "Town", "state": "CA", "zip": "90001"}
    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE claims SET claimant = CAST(:c AS jsonb) WHERE id = :id"),
            {"c": json.dumps(home), "id": claim},
        )
        conn.execute(
            text(
                "INSERT INTO claims (id, firm_id, program_id, token_hash, status, claimant, submitted_at) "
                "VALUES (:c, :f, :p, :t, 'denied', CAST(:j AS jsonb), now())"
            ),
            {
                "c": uuid4(),
                "f": ctx.firm_id,
                "p": program,
                "t": uuid4().hex,
                "j": json.dumps({"mailing_address": "1 Shared Ave Town CA 90001"}),
            },
        )

    def create(request: Any, **_: Any) -> Any:
        raise _invalid_request("account closed", 400)

    _sdk(monkeypatch, create)
    assert connect.process_payout(clean_db, get_settings(), ctx, payout_id) == "held"
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT status FROM claims WHERE id = :c"), {"c": claim}) == "under_review"
        assert conn.scalar(text("SELECT count(*) FROM export_batches")) == 0


def test_a_resume_in_the_next_month_keeps_the_first_record_and_does_not_raise(clean_db: Engine) -> None:
    """Postgres checks the claim_usage period CHECK before ON CONFLICT, so retrying the claim insert on a resume in
    the next month raised; the claim's count is now decided once per run, at its first record."""
    ctx, program, claim = _firm_program_claim(clean_db)
    billing = PlanBilling(clean_db, get_settings())
    first = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    first.finish("done")
    second = DbTrace(clean_db, "rules_only", ctx.firm_id, claim, program)
    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE agent_runs SET finished_at = '2026-10-10T12:00:00Z' WHERE id = :r"), {"r": first.run_id}
        )
        conn.execute(
            text("UPDATE agent_runs SET finished_at = '2026-10-30T12:00:00Z' WHERE id = :r"), {"r": second.run_id}
        )
    billing.record_usage(ctx, claim, first.run_id, Decimal("0.001"))
    billing.record_usage(ctx, claim, second.run_id, Decimal(0))  # needs_info answered, a new run that holds
    with clean_db.begin() as conn:  # the reviewer's resume of the second run finishes in November
        conn.execute(
            text("UPDATE agent_runs SET finished_at = '2026-11-02T09:00:00Z' WHERE id = :r"), {"r": second.run_id}
        )
    billing.record_usage(ctx, claim, second.run_id, Decimal("0.002"))
    with tenant_session(ctx, clean_db) as session:
        entries = session.scalars(select(ClaimUsage).where(ClaimUsage.firm_id == ctx.firm_id)).all()
        meters = session.scalars(select(UsageMeter).where(UsageMeter.firm_id == ctx.firm_id)).all()
    assert [str(e.period_start) for e in entries] == ["2026-10-01"]
    assert [(str(m.period_start), m.claims_adjudicated, m.model_cost_usd) for m in meters] == [
        ("2026-10-01", 1, Decimal("0.003"))
    ]
