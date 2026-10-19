"""The payments seam for money out (invariant 9: ledger first). The backend calls this protocol when a claim is
approved; component 3's stripe_connect.py claims a row with claim_for_transfer immediately before Transfer.create and
calls the mark_* methods as Stripe events arrive. DbPayoutLedger (payouts/db_ledger.py) implements it on the payouts
table with legal transitions only; FakeLedger (payouts/fake.py) keeps state in memory for unit tests.

States (decisions log 2026-10-18): pending and pending_export wait for a payout path; onboarding waits for the
claimant's Connect account; transferring is claimed by component 3 for one Stripe transfer; held means the claim left
approved (reviewer override, a duplicate found later) before money moved; transferred and exported are terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from recallops.tenancy import FirmContext

PayoutStatus = Literal[
    "pending", "pending_export", "onboarding", "transferring", "transferred", "failed", "exported", "held"
]


@dataclass(frozen=True)
class PayoutRef:
    payout_id: UUID
    claim_id: UUID
    amount_cents: int
    status: PayoutStatus


class PayoutLedger(Protocol):
    def create_pending(
        self,
        ctx: FirmContext,
        claim_id: UUID,
        amount_cents: int,
        method: str = "stripe_connect",
        session: Any = None,
    ) -> PayoutRef:
        """Called on approval, before any Stripe call, inside the approval's own transaction when session is given.
        Idempotent per claim; a held row for a re-approved claim is released. The payout id is the transfer's
        idempotency key. method check_export starts the row in pending_export (the export batch pays it)."""
        ...

    def mark_onboarding(self, ctx: FirmContext, payout_id: UUID, connect_account_id: str) -> PayoutRef: ...

    def claim_for_transfer(self, ctx: FirmContext, payout_id: UUID) -> PayoutRef:
        """Component 3 calls this under the row lock immediately before stripe.Transfer.create. Refuses unless the
        claim is approved and the payout is pending, onboarding, or failed (never held)."""
        ...

    def mark_transferred(self, ctx: FirmContext, payout_id: UUID, stripe_transfer_id: str) -> PayoutRef: ...

    def mark_failed(self, ctx: FirmContext, payout_id: UUID, error: str) -> PayoutRef: ...

    def mark_exported(self, ctx: FirmContext, payout_id: UUID, export_batch: str) -> PayoutRef: ...
