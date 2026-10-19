"""The payments seam for money out (invariant 9: ledger first). The backend calls this protocol when a claim is
approved; component 3's stripe_connect.py calls the mark_* methods as Stripe events arrive. DbPayoutLedger
(payouts/db_ledger.py) implements it on the payouts table with legal transitions only; FakeLedger (payouts/fake.py)
keeps state in memory for unit tests."""

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
    def create_pending(
        self, ctx: FirmContext, claim_id: UUID, amount_cents: int, method: str = "stripe_connect"
    ) -> PayoutRef:
        """Called on approval, before any Stripe call. Idempotent per claim. The payout id is the transfer's
        idempotency key. method check_export starts the row in pending_export (the export file pays it)."""
        ...

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef: ...

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef: ...

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef: ...

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef: ...
