"""The payments seam for money in (component 3 owns this package). The backend calls check_entitlement before
opening a program, adjudicating a claim, and creating a payout, and record_usage after each adjudication.
Over-limit on the free plan becomes an HTTP 402 problem detail; viewing data is never blocked."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from recallops.tenancy import FirmContext

Action = Literal["open_program", "create_program", "adjudicate_claim", "create_payout"]


@dataclass(frozen=True)
class Entitlement:
    allowed: bool
    reason: str | None = None
    plan: str = "free"


class BillingGate(Protocol):
    def check_entitlement(self, ctx: FirmContext, action: Action, claim_id: UUID | None = None) -> Entitlement: ...

    def record_usage(
        self, ctx: FirmContext, claim_id: UUID, run_id: UUID, model_cost_usd: Decimal, *, session: Any = None
    ) -> None: ...
