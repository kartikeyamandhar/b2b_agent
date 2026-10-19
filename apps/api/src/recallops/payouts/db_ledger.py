"""The payouts ledger on the payouts table (invariant 9: ledger first; 03-payments.md section 5).

A payouts row exists before any Stripe call; its id is the transfer's idempotency key. create_pending is idempotent
per claim (payouts.claim_id is unique) and runs inside the approval's transaction, so a claim is never approved
without its payout row. Every transition is checked against LEGAL and writes audit_log (03-payments.md section 6).

Locking (red team, decisions log 2026-10-18): every money path locks the claim row first and the payout row second,
so the approval path, the reviewer path, the portal payout step, the export batch, and component 3's transfer hooks
cannot deadlock against each other.

Holds: when a claim leaves approved before money moved (a reviewer override, or a duplicate found later), its payout
goes to held. A payout component 3 already claimed for a transfer (transferring) cannot be stopped from here: the hold
is recorded in hold_requested and applied if the transfer fails. A result that arrives for a held payout (a transfer
created just before the hold) is recorded as what happened, with an alert row, never refused: the ledger must match
Stripe. Only a re-approval of the claim releases a hold (create_pending), and never after money moved.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, Payout
from recallops.payouts.ledger import PayoutRef, PayoutStatus
from recallops.tenancy import FirmContext

log = get_logger("payouts.ledger")

PayoutMethod = str  # stripe_connect | check_export | ach_export
EXPORT_METHODS = ("check_export", "ach_export")

# from-status -> allowed to-statuses. held -> transferred | failed are late results only (recorded, alerted); a hold
# is released only by create_pending on re-approval.
LEGAL: dict[str, frozenset[str]] = {
    "pending": frozenset({"onboarding", "transferring", "pending_export", "held"}),
    "onboarding": frozenset({"transferring", "pending_export", "failed", "held"}),
    "transferring": frozenset({"transferred", "failed"}),
    "failed": frozenset({"onboarding", "transferring", "pending_export", "held"}),
    "pending_export": frozenset({"exported", "held"}),
    "held": frozenset({"transferred", "failed"}),
    "transferred": frozenset(),
    "exported": frozenset(),
}
HOLDABLE = ("pending", "pending_export", "onboarding", "failed")
TRANSFERABLE = ("pending", "onboarding", "failed")
DELIVERED = ("transferred", "exported")
MAX_TRANSFER_ATTEMPTS = 3


class IllegalTransition(ValueError):
    pass


class PayoutNotFound(LookupError):
    pass


class NotPayable(ValueError):
    """claim_for_transfer refused: the claim is not approved, or the payout is held, delivered, or already claimed."""


@dataclass(frozen=True)
class PayoutRow:
    ref: PayoutRef
    method: str
    attempts: int
    last_error: str | None


def _ref(p: Payout) -> PayoutRef:
    return PayoutRef(payout_id=p.id, claim_id=p.claim_id, amount_cents=p.amount_cents, status=p.status)  # type: ignore[arg-type]


def _audit(session: Session, ctx: FirmContext, action: str, payout_id: UUID, before: Any, after: Any) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="payout",
            target_id=str(payout_id),
            before=before,
            after=after,
        )
    )


MONEY_LOCK_CLASS = 7_420_019  # pg_advisory_xact_lock(class, hashtext(program_id)); fraud/sync.py uses 7_420_018


def lock_program_money(session: Session, program_id: UUID) -> None:
    """Serialize the transactions that change several claims of one program at once (a decision that reopens
    duplicates, the portal's shared-address hold), so they cannot deadlock on each other's claim rows and the
    shared-address check sees concurrent choices. Taken before any claim row lock; released at commit."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:cls, hashtext(:p))"), {"cls": MONEY_LOCK_CLASS, "p": str(program_id)}
    )


def lock_claim_and_payout(session: Session, ctx: FirmContext, payout_id: UUID) -> tuple[Payout, str]:
    """The global lock order: the claim row, then the payout row. Returns the payout and the claim's status."""
    claim_id = session.scalar(select(Payout.claim_id).where(Payout.firm_id == ctx.firm_id, Payout.id == payout_id))
    if claim_id is None:
        raise PayoutNotFound(str(payout_id))
    status = session.scalar(
        select(Claim.status).where(Claim.firm_id == ctx.firm_id, Claim.id == claim_id).with_for_update()
    )
    payout = session.scalars(
        select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.id == payout_id).with_for_update()
    ).one()
    return payout, str(status)


def transition(session: Session, ctx: FirmContext, p: Payout, to: PayoutStatus, **values: object) -> PayoutRef:
    """Move a locked payout row (the caller holds the claim and payout locks) and write its audit row."""
    if to not in LEGAL[p.status]:
        raise IllegalTransition(f"payout {p.id}: {p.status} -> {to} is not allowed")
    before = {"status": p.status, "attempts": p.attempts}
    session.execute(
        update(Payout)
        .where(Payout.id == p.id, Payout.firm_id == ctx.firm_id)
        .values(status=to, updated_at=datetime.now(UTC), **values)
    )
    _audit(session, ctx, f"payout_{to}", p.id, before, {"status": to, **{k: str(v) for k, v in values.items()}})
    session.flush()
    session.refresh(p)
    log_event(log, "payout_state_changed", firm_id=str(ctx.firm_id), payout_id=str(p.id), status=to)
    return _ref(p)


def mark_claim_paid(session: Session, ctx: FirmContext, claim_id: UUID) -> None:
    """A delivered refund moves an approved claim to paid (same transaction as the delivery)."""
    done = session.execute(
        update(Claim)
        .where(Claim.id == claim_id, Claim.firm_id == ctx.firm_id, Claim.status == "approved")
        .values(status="paid")
        .returning(Claim.id)
    ).first()
    if done is not None:
        session.add(
            AuditLog(
                firm_id=ctx.firm_id,
                actor=ctx.user_id,
                action="claim_paid",
                target_type="claim",
                target_id=str(claim_id),
                before={"status": "approved"},
                after={"status": "paid"},
            )
        )


def hold_for_claim(session: Session, ctx: FirmContext, claim_id: UUID, reason: str) -> str | None:
    """Hold the payout of a claim that left approved. The caller holds the claim row lock (it just updated it).
    Idempotent. Returns the payout's resulting status, or None when the claim has no payout."""
    p = session.scalars(
        select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.claim_id == claim_id).with_for_update()
    ).first()
    if p is None:
        return None
    if p.status in HOLDABLE:
        return transition(session, ctx, p, "held", last_error=reason).status
    if p.status == "transferring" and not p.hold_requested:
        session.execute(update(Payout).where(Payout.id == p.id).values(hold_requested=True))
        _audit(session, ctx, "payout_hold_requested", p.id, {"status": p.status}, {"reason": reason})
        log_event(log, "payout_hold_requested", level=30, firm_id=str(ctx.firm_id), payout_id=str(p.id))
    elif p.status in DELIVERED:
        _audit(session, ctx, "payout_hold_too_late", p.id, {"status": p.status}, {"reason": reason})
        log_event(log, "payout_hold_too_late", level=30, firm_id=str(ctx.firm_id), payout_id=str(p.id))
    return str(p.status)


class DbPayoutLedger:
    def __init__(
        self,
        engine: Engine,
        session_for: Callable[[FirmContext, Engine], AbstractContextManager[Session]] = tenant_session,
    ) -> None:
        self.engine = engine
        self.session_for = session_for

    def create_pending(
        self,
        ctx: FirmContext,
        claim_id: UUID,
        amount_cents: int,
        method: PayoutMethod = "stripe_connect",
        session: Any = None,
    ) -> PayoutRef:
        if session is not None:
            return self._create_in(session, ctx, claim_id, amount_cents, method)
        with self.session_for(ctx, self.engine) as own:
            return self._create_in(own, ctx, claim_id, amount_cents, method)

    def _create_in(
        self, session: Session, ctx: FirmContext, claim_id: UUID, amount_cents: int, method: PayoutMethod
    ) -> PayoutRef:
        session.execute(
            select(Claim.id).where(Claim.firm_id == ctx.firm_id, Claim.id == claim_id).with_for_update()
        ).first()
        status = "pending_export" if method in EXPORT_METHODS else "pending"
        inserted = session.execute(
            insert(Payout)
            .values(firm_id=ctx.firm_id, claim_id=claim_id, amount_cents=amount_cents, method=method, status=status)
            .on_conflict_do_nothing(index_elements=["claim_id"])
            .returning(Payout.id)
        ).first()
        row = session.scalars(
            select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.claim_id == claim_id).with_for_update()
        ).one()
        if inserted is not None:
            _audit(
                session,
                ctx,
                "payout_created",
                row.id,
                None,
                {"status": status, "amount_cents": amount_cents, "method": method, "claim_id": str(claim_id)},
            )
            event = "payout_created_pending"
        elif row.status in DELIVERED:
            mark_claim_paid(session, ctx, claim_id)  # the money already reached the claimant (a late result)
            event = "payout_exists"
        elif row.status == "transferring" and row.hold_requested:
            # A person approved the claim again while its transfer is in flight: the hold no longer applies.
            session.execute(update(Payout).where(Payout.id == row.id).values(hold_requested=False))
            _audit(session, ctx, "payout_hold_cleared", row.id, {"hold_requested": True}, {"hold_requested": False})
            event = "payout_hold_cleared"
        elif row.status == "held" and row.stripe_transfer_id is None:
            # A re-approval releases the hold; the row keeps its id (the idempotency key) and its method.
            released = "pending_export" if row.method in EXPORT_METHODS else "pending"
            session.execute(
                update(Payout)
                .where(Payout.id == row.id)
                .values(status=released, hold_requested=False, last_error=None, updated_at=datetime.now(UTC))
            )
            _audit(session, ctx, "payout_released", row.id, {"status": "held"}, {"status": released})
            session.flush()
            session.refresh(row)
            event = "payout_released"
        else:
            event = "payout_exists"
        ref = _ref(row)
        log_event(
            log,
            event,
            firm_id=str(ctx.firm_id),
            claim_id=str(claim_id),
            payout_id=str(ref.payout_id),
            amount_cents=amount_cents,
            status=ref.status,
        )
        return ref

    def get(self, ctx: FirmContext, payout_id: UUID) -> PayoutRow:
        with self.session_for(ctx, self.engine) as session:
            p = session.scalars(select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.id == payout_id)).first()
            if p is None:
                raise PayoutNotFound(str(payout_id))
            return PayoutRow(ref=_ref(p), method=p.method, attempts=p.attempts, last_error=p.last_error)

    def _move(self, ctx: FirmContext, payout_id: UUID, to: PayoutStatus, **values: object) -> PayoutRef:
        with self.session_for(ctx, self.engine) as session:
            p, _ = lock_claim_and_payout(session, ctx, payout_id)
            return transition(session, ctx, p, to, **values)

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef:
        return self._move(ctx, payout_id, "onboarding", connect_account_id=connect_account_id, last_error=None)

    def claim_for_transfer(self, ctx: FirmContext, payout_id: UUID) -> PayoutRef:
        with self.session_for(ctx, self.engine) as session:
            p, claim_status = lock_claim_and_payout(session, ctx, payout_id)
            if claim_status != "approved" or p.status not in TRANSFERABLE or p.hold_requested:
                raise NotPayable(f"payout {payout_id} is {p.status}; claim is {claim_status}")
            return transition(session, ctx, p, "transferring")

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef:
        with self.session_for(ctx, self.engine) as session:
            p, _ = lock_claim_and_payout(session, ctx, payout_id)
            if p.status == "transferred":
                return _ref(p)  # replayed event: no-op
            late = p.status == "held"
            despite_hold = p.status == "transferring" and p.hold_requested
            ref = transition(session, ctx, p, "transferred", stripe_transfer_id=stripe_transfer_id, last_error=None)
            if late or despite_hold:
                _audit(
                    session,
                    ctx,
                    "payout_paid_despite_hold",
                    p.id,
                    {"status": "held" if late else "transferring"},
                    {"stripe_transfer_id": stripe_transfer_id},
                )
                log_event(log, "payout_paid_despite_hold", level=40, firm_id=str(ctx.firm_id), payout_id=str(p.id))
            mark_claim_paid(session, ctx, p.claim_id)
            return ref

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef:
        """One transaction. A failure on a held row is recorded and the row stays held. A failure with a hold
        requested lands in held. After MAX_TRANSFER_ATTEMPTS failures the row flips to pending_export (the export
        batch is the fallback)."""
        with self.session_for(ctx, self.engine) as session:
            p, claim_status = lock_claim_and_payout(session, ctx, payout_id)
            attempts = p.attempts + 1
            if p.status == "held":
                session.execute(update(Payout).where(Payout.id == p.id).values(attempts=attempts, last_error=error))
                _audit(session, ctx, "payout_failed_while_held", p.id, {"status": "held"}, {"error": error[:300]})
                session.flush()
                session.refresh(p)
                return _ref(p)
            ref = transition(session, ctx, p, "failed", attempts=attempts, last_error=error)
            if p.hold_requested or claim_status != "approved":
                session.execute(update(Payout).where(Payout.id == p.id).values(hold_requested=False))
                return transition(session, ctx, p, "held")
            if attempts >= MAX_TRANSFER_ATTEMPTS:
                return transition(session, ctx, p, "pending_export", method="check_export")
            return ref

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef:
        with self.session_for(ctx, self.engine) as session:
            p, _ = lock_claim_and_payout(session, ctx, payout_id)
            if p.status == "exported":
                return _ref(p)
            ref = transition(session, ctx, p, "exported", export_batch=UUID(export_batch))
            mark_claim_paid(session, ctx, p.claim_id)
            return ref

    def to_export(self, ctx: FirmContext, payout_id: UUID) -> PayoutRef:
        """Claimant chose a check (portal payout step) or Connect is unavailable: the export batch pays it."""
        return self._move(ctx, payout_id, "pending_export", method="check_export")


def held_reason(session: Session, firm_id: UUID, claim_id: UUID) -> str | None:
    """Why a claim's payout is held, for views (the last hold's reason)."""
    return session.execute(
        text("SELECT last_error FROM payouts WHERE firm_id = :f AND claim_id = :c AND status = 'held'"),
        {"f": firm_id, "c": claim_id},
    ).scalar_one_or_none()
