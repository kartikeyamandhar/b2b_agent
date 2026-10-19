"""Database effects of an adjudication run: what route and human_review write.

Both methods are idempotent and fenced, because LangGraph re-executes a whole node after a crash or a resume
(red-team finding, decisions log 2026-10-18): the claim update only applies from the expected prior status (and,
for the worker's own run, only while this worker holds the claim's lease); a second call finds nothing to update
and writes nothing. Payout creation is idempotent per claim in the ledger.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text, update

from recallops.billing.interface import BillingGate
from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim
from recallops.payouts.ledger import PayoutLedger
from recallops.tenancy import FirmContext

log = get_logger("claims.effects")

STATUS = {"approve": "approved", "deny": "denied", "needs_info": "needs_info"}
HUMAN_PRIOR = ("flagged", "under_review", "capped")


@dataclass
class DbEffects:
    engine: Engine
    ctx: FirmContext
    claim_id: UUID
    refund_cents: int | None
    ledger: PayoutLedger
    billing: BillingGate
    lease_owner: str | None = None  # set for the worker's adjudication run; None for a reviewer resume

    def _prior(self) -> tuple[str, ...]:
        return ("submitted",) if self.lease_owner else HUMAN_PRIOR

    def apply(self, final: dict[str, Any]) -> None:
        self.apply_from(final, self._prior())

    def apply_from(self, final: dict[str, Any], prior: tuple[str, ...]) -> None:
        """Apply only when the claim is in one of the prior statuses (the fence that makes replays no-ops)."""
        outcome = final["outcome"]
        if outcome not in STATUS:
            raise ValueError(f"cannot apply outcome {outcome!r} as final")
        status = STATUS[outcome]
        decision = {
            "outcome": outcome,
            "reasons": final.get("reasons", []),
            "cited_rules": final.get("cited_rules", []),
            "confidence": final.get("confidence"),
            "question_for_claimant": final.get("question_for_claimant"),
            "routed_to_human": final.get("review_action") is not None,
            "review_action": final.get("review_action"),
            "agent_outcome": final.get("agent_outcome"),
        }
        with tenant_session(self.ctx, self.engine) as session:
            stmt = update(Claim).where(
                Claim.id == self.claim_id, Claim.firm_id == self.ctx.firm_id, Claim.status.in_(prior)
            )
            if self.lease_owner:
                stmt = stmt.where(Claim.lease_owner == self.lease_owner)
            before = session.execute(
                text("SELECT status FROM claims WHERE id = :c AND firm_id = :f"),
                {"c": self.claim_id, "f": self.ctx.firm_id},
            ).scalar_one_or_none()
            done = session.execute(
                stmt.values(
                    status=status, decision=decision, decided_at=datetime.now(UTC), decided_by=final["decided_by"]
                ).returning(Claim.id)
            ).first()
            if done is None:
                log_event(log, "claim_apply_skipped", claim_id=str(self.claim_id), status=before, outcome=outcome)
                return
            question = final.get("question_for_claimant")
            if outcome == "needs_info" and question:
                message = {"from": "firm", "text": question, "at": datetime.now(UTC).isoformat()}
                session.execute(
                    text("UPDATE claims SET messages = messages || CAST(:m AS jsonb) WHERE id = :c"),
                    {"m": json.dumps([message]), "c": self.claim_id},
                )
            session.add(
                AuditLog(
                    firm_id=self.ctx.firm_id,
                    actor=str(final["decided_by"]),
                    action="claim_reviewed" if final.get("review_action") else "claim_adjudicated",
                    target_type="claim",
                    target_id=str(self.claim_id),
                    before={"status": before},
                    after={"status": status, "outcome": outcome, "review_action": final.get("review_action")},
                )
            )
        log_event(log, "claim_decided", firm_id=str(self.ctx.firm_id), claim_id=str(self.claim_id), outcome=outcome)
        if outcome == "approve" and self.refund_cents:
            entitlement = self.billing.check_entitlement(self.ctx, "create_payout")
            method = "stripe_connect" if entitlement.allowed else "check_export"
            self.ledger.create_pending(self.ctx, self.claim_id, self.refund_cents, method)

    def hold(self, held: dict[str, Any]) -> None:
        decision = held.get("decision")
        stored = None
        if decision:
            stored = {**decision, "routed_to_human": True, "hold_reason": held["reason"]}
        with tenant_session(self.ctx, self.engine) as session:
            stmt = update(Claim).where(
                Claim.id == self.claim_id, Claim.firm_id == self.ctx.firm_id, Claim.status == "submitted"
            )
            if self.lease_owner:
                stmt = stmt.where(Claim.lease_owner == self.lease_owner)
            done = session.execute(
                stmt.values(status=held["claim_status"], decision=stored).returning(Claim.id)
            ).first()
            if done is None:
                log_event(log, "claim_hold_skipped", claim_id=str(self.claim_id), reason=held["reason"])
                return
            session.add(
                AuditLog(
                    firm_id=self.ctx.firm_id,
                    actor="agent",
                    action="claim_held",
                    target_type="claim",
                    target_id=str(self.claim_id),
                    before={"status": "submitted"},
                    after={"status": held["claim_status"], "reason": held["reason"], "detail": held.get("detail")},
                )
            )
        log_event(log, "claim_held_for_review", claim_id=str(self.claim_id), reason=held["reason"])
