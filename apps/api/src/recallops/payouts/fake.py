"""FakeLedger: allows every transition, keeps state in memory, and logs each call. M1 and M2 only."""

from __future__ import annotations

import uuid
from dataclasses import replace
from uuid import UUID

from recallops.logs import get_logger, log_event
from recallops.payouts.ledger import PayoutRef, PayoutStatus
from recallops.tenancy import FirmContext

log = get_logger("payouts.fake")


class FakeLedger:
    def __init__(self) -> None:
        self.rows: dict[UUID, PayoutRef] = {}

    def _set(self, ctx: FirmContext, payout_id: UUID, status: PayoutStatus, **detail: str) -> PayoutRef:
        ref = replace(self.rows[payout_id], status=status)
        self.rows[payout_id] = ref
        log_event(
            log,
            "payout_state_changed",
            firm_id=str(ctx.firm_id),
            claim_id=str(ref.claim_id),
            payout_id=str(payout_id),
            status=status,
            detail=detail,
        )
        return ref

    def create_pending(self, ctx: FirmContext, claim_id: UUID, amount_cents: int) -> PayoutRef:
        ref = PayoutRef(payout_id=uuid.uuid4(), claim_id=claim_id, amount_cents=amount_cents, status="pending")
        self.rows[ref.payout_id] = ref
        log_event(
            log,
            "payout_created_pending",
            firm_id=str(ctx.firm_id),
            claim_id=str(claim_id),
            payout_id=str(ref.payout_id),
            amount_cents=amount_cents,
        )
        return ref

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef:
        return self._set(ctx, payout_id, "onboarding", connect_account_id=connect_account_id)

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef:
        return self._set(ctx, payout_id, "transferred", stripe_transfer_id=stripe_transfer_id)

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef:
        return self._set(ctx, payout_id, "failed", error=error)

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef:
        return self._set(ctx, payout_id, "exported", export_batch=export_batch)
