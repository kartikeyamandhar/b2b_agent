"""Reviewer actions (01-backend.md section 6): resume_override, request_info, mark_fraud.

When the claim's latest run is an agent run paused for a person, the action is stored as the run's resume_payload
and the worker resumes the same checkpointed run (invariant 6: resuming is a resume, not a new run). Only one
review can be pending per run. Claims decided by the rules-only baseline (the seeded queue) have no checkpoint, so
their override is applied directly. Every action writes audit_log.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from recallops.billing.interface import BillingGate
from recallops.claims.effects import DbEffects
from recallops.models import AgentRun, AuditLog, Claim
from recallops.payouts.ledger import PayoutLedger
from recallops.tenancy import FirmContext

WAITING_RUN = ("waiting_human", "capped", "failed")
REVIEWABLE = ("flagged", "under_review", "capped")
DIRECT_REVIEWABLE = (*REVIEWABLE, "needs_info", "approved", "denied")


class ReviewConflict(ValueError):
    pass


def submit_action(
    session: Session,
    engine: Engine,
    ctx: FirmContext,
    claim: Claim,
    action: str,
    outcome: str | None,
    message: str | None,
    ledger: PayoutLedger,
    billing: BillingGate,
) -> str:
    """Returns "queued" (the worker resumes the run) or "applied" (decided directly)."""
    if action == "resume_override" and outcome is None:
        raise ValueError("resume_override needs an outcome")
    if action == "request_info" and not (message or "").strip():
        raise ValueError("request_info needs a message for the claimant")
    payload: dict[str, Any] = {"action": action, "outcome": outcome, "message": message, "actor": ctx.user_id}
    run = session.scalars(
        select(AgentRun)
        .where(AgentRun.firm_id == ctx.firm_id, AgentRun.claim_id == claim.id)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    ).first()
    if run is not None and run.graph == "adjudicate" and run.status in WAITING_RUN and claim.status in REVIEWABLE:
        done = session.execute(
            text(
                "UPDATE agent_runs SET resume_payload = CAST(:p AS jsonb) WHERE id = :r AND firm_id = :f "
                "AND resume_payload IS NULL AND status IN ('waiting_human', 'capped', 'failed') RETURNING id"
            ),
            {"p": _json(payload), "r": run.id, "f": ctx.firm_id},
        ).first()
        if done is None:
            raise ReviewConflict("a review is already pending for this claim")
        _audit(session, ctx, claim.id, "review_submitted", claim.status, payload)
        return "queued"
    if run is not None and run.graph == "adjudicate" and run.status == "running":
        raise ReviewConflict("the claim is being adjudicated; try again when the run finishes")
    if claim.status not in DIRECT_REVIEWABLE:
        raise ReviewConflict(f"a claim in status {claim.status} cannot be reviewed")
    from recallops.claims.graph import _final_from_review

    final = _final_from_review(payload, {"decision": claim.decision or {}})
    effects = DbEffects(engine, ctx, claim.id, claim.refund_cents, ledger, billing, lease_owner=None)
    effects.apply_from(final, DIRECT_REVIEWABLE)
    return "applied"


def _json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload)


def _audit(
    session: Session, ctx: FirmContext, claim_id: UUID, action: str, before: str, payload: dict[str, Any]
) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="claim",
            target_id=str(claim_id),
            before={"status": before},
            after={"review": payload},
        )
    )
