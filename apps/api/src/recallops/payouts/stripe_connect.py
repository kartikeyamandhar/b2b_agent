"""Ledger-first Connect Express onboarding, dispatch, and reconciliation (Stripe test mode only)."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from recallops.billing.client import PaymentError, client, connect_client, data, firm_lock, object_id, test_object
from recallops.billing.policy import PlanBilling
from recallops.db.session import tenant_session
from recallops.logs import configure, get_logger, log_event
from recallops.models import AuditLog, Claim, Payout, RecallProgram
from recallops.payouts.db_ledger import DbPayoutLedger, NotPayable, lock_claim_and_payout, lock_program_money
from recallops.settings import Settings, get_settings
from recallops.tenancy import FirmContext

log = get_logger("payouts.stripe_connect")
SAFE_WINDOW = timedelta(hours=23)


def payable(session: Session, ctx: FirmContext, payout: Payout) -> Claim:
    claim = session.scalar(select(Claim).where(Claim.firm_id == ctx.firm_id, Claim.id == payout.claim_id))
    if claim is None or claim.status != "approved" or payout.hold_requested:
        raise NotPayable("the refund is not approved for transfer")
    program = session.get(RecallProgram, claim.program_id)
    if payout.method != "stripe_connect" or payout.amount_cents <= 0 or payout.currency != "usd":
        raise NotPayable("the payout is not a positive USD Connect refund")
    if claim.refund_cents != payout.amount_cents or claim.remedy != "refund" or program is None:
        raise NotPayable("the ledger amount does not match the approved refund")
    if payout.amount_cents > program.value_threshold_cents and (
        not (claim.decision or {}).get("review_action") or claim.decided_by in (None, "agent", "rules_only")
    ):
        raise NotPayable("this refund requires recorded reviewer approval")
    return claim


def ready(account: Any) -> bool:
    recipient = (data(account).get("configuration") or {}).get("recipient") or {}
    balance = (recipient.get("capabilities") or {}).get("stripe_balance") or {}
    return all((balance.get(name) or {}).get("status") == "active" for name in ("stripe_transfers", "payouts"))


def onboarding(engine: Engine, settings: Settings, ctx: FirmContext, claim_id: UUID) -> str:
    ledger = DbPayoutLedger(engine)
    with firm_lock(engine, ctx.firm_id):
        with tenant_session(ctx, engine) as session:
            row = session.scalar(select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.claim_id == claim_id))
            if row is None:
                raise PaymentError("there is no approved refund to pay out", 409)
            payout, _ = lock_claim_and_payout(session, ctx, row.id)
            if payout.status not in ("pending", "onboarding", "failed"):
                raise PaymentError(f"the payout is already {payout.status}", 409)
            claim = payable(session, ctx, payout)
            email = (claim.claimant or {}).get("email")
            if not isinstance(email, str) or not email.strip():
                raise PaymentError("claimant email is required for Stripe onboarding", 400)
            if not PlanBilling(engine, settings, session).check_entitlement(ctx, "create_payout").allowed:
                raise PaymentError("the current plan pays this refund by check export", 402)
            sdk = connect_client(settings)
            payout_id, account_id = payout.id, payout.connect_account_id
            started = payout.connect_started_at
            if started is None:
                started = payout.connect_started_at = datetime.now(UTC)
        if not account_id:
            if datetime.now(UTC) - started >= SAFE_WINDOW:
                matches = [
                    a
                    for a in sdk.accounts.list({"limit": 100}).auto_paging_iter()
                    if data(a).get("metadata", {}).get("payout_id") == str(payout_id)
                    and data(a).get("metadata", {}).get("firm_id") == str(ctx.firm_id)
                ]
                if len(matches) != 1:
                    raise PaymentError("Connect account creation needs reconciliation")
                account = matches[0]
            else:
                account = sdk.accounts.create(
                    {
                        "dashboard": "express",
                        "contact_email": email,
                        "identity": {"country": "US"},
                        "defaults": {
                            "responsibilities": {"fees_collector": "application", "losses_collector": "application"}
                        },
                        "configuration": {
                            "recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}}
                        },
                        "include": ["configuration.recipient"],
                        "metadata": {"firm_id": str(ctx.firm_id), "payout_id": str(payout_id)},
                    },
                    options={"idempotency_key": f"connect-v2-account:{payout_id}"},
                )
            test_object(account)
            account_id = str(account.id)
            with tenant_session(ctx, engine) as session:
                p, _ = lock_claim_and_payout(session, ctx, payout_id)
                # Store identity even if a hold/check choice landed while Stripe created the account.
                p.connect_account_id = account_id
                if p.status in ("pending", "failed"):
                    ledger.mark_onboarding(ctx, payout_id, account_id, session=session)
                else:
                    session.add(
                        AuditLog(
                            firm_id=ctx.firm_id,
                            actor=ctx.user_id,
                            action="connect_account_linked",
                            target_type="payout",
                            target_id=str(payout_id),
                            before=None,
                            after={"account": account_id},
                        )
                    )
                allowed = p.status == "onboarding" and not p.hold_requested
            if not allowed:
                raise PaymentError("the payout changed while onboarding was being created", 409)
        with tenant_session(ctx, engine) as session:
            p, _ = lock_claim_and_payout(session, ctx, payout_id)
            payable(session, ctx, p)
            if p.status not in ("onboarding", "failed"):
                raise PaymentError("the payout no longer accepts onboarding", 409)
        link = sdk.account_links.create(
            {
                "account": account_id,
                "use_case": {
                    "type": "account_onboarding",
                    "account_onboarding": {
                        "return_url": settings.connect_return_url,
                        "refresh_url": settings.connect_refresh_url,
                    },
                },
            }
        )
        return str(link.url)


def apply_transfer(session: Session, ctx: FirmContext, obj: Any, *, reversed_event: bool = False) -> None:
    test_object(obj)
    metadata = data(obj).get("metadata", {})
    try:
        payout_id = UUID(metadata.get("payout_id", ""))
    except (ValueError, TypeError) as exc:
        raise PaymentError("transfer is missing its payout identity", 400) from exc
    p, _ = lock_claim_and_payout(session, ctx, payout_id)
    claim = session.get(Claim, p.claim_id)
    if (
        claim is None
        or metadata.get("firm_id") != str(ctx.firm_id)
        or object_id(data(obj).get("destination")) != p.connect_account_id
        or data(obj).get("amount") != p.amount_cents
        or data(obj).get("currency") != p.currency
        or data(obj).get("transfer_group") != str(claim.program_id)
        or p.transfer_request is None
    ):
        raise PaymentError("transfer does not match the committed payout request", 400)
    DbPayoutLedger(session.get_bind()).mark_transferred(ctx, p.id, str(obj.id), session=session)  # type: ignore[arg-type]
    reversed_cents = int(data(obj).get("amount_reversed", 0))
    if reversed_cents < 0 or reversed_cents > p.amount_cents:
        raise PaymentError("invalid reversed transfer amount", 400)
    if reversed_event and reversed_cents > p.stripe_reversed_cents:
        before = p.stripe_reversed_cents
        p.stripe_reversed_cents = reversed_cents
        session.add(
            AuditLog(
                firm_id=ctx.firm_id,
                actor=ctx.user_id,
                action="payout_transfer_reversed",
                target_type="payout",
                target_id=str(p.id),
                before={"reversed_cents": before},
                after={"reversed_cents": reversed_cents, "transfer_id": str(obj.id), "requires_review": True},
            )
        )
        log_event(log, "payout_transfer_reversed", level=40, firm_id=str(ctx.firm_id), payout_id=str(p.id))


def account_updated(session: Session, ctx: FirmContext, obj: Any) -> None:
    test_object(obj)
    ids = session.scalars(
        select(Payout.id).where(Payout.firm_id == ctx.firm_id, Payout.connect_account_id == obj.id)
    ).all()
    if len(ids) != 1:
        raise PaymentError("Connect account linkage is missing or ambiguous")
    p, _ = lock_claim_and_payout(session, ctx, ids[0])
    if data(obj).get("metadata", {}).get("payout_id") != str(p.id):
        raise PaymentError("Connect account metadata mismatch", 400)
    value = ready(obj)
    if value != p.connect_ready:
        session.add(
            AuditLog(
                firm_id=ctx.firm_id,
                actor=ctx.user_id,
                action="connect_readiness_changed",
                target_type="payout",
                target_id=str(p.id),
                before={"ready": p.connect_ready},
                after={"ready": value},
            )
        )
        p.connect_ready = value


def note_unresolved(engine: Engine, ctx: FirmContext, payout_id: UUID) -> str:
    with tenant_session(ctx, engine) as session:
        p, _ = lock_claim_and_payout(session, ctx, payout_id)
        reason = "Stripe transfer outcome unknown; reconcile before retry or export"
        if p.last_error != reason:
            p.last_error = reason
            session.add(
                AuditLog(
                    firm_id=ctx.firm_id,
                    actor=ctx.user_id,
                    action="payout_outcome_unknown",
                    target_type="payout",
                    target_id=str(p.id),
                    before=None,
                    after={"requires_review": True},
                )
            )
            log_event(log, "transfer_outcome_unknown", level=40, firm_id=str(ctx.firm_id), payout_id=str(p.id))
    return "unresolved"


def process_payout(engine: Engine, settings: Settings, ctx: FirmContext, payout_id: UUID) -> str:
    sdk, ledger = client(settings), DbPayoutLedger(engine)
    with firm_lock(engine, ctx.firm_id):
        with tenant_session(ctx, engine) as session:
            p, _ = lock_claim_and_payout(session, ctx, payout_id)
            if p.status in ("transferred", "exported", "pending_export", "held"):
                return str(p.status)
            account_id, status = p.connect_account_id, p.status
            if not account_id:
                return "awaiting_onboarding"
            if p.transfer_retry_at and p.transfer_retry_at > datetime.now(UTC):
                return "backoff"
            original, started = p.transfer_request, p.transfer_started_at
            claim = session.get(Claim, p.claim_id)
            assert claim is not None
            program_id = claim.program_id
        if status == "transferring":
            matches = [
                t
                for t in sdk.transfers.list(
                    {"destination": account_id, "transfer_group": str(program_id), "limit": 100}
                ).auto_paging_iter()
                if data(t).get("metadata", {}).get("payout_id") == str(payout_id)
            ]
            if len(matches) > 1:
                raise PaymentError("multiple Stripe transfers match one payout; operator review required")
            if matches:
                with tenant_session(ctx, engine) as session:
                    apply_transfer(session, ctx, sdk.transfers.retrieve(matches[0].id), reversed_event=True)
                return "reconciled"
            if not started or not original or datetime.now(UTC) - started >= SAFE_WINDOW:
                return note_unresolved(engine, ctx, payout_id)
            with tenant_session(ctx, engine) as session:
                p, claim_status = lock_claim_and_payout(session, ctx, payout_id)
                replay_allowed = (
                    claim_status == "approved"
                    and not p.hold_requested
                    and PlanBilling(engine, settings, session).check_entitlement(ctx, "create_payout").allowed
                )
            if not replay_allowed:
                return note_unresolved(engine, ctx, payout_id)
        else:
            account = connect_client(settings).accounts.retrieve(account_id, {"include": ["configuration.recipient"]})
            test_object(account)
            if not ready(account):
                return "awaiting_onboarding"
            with tenant_session(ctx, engine) as session:
                p, _ = lock_claim_and_payout(session, ctx, payout_id)
                claim = payable(session, ctx, p)
                if not PlanBilling(engine, settings, session).check_entitlement(ctx, "create_payout").allowed:
                    return "plan_restricted"
                ledger.claim_for_transfer(ctx, payout_id, session=session)
                if original is None:
                    original = {
                        "amount": p.amount_cents,
                        "currency": p.currency,
                        "destination": account_id,
                        "transfer_group": str(claim.program_id),
                        "metadata": {"payout_id": str(p.id), "firm_id": str(ctx.firm_id)},
                    }
                    p.transfer_request = original
                # The first dispatch anchors the replay window. A retry after a 429 (which Stripe never executes)
                # keeps it, so the window can only be shorter than the idempotency key's life, and the trigger's
                # "an unknown transfer retains its first request time" rule holds (backend review of PR 6).
                if p.transfer_started_at is None:
                    p.transfer_started_at = datetime.now(UTC)
                p.transfer_retry_at = None
        try:
            result = sdk.transfers.create(original, options={"idempotency_key": str(payout_id)})
        except stripe.StripeError as exc:
            http_status = getattr(exc, "http_status", None)
            if not confirmed_rejection(exc):
                # Unknown outcome (a network error, a 5xx, a 409, or a response the SDK could not read, which can
                # carry any status, even 200): the transfer may exist. It stays transferring for reconciliation and
                # never reaches a check export.
                return note_unresolved(engine, ctx, payout_id)
            if http_status in (401, 403):
                raise PaymentError("Stripe transfer credentials need operator attention") from exc
            with tenant_session(ctx, engine) as session:
                # Program money lock first, then claim, then payout: the order the portal's check choice uses.
                lock_program_money(session, program_id)
                p, _ = lock_claim_and_payout(session, ctx, payout_id)
                if p.status != "transferring":
                    return str(p.status)
                code = str(getattr(exc, "code", None) or "stripe_rejected")
                ledger.mark_failed(ctx, payout_id, code, session=session)
                if p.status == "failed":
                    if isinstance(exc, stripe.RateLimitError) or http_status == 429:
                        p.transfer_retry_at = datetime.now(UTC) + timedelta(minutes=1 if p.attempts == 1 else 5)
                    else:
                        ledger.to_export(ctx, payout_id, session=session)
                if p.status == "pending_export":
                    # The refund became a mailed check: the same shared-destination hold as a claimant's choice.
                    from recallops.portal import hold_shared_destination

                    claim_row = session.get(Claim, p.claim_id)
                    if claim_row is not None and hold_shared_destination(
                        session, ctx, claim_row.id, claim_row.program_id, dict(claim_row.claimant or {})
                    ):
                        session.refresh(p)
                return str(p.status)
        with tenant_session(ctx, engine) as session:
            apply_transfer(session, ctx, result, reversed_event=True)
        return "transferred"


def confirmed_rejection(exc: stripe.StripeError) -> bool:
    """True only when Stripe answered with a parsed error object and a 4xx other than 409: the request was not
    executed. Everything else is an unknown outcome: a connection or idempotency error, a 5xx, a 409, and an SDK
    APIError for a body it could not read (stripe 16.0.0 raises that with the real status, 200 included)."""
    if isinstance(exc, (stripe.APIConnectionError, stripe.IdempotencyError)):
        return False
    http_status = getattr(exc, "http_status", None)
    if not isinstance(http_status, int) or not 400 <= http_status < 500 or http_status == 409:
        return False
    return getattr(exc, "error", None) is not None


def tick(engine: Engine, settings: Settings) -> int:
    # Tenant enumeration belongs to this standalone job, never an API request path.
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT firm_id, id FROM payouts WHERE method = 'stripe_connect' "
                "AND status IN ('onboarding', 'failed', 'transferring') ORDER BY created_at"
            )
        ).all()
    attention = 0
    for firm_id, payout_id in rows:
        try:
            result = process_payout(engine, settings, FirmContext(firm_id, "connect-job", "admin"), payout_id)
            if result in ("unresolved", "plan_restricted"):
                attention += 1
        except Exception as exc:  # noqa: BLE001 - one payout's failure must not stop every later payout of every firm
            attention += 1
            log_event(
                log,
                "connect_job_refused",
                level=30,
                firm_id=str(firm_id),
                payout_id=str(payout_id),
                error=type(exc).__name__,
            )
    return attention


def main() -> None:
    from recallops.db.engine import internal_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure(settings.log_level)
    while True:
        attention = tick(internal_engine(), settings)
        if not args.loop:
            raise SystemExit(1 if attention else 0)
        time.sleep(settings.payment_poll_s)


if __name__ == "__main__":
    main()
