"""TenantScoped repositories. Every query filters by the FirmContext's firm; no method accepts a firm id.
RLS on the same tables is the second guard (CLAUDE.md invariant 2)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from recallops.models import (
    AgentRun,
    AgentStep,
    AuditLog,
    Claim,
    ClaimPhoto,
    Firm,
    FraudCluster,
    Payout,
    RecallProgram,
    UsageMeter,
)
from recallops.tenancy import FirmContext


class TenantRepo:
    def __init__(self, session: Session, ctx: FirmContext) -> None:
        self.session = session
        self.ctx = ctx

    @property
    def firm_id(self) -> UUID:
        return self.ctx.firm_id


class FirmsRepo(TenantRepo):
    def current(self) -> Firm | None:
        return self.session.get(Firm, self.firm_id)


class ProgramsRepo(TenantRepo):
    def list(self) -> list[RecallProgram]:
        stmt = (
            select(RecallProgram).where(RecallProgram.firm_id == self.firm_id).order_by(RecallProgram.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def get(self, program_id: UUID) -> RecallProgram | None:
        stmt = select(RecallProgram).where(RecallProgram.id == program_id, RecallProgram.firm_id == self.firm_id)
        return self.session.scalars(stmt).first()

    def claim_status_counts(self, program_id: UUID) -> dict[str, int]:
        stmt = (
            select(Claim.status, func.count())
            .where(Claim.firm_id == self.firm_id, Claim.program_id == program_id)
            .group_by(Claim.status)
        )
        return {status: int(n) for status, n in self.session.execute(stmt).all()}


class ClaimsRepo(TenantRepo):
    def get(self, claim_id: UUID) -> Claim | None:
        stmt = select(Claim).where(Claim.id == claim_id, Claim.firm_id == self.firm_id)
        return self.session.scalars(stmt).first()

    def list_for_program(
        self,
        program_id: UUID,
        status: str | None = None,
        after: tuple[datetime, UUID] | None = None,
        limit: int = 50,
    ) -> list[Claim]:
        """Keyset pagination on (created_at, id), oldest first (the queue sorts by age)."""
        stmt = select(Claim).where(Claim.firm_id == self.firm_id, Claim.program_id == program_id)
        if status:
            stmt = stmt.where(Claim.status == status)
        if after:
            stmt = stmt.where(tuple_(Claim.created_at, Claim.id) > tuple_(after[0], after[1]))
        stmt = stmt.order_by(Claim.created_at, Claim.id).limit(limit)
        return list(self.session.scalars(stmt))

    def photos(self, claim_id: UUID) -> list[ClaimPhoto]:
        stmt = (
            select(ClaimPhoto)
            .where(ClaimPhoto.firm_id == self.firm_id, ClaimPhoto.claim_id == claim_id)
            .order_by(ClaimPhoto.kind)
        )
        return list(self.session.scalars(stmt))

    def clusters(self, claim_id: UUID) -> list[FraudCluster]:
        stmt = select(FraudCluster).where(
            FraudCluster.firm_id == self.firm_id, FraudCluster.member_claim_ids.contains([claim_id])
        )
        return list(self.session.scalars(stmt))

    def payout(self, claim_id: UUID) -> Payout | None:
        stmt = select(Payout).where(Payout.firm_id == self.firm_id, Payout.claim_id == claim_id)
        return self.session.scalars(stmt).first()

    def audit(self, claim_id: UUID) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.firm_id == self.firm_id,
                AuditLog.target_type == "claim",
                AuditLog.target_id == str(claim_id),
            )
            .order_by(AuditLog.at)
        )
        return list(self.session.scalars(stmt))


class RunsRepo(TenantRepo):
    def latest_for_claim(self, claim_id: UUID) -> AgentRun | None:
        stmt = (
            select(AgentRun)
            .where(AgentRun.firm_id == self.firm_id, AgentRun.claim_id == claim_id)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def steps(self, run_id: UUID) -> list[AgentStep]:
        stmt = (
            select(AgentStep)
            .where(AgentStep.firm_id == self.firm_id, AgentStep.run_id == run_id)
            .order_by(AgentStep.idx)
        )
        return list(self.session.scalars(stmt))


class UsageRepo(TenantRepo):
    def for_period(self, period_start: date) -> UsageMeter | None:
        stmt = select(UsageMeter).where(UsageMeter.firm_id == self.firm_id, UsageMeter.period_start == period_start)
        return self.session.scalars(stmt).first()
