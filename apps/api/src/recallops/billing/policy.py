"""Entitlements are local database decisions; reading firm data never contacts Stripe."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from recallops.billing.interface import Action, Entitlement
from recallops.billing.models import BillingAccount, BillingInvoice, ClaimUsage
from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import Firm, RecallProgram, UsageMeter
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("billing.policy")


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
        from datetime import UTC, datetime

        firm = session.get(Firm, ctx.firm_id)
        if firm is None:
            return Entitlement(False, "firm not found")
        plan = effective_plan(session, firm)
        if plan != "free":
            return Entitlement(True, plan=plan)
        if action == "create_payout":
            return Entitlement(False, "the free plan pays by export", plan)
        if action == "open_program":
            # The calling program transaction takes this lock before reading the quota and writing its result.
            session.execute(select(Firm.id).where(Firm.id == ctx.firm_id).with_for_update())
            n = (
                session.scalar(
                    select(func.count())
                    .select_from(RecallProgram)
                    .where(RecallProgram.firm_id == ctx.firm_id, RecallProgram.status == "open")
                )
                or 0
            )
            allowed = n < self.settings.free_open_programs
        else:
            if claim_id and session.get(ClaimUsage, (ctx.firm_id, claim_id, datetime.now(UTC).date().replace(day=1))):
                return Entitlement(True, plan=plan)
            meter = session.scalar(
                select(UsageMeter).where(
                    UsageMeter.firm_id == ctx.firm_id,
                    UsageMeter.period_start == datetime.now(UTC).date().replace(day=1),
                )
            )
            allowed = (meter.claims_adjudicated if meter else 0) < self.settings.free_claims_per_month
        return Entitlement(allowed, None if allowed else "free plan limit reached", plan)

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
