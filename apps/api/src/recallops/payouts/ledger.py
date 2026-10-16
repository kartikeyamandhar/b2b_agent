"""The payments seam for money out (invariant 9: ledger first). The backend calls this protocol when a claim is
approved; component 3's stripe_connect.py calls the mark_* methods as Stripe events arrive. The M4 state machine
implements it on the payouts table; in M1 and M2 the fake below allows every transition and logs it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from recallops.tenancy import FirmContext

PayoutStatus = Literal["pending", "pending_export", "onboarding", "transferred", "failed", "exported"]


@dataclass(frozen=True)
class PayoutRef:
    payout_id: UUID
    claim_id: UUID
    amount_cents: int
    status: PayoutStatus


class PayoutLedger(Protocol):
    def create_pending(self, ctx: FirmContext, claim_id: UUID, amount_cents: int) -> PayoutRef:
        """Called on approval, before any Stripe call. The payout id is the transfer's idempotency key."""
        ...

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef: ...

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef: ...

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef: ...

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef: ...
