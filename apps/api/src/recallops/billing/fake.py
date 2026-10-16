"""AllowAllBilling: always allows and logs. Replaced by the payments owner's plan-aware implementation."""

from __future__ import annotations

from decimal import Decimal

from recallops.billing.interface import Action, Entitlement
from recallops.logs import get_logger, log_event
from recallops.tenancy import FirmContext

log = get_logger("billing.fake")


class AllowAllBilling:
    def __init__(self) -> None:
        self.usage: list[tuple[str, int, Decimal]] = []

    def check_entitlement(self, ctx: FirmContext, action: Action) -> Entitlement:
        log_event(log, "entitlement_checked", firm_id=str(ctx.firm_id), action=action, allowed=True)
        return Entitlement(allowed=True, reason="fake billing allows everything", plan="free")

    def record_usage(self, ctx: FirmContext, claims_adjudicated: int, model_cost_usd: Decimal) -> None:
        self.usage.append((str(ctx.firm_id), claims_adjudicated, model_cost_usd))
        log_event(
            log,
            "usage_recorded",
            firm_id=str(ctx.firm_id),
            claims_adjudicated=claims_adjudicated,
            model_cost_usd=str(model_cost_usd),
        )
