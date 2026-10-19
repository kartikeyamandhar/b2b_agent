"""The payouts ledger on the payouts table (invariant 9: ledger first; 03-payments.md section 5).

A payouts row exists before any Stripe call; its id is the transfer's idempotency key. create_pending is idempotent
per claim (payouts.claim_id is unique), so a re-executed graph node or a replayed approval never creates a second
row. Every transition is checked against LEGAL and writes audit_log (03-payments.md section 6). The payments owner's
stripe_connect.py calls the mark_* methods as Stripe events arrive; the export job calls mark_exported.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Payout
from recallops.payouts.ledger import PayoutRef, PayoutStatus
from recallops.tenancy import FirmContext

log = get_logger("payouts.ledger")

PayoutMethod = str  # stripe_connect | check_export | ach_export

# from-status -> allowed to-statuses
LEGAL: dict[str, frozenset[str]] = {
    "pending": frozenset({"onboarding", "transferred", "failed", "pending_export"}),
    "onboarding": frozenset({"transferred", "failed", "pending_export"}),
    "failed": frozenset({"pending", "onboarding", "transferred", "pending_export"}),
    "pending_export": frozenset({"exported"}),
    "transferred": frozenset(),
    "exported": frozenset(),
}
MAX_TRANSFER_ATTEMPTS = 3


class IllegalTransition(ValueError):
    pass


class PayoutNotFound(LookupError):
    pass


@dataclass(frozen=True)
class PayoutRow:
    ref: PayoutRef
    method: str
    attempts: int
    last_error: str | None


def _ref(p: Payout) -> PayoutRef:
    return PayoutRef(payout_id=p.id, claim_id=p.claim_id, amount_cents=p.amount_cents, status=p.status)  # type: ignore[arg-type]


class DbPayoutLedger:
    def __init__(
        self,
        engine: Engine,
        session_for: Callable[[FirmContext, Engine], AbstractContextManager[Session]] = tenant_session,
    ) -> None:
        self.engine = engine
        self.session_for = session_for

    def create_pending(
        self, ctx: FirmContext, claim_id: UUID, amount_cents: int, method: PayoutMethod = "stripe_connect"
    ) -> PayoutRef:
        status = "pending_export" if method in ("check_export", "ach_export") else "pending"
        with self.session_for(ctx, self.engine) as session:
            inserted = session.execute(
                insert(Payout)
                .values(firm_id=ctx.firm_id, claim_id=claim_id, amount_cents=amount_cents, method=method, status=status)
                .on_conflict_do_nothing(index_elements=["claim_id"])
                .returning(Payout.id)
            ).first()
            row = session.scalars(
                select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.claim_id == claim_id)
            ).one()
            if inserted is not None:
                session.add(
                    AuditLog(
                        firm_id=ctx.firm_id,
                        actor=ctx.user_id,
                        action="payout_created",
                        target_type="payout",
                        target_id=str(row.id),
                        before=None,
                        after={
                            "status": status,
                            "amount_cents": amount_cents,
                            "method": method,
                            "claim_id": str(claim_id),
                        },
                    )
                )
            ref = _ref(row)
        log_event(
            log,
            "payout_created_pending" if inserted is not None else "payout_exists",
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
            p = session.scalars(
                select(Payout).where(Payout.firm_id == ctx.firm_id, Payout.id == payout_id).with_for_update()
            ).first()
            if p is None:
                raise PayoutNotFound(str(payout_id))
            if p.status == to and to in ("transferred", "exported"):
                return _ref(p)  # replayed terminal event: no-op
            if to not in LEGAL[p.status]:
                raise IllegalTransition(f"payout {payout_id}: {p.status} -> {to} is not allowed")
            before = {"status": p.status, "attempts": p.attempts}
            session.execute(
                update(Payout)
                .where(Payout.id == payout_id, Payout.firm_id == ctx.firm_id)
                .values(status=to, updated_at=datetime.now(UTC), **values)
            )
            session.add(
                AuditLog(
                    firm_id=ctx.firm_id,
                    actor=ctx.user_id,
                    action=f"payout_{to}",
                    target_type="payout",
                    target_id=str(payout_id),
                    before=before,
                    after={"status": to, **{k: str(v) for k, v in values.items()}},
                )
            )
            session.flush()
            session.refresh(p)
            ref = _ref(p)
        log_event(log, "payout_state_changed", firm_id=str(ctx.firm_id), payout_id=str(payout_id), status=to)
        return ref

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef:
        return self._move(ctx, payout_id, "onboarding", last_error=None)

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef:
        return self._move(ctx, payout_id, "transferred", stripe_transfer_id=stripe_transfer_id, last_error=None)

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef:
        """After MAX_TRANSFER_ATTEMPTS failures the row flips to pending_export (the export file is the fallback)."""
        row = self.get(ctx, payout_id)
        attempts = row.attempts + 1
        if attempts >= MAX_TRANSFER_ATTEMPTS:
            self._move(ctx, payout_id, "failed", attempts=attempts, last_error=error)
            return self._move(ctx, payout_id, "pending_export", method="check_export")
        return self._move(ctx, payout_id, "failed", attempts=attempts, last_error=error)

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef:
        return self._move(ctx, payout_id, "exported")

    def to_export(self, ctx: FirmContext, payout_id: UUID) -> PayoutRef:
        """Claimant chose a check (portal payout step) or Connect is unavailable: the export file pays it."""
        return self._move(ctx, payout_id, "pending_export", method="check_export")
