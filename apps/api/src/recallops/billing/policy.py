"""Entitlements are local database decisions; reading firm data never contacts Stripe."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from recallops.billing.interface import Action, Entitlement
from recallops.billing.models import BillingAccount, BillingInvoice
from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import Firm
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("billing.policy")

FREE_PRODUCT_LIMIT = "Free covers one product. Upgrade to Growth to add another."


def active_products(session: Session, firm_id: UUID, statuses: tuple[str, ...] = ("draft", "open")) -> int:
    """The firm's products that count toward its plan: in one of `statuses` and still approved by the operator
    (a revoked product neither counts nor bills)."""
    from recallops.access import approved_program_sql

    return int(
        session.scalar(
            text(
                f"SELECT count(*) FROM recall_programs pr WHERE pr.firm_id = :f AND pr.status = ANY(:s) "
                f"AND {approved_program_sql('pr')}"
            ),
            {"f": firm_id, "s": list(statuses)},
        )
        or 0
    )


def effective_plan(session: Session, firm: Firm) -> str:
    if firm.plan != "growth":
        return str(firm.plan)
    account = session.get(BillingAccount, firm.id)
    if account is None or not account.paid_access:
        return "free"
    restricted = session.scalar(
        select(BillingInvoice.id)
        .where(
            BillingInvoice.firm_id == firm.id,
            BillingInvoice.subscription_id == account.subscription_id,
            BillingInvoice.status.in_(("open", "uncollectible")),
            BillingInvoice.attempt_count >= 2,
        )
        .limit(1)
    )
    if restricted:
        return "free"
    if account.subscription_status == "active" or account.subscription_status == "past_due":
        return "growth"
    return "free"


class PlanBilling:
    def __init__(self, engine: Engine, settings: Settings, session: Session | None = None) -> None:
        self.engine, self.settings, self.session = engine, settings, session

    def check_entitlement(self, ctx: FirmContext, action: Action, claim_id: UUID | None = None) -> Entitlement:
        if self.session is not None:
            result = self._check(self.session, ctx, action, claim_id)
        else:
            with tenant_session(ctx, self.engine) as session:
                result = self._check(session, ctx, action, claim_id)
        log_event(
            log,
            "entitlement_checked",
            firm_id=str(ctx.firm_id),
            claim_id=claim_id,
            action=action,
            allowed=result.allowed,
            plan=result.plan,
        )
        return result

    def _check(self, session: Session, ctx: FirmContext, action: Action, claim_id: UUID | None) -> Entitlement:
        firm = session.get(Firm, ctx.firm_id)
        if firm is None:
            return Entitlement(False, "firm not found")
        plan = effective_plan(session, firm)
        if plan != "free":
            return Entitlement(True, plan=plan)
        # Free (owner decision 2026-10-22): unlimited claims and both refund options; one product at a time.
        if action in ("adjudicate_claim", "create_payout"):
            return Entitlement(True, plan=plan)
        # The calling program transaction takes this lock before reading the quota and writing its result.
        session.execute(select(Firm.id).where(Firm.id == ctx.firm_id).with_for_update())
        statuses = ("open",) if action == "open_program" else ("draft", "open")
        n = active_products(session, ctx.firm_id, statuses)
        allowed = n < self.settings.free_active_products
        return Entitlement(allowed, None if allowed else FREE_PRODUCT_LIMIT, plan)

    def record_usage(
        self,
        ctx: FirmContext,
        claim_id: UUID,
        run_id: UUID,
        model_cost_usd: Decimal,
        *,
        session: Any = None,
    ) -> None:
        from recallops.billing.usage import record_usage

        if session is not None:
            record_usage(session, ctx, claim_id, run_id, model_cost_usd)
        else:
            with tenant_session(ctx, self.engine) as own:
                record_usage(own, ctx, claim_id, run_id, model_cost_usd)
