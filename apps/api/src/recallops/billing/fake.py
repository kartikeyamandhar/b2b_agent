"""Explicit test double for scenarios that exercise backend behavior independently of billing policy."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from recallops.billing.interface import Action, Entitlement
from recallops.logs import get_logger, log_event
from recallops.tenancy import FirmContext

log = get_logger("billing.fake")


class AllowAllBilling:
    def __init__(self) -> None:
        self.usage: list[tuple[str, UUID, UUID, Decimal]] = []

    def check_entitlement(self, ctx: FirmContext, action: Action, claim_id: UUID | None = None) -> Entitlement:
        log_event(log, "entitlement_checked", firm_id=str(ctx.firm_id), action=action, allowed=True)
        return Entitlement(allowed=True, reason="fake billing allows everything", plan="free")

    def record_usage(
        self, ctx: FirmContext, claim_id: UUID, run_id: UUID, model_cost_usd: Decimal, *, session: Any = None
    ) -> None:
        self.usage.append((str(ctx.firm_id), claim_id, run_id, model_cost_usd))
        log_event(
            log,
            "usage_recorded",
            firm_id=str(ctx.firm_id),
            claim_id=str(claim_id),
            run_id=str(run_id),
            model_cost_usd=str(model_cost_usd),
        )
