"""Claim/month deduplication and per-run cost deltas, committed with monthly projections."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.billing.models import ClaimUsage, RunUsageCost
from recallops.billing.policy import effective_plan
from recallops.models import AgentRun, Firm, UsageMeter
from recallops.tenancy import FirmContext


def record_usage(session: Session, ctx: FirmContext, claim_id: UUID, run_id: UUID, cost: Decimal) -> None:
    run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id, AgentRun.firm_id == ctx.firm_id, AgentRun.claim_id == claim_id)
        .with_for_update()
    )
    firm = session.get(Firm, ctx.firm_id)
    if run is None or firm is None or cost < 0:
        raise ValueError("usage must identify this firm's adjudication run and a nonnegative cost")
    # A resume keeps the original usage month. An interrupted worker can recover a finished result's timestamp.
    at = (run.finished_at or datetime.now(UTC)).astimezone(UTC)
    prior_cost = session.get(RunUsageCost, run_id)
    prior_claim = session.scalar(
        select(ClaimUsage).where(ClaimUsage.firm_id == ctx.firm_id, ClaimUsage.run_id == run_id).limit(1)
    )
    if prior_claim:
        at = prior_claim.counted_at.astimezone(UTC)
    period = prior_cost.period_start if prior_cost else at.date().replace(day=1)
    inserted = session.execute(
        insert(ClaimUsage)
        .values(
            firm_id=ctx.firm_id,
            claim_id=claim_id,
            run_id=run_id,
            period_start=period,
            counted_at=at,
            growth_billable=effective_plan(session, firm) == "growth",
        )
        .on_conflict_do_nothing()
        .returning(ClaimUsage.claim_id)
    ).first()
    previous = prior_cost.recorded_cost if prior_cost else Decimal(0)
    delta = max(cost - previous, Decimal(0))
    if prior_cost:
        prior_cost.recorded_cost = max(cost, previous)
    else:
        session.add(RunUsageCost(firm_id=ctx.firm_id, run_id=run_id, period_start=period, recorded_cost=cost))
    stmt = insert(UsageMeter).values(
        firm_id=ctx.firm_id, period_start=period, claims_adjudicated=int(inserted is not None), model_cost_usd=delta
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["firm_id", "period_start"],
            set_={
                "claims_adjudicated": UsageMeter.claims_adjudicated + int(inserted is not None),
                "model_cost_usd": UsageMeter.model_cost_usd + delta,
            },
        )
    )
