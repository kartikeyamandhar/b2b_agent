"""Database effects of an adjudication run: what route and human_review write.

Both methods are idempotent and fenced, because LangGraph re-executes a whole node after a crash or a resume
(red-team finding, decisions log 2026-10-18): the claim update only applies from the expected prior status (and,
for the worker's own run, only while this worker holds the claim's lease); a second call finds nothing to update
and writes nothing.

Everything a decision changes happens in that one fenced transaction (red team on the completion plan, decisions log
2026-10-18), with the claim row locked before any payout row:
- an approval creates its payout row (invariant 9: a claim is never approved without one);
- a claim that leaves approved holds its payout;
- an automated decision reopens earlier duplicates: check_duplicates (the same frozen tool the adjudicator uses) runs
  over the program's other claims, and a hit partner that is approved with money not yet moved, or waiting in
  needs_info, goes back to submitted with its payout held, so the adjudicator decides it again with the new evidence
  (and flags it through its interrupt, invariant 6). A partner already with a person keeps its status and loses any
  queued, unleased review so the reviewer decides again with the duplicate visible. A reviewer's decision records the
  duplicates it saw (duplicates_ack), and those never reopen it again. Money safety therefore never depends on the
  Neo4j projection.
- an automated re-approval of a claim whose payout is held does not release it: the claim goes to a person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from recallops.billing.interface import BillingGate
from recallops.claims.context import facts_from_rows, program_claims
from recallops.claims.tools import check_duplicates
from recallops.db.session import tenant_session
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, ClaimPhoto, Payout
from recallops.payouts.db_ledger import hold_for_claim, lock_program_money
from recallops.payouts.ledger import PayoutLedger
from recallops.tenancy import FirmContext

log = get_logger("claims.effects")

STATUS = {"approve": "approved", "deny": "denied", "needs_info": "needs_info"}
HUMAN_PRIOR = ("flagged", "under_review", "capped")
AUTOMATED = ("agent", "rules_only")
REOPENABLE = ("approved", "needs_info")
WITH_A_PERSON = ("under_review", "capped")
DUPLICATE_HOLD = "duplicate_found_later"
# Holds set after a decision, outside any LangGraph interrupt: the claim's run has finished, so a reviewer's action
# applies to the claim directly and a crashed run with one of these is done, not waiting (final review 2026-10-19).
POST_DECISION_HOLDS = ("payout_held_needs_review", "shared_mailing_address")


def lock_claim_program(session: Session, firm_id: UUID, claim_id: UUID) -> None:
    program_id = session.execute(
        text("SELECT program_id FROM claims WHERE id = :c AND firm_id = :f"), {"c": claim_id, "f": firm_id}
    ).scalar_one_or_none()
    if program_id is not None:
        lock_program_money(session, program_id)


def _audit(
    session: Session, ctx: FirmContext, actor: str, action: str, claim_id: UUID, before: Any, after: Any
) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=actor,
            action=action,
            target_type="claim",
            target_id=str(claim_id),
            before=before,
            after=after,
        )
    )


def duplicate_partners(session: Session, firm_id: UUID, claim_id: UUID, phash_bits: int) -> list[UUID]:
    """Claims of the same program that check_duplicates links to this claim (sorted, distinct)."""
    claim = session.scalars(select(Claim).where(Claim.firm_id == firm_id, Claim.id == claim_id)).one()
    photos = list(
        session.scalars(select(ClaimPhoto).where(ClaimPhoto.firm_id == firm_id, ClaimPhoto.claim_id == claim_id))
    )
    others = program_claims(session, firm_id, claim.program_id, claim_id)
    hits = check_duplicates(facts_from_rows(claim, photos), others, phash_bits).hits
    return sorted({h.other_claim_id for h in hits})


def reopen_duplicates(
    session: Session, ctx: FirmContext, claim_id: UUID, partners: list[UUID], actor: str
) -> dict[str, list[str]]:
    """Act on the earlier claims a fresh automated decision found duplicates of (see the module docstring)."""
    acted: dict[str, list[str]] = {"reopened": [], "review_voided": [], "after_payment": []}
    for pid in partners:
        row = session.execute(
            text("SELECT status, decided_by, decision FROM claims WHERE id = :c AND firm_id = :f FOR UPDATE"),
            {"c": pid, "f": ctx.firm_id},
        ).first()
        if row is None:
            continue
        decision = row.decision or {}
        if row.decided_by and row.decided_by not in AUTOMATED and str(claim_id) in decision.get("duplicates_ack", []):
            continue  # a reviewer already decided this claim with this duplicate in view
        note = {"duplicate_found_later": sorted({*decision.get("duplicate_found_later", []), str(claim_id)})}
        if row.status in REOPENABLE:
            payout = session.execute(
                text("SELECT status FROM payouts WHERE claim_id = :c AND firm_id = :f"), {"c": pid, "f": ctx.firm_id}
            ).scalar_one_or_none()
            if payout in ("transferred", "exported"):
                acted["after_payment"].append(str(pid))
                _audit(
                    session,
                    ctx,
                    actor,
                    "duplicate_found_after_payment",
                    pid,
                    {"status": row.status},
                    {"with": str(claim_id)},
                )
                continue
            session.execute(  # a run a crash left running for this claim is over: the next run starts fresh
                text(
                    "UPDATE agent_runs SET status = 'done', finished_at = now(), lease_owner = NULL, "
                    "lease_until = NULL, outcome = coalesce(outcome, '{}'::jsonb) || "
                    '\'{"superseded": "reopened"}\'::jsonb '
                    "WHERE claim_id = :c AND firm_id = :f AND graph = 'adjudicate' AND status = 'running'"
                ),
                {"c": pid, "f": ctx.firm_id},
            )
            session.execute(
                text(
                    "UPDATE claims SET status = 'submitted', submitted_at = now(), decided_at = NULL, "
                    "decided_by = NULL, attempts = 0, lease_owner = NULL, lease_until = NULL, "
                    "decision = coalesce(decision, '{}'::jsonb) || CAST(:d AS jsonb) WHERE id = :c AND firm_id = :f"
                ),
                {"d": json.dumps({**note, "reopened_from": row.status}), "c": pid, "f": ctx.firm_id},
            )
            hold_for_claim(session, ctx, pid, DUPLICATE_HOLD)
            _audit(
                session,
                ctx,
                actor,
                "claim_reopened_duplicate",
                pid,
                {"status": row.status},
                {"status": "submitted", "with": str(claim_id)},
            )
            acted["reopened"].append(str(pid))
        elif row.status in WITH_A_PERSON:
            session.execute(
                text(
                    "UPDATE claims SET decision = coalesce(decision, '{}'::jsonb) || CAST(:d AS jsonb) "
                    "WHERE id = :c AND firm_id = :f"
                ),
                {"d": json.dumps(note), "c": pid, "f": ctx.firm_id},
            )
            voided = session.execute(
                text(
                    "UPDATE agent_runs SET resume_payload = NULL WHERE claim_id = :c AND firm_id = :f "
                    "AND resume_payload IS NOT NULL AND status IN ('waiting_human', 'capped', 'failed') "
                    "AND (lease_until IS NULL OR lease_until < now()) RETURNING id"
                ),
                {"c": pid, "f": ctx.firm_id},
            ).all()
            if voided:
                _audit(
                    session,
                    ctx,
                    actor,
                    "review_voided_by_duplicate",
                    pid,
                    {"status": row.status},
                    {"with": str(claim_id)},
                )
                acted["review_voided"].append(str(pid))
        elif row.status == "paid":
            _audit(
                session,
                ctx,
                actor,
                "duplicate_found_after_payment",
                pid,
                {"status": row.status},
                {"with": str(claim_id)},
            )
            acted["after_payment"].append(str(pid))
    if any(acted.values()):
        log_event(
            log,
            "duplicates_acted_on",
            firm_id=str(ctx.firm_id),
            claim_id=str(claim_id),
            **{k: len(v) for k, v in acted.items()},
        )
    return acted


@dataclass
class DbEffects:
    engine: Engine
    ctx: FirmContext
    claim_id: UUID
    refund_cents: int | None
    ledger: PayoutLedger
    billing: BillingGate
    lease_owner: str | None = None  # set for the worker's adjudication run; None for a reviewer resume
    phash_bits: int = 6

    def _prior(self) -> tuple[str, ...]:
        return ("submitted",) if self.lease_owner else HUMAN_PRIOR

    def apply(self, final: dict[str, Any]) -> None:
        self.apply_from(final, self._prior())

    def apply_from(self, final: dict[str, Any], prior: tuple[str, ...]) -> None:
        """Apply only when the claim is in one of the prior statuses (the fence that makes replays no-ops)."""
        outcome = final["outcome"]
        if outcome not in STATUS:
            raise ValueError(f"cannot apply outcome {outcome!r} as final")
        automated = str(final["decided_by"]) in AUTOMATED
        method = "check_export"
        if outcome == "approve" and self.refund_cents:
            # Outside the transaction: component 3's policy may call out, and no row lock is held across it.
            method = (
                "stripe_connect"
                if self.billing.check_entitlement(self.ctx, "create_payout").allowed
                else "check_export"
            )
        decision: dict[str, Any] = {
            "outcome": outcome,
            "reasons": final.get("reasons", []),
            "cited_rules": final.get("cited_rules", []),
            "confidence": final.get("confidence"),
            "question_for_claimant": final.get("question_for_claimant"),
            "routed_to_human": final.get("review_action") is not None,
            "review_action": final.get("review_action"),
            "agent_outcome": final.get("agent_outcome"),
        }
        if final.get("fraud"):
            decision["fraud"] = True
        with tenant_session(self.ctx, self.engine) as session:
            lock_claim_program(session, self.ctx.firm_id, self.claim_id)
            before = session.execute(
                text("SELECT status FROM claims WHERE id = :c AND firm_id = :f FOR UPDATE"),
                {"c": self.claim_id, "f": self.ctx.firm_id},
            ).scalar_one_or_none()
            if before not in prior:
                log_event(log, "claim_apply_skipped", claim_id=str(self.claim_id), status=before, outcome=outcome)
                return
            partners = duplicate_partners(session, self.ctx.firm_id, self.claim_id, self.phash_bits)
            if not automated:
                decision["duplicates_ack"] = [str(p) for p in partners]
            status = STATUS[outcome]
            payout_row = session.execute(
                select(Payout.status, Payout.hold_requested).where(
                    Payout.firm_id == self.ctx.firm_id, Payout.claim_id == self.claim_id
                )
            ).first()
            held_payout = payout_row is not None and (
                payout_row.status == "held" or (payout_row.status == "transferring" and payout_row.hold_requested)
            )
            if outcome == "approve" and automated and held_payout:
                status = "under_review"  # a held payout is released only by a person
                decision["routed_to_human"] = True
                decision["hold_reason"] = "payout_held_needs_review"
            stmt = update(Claim).where(
                Claim.id == self.claim_id, Claim.firm_id == self.ctx.firm_id, Claim.status.in_(prior)
            )
            if self.lease_owner:
                stmt = stmt.where(Claim.lease_owner == self.lease_owner)
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
            _audit(
                session,
                self.ctx,
                str(final["decided_by"]),
                "claim_reviewed" if final.get("review_action") else "claim_adjudicated",
                self.claim_id,
                {"status": before},
                {"status": status, "outcome": outcome, "review_action": final.get("review_action")},
            )
            if status == "approved" and self.refund_cents:
                self.ledger.create_pending(self.ctx, self.claim_id, self.refund_cents, method, session=session)
            elif before == "approved" and status != "approved":
                hold_for_claim(session, self.ctx, self.claim_id, f"claim {status} by {final['decided_by']}")
            if automated and partners:
                reopen_duplicates(session, self.ctx, self.claim_id, partners, str(final["decided_by"]))
        log_event(log, "claim_decided", firm_id=str(self.ctx.firm_id), claim_id=str(self.claim_id), outcome=outcome)

    def hold(self, held: dict[str, Any]) -> None:
        decision = held.get("decision")
        stored = None
        if decision:
            stored = {**decision, "routed_to_human": True, "hold_reason": held["reason"]}
        with tenant_session(self.ctx, self.engine) as session:
            lock_claim_program(session, self.ctx.firm_id, self.claim_id)
            before = session.execute(
                text("SELECT status FROM claims WHERE id = :c AND firm_id = :f FOR UPDATE"),
                {"c": self.claim_id, "f": self.ctx.firm_id},
            ).scalar_one_or_none()
            stmt = update(Claim).where(
                Claim.id == self.claim_id, Claim.firm_id == self.ctx.firm_id, Claim.status == "submitted"
            )
            if self.lease_owner:
                stmt = stmt.where(Claim.lease_owner == self.lease_owner)
            done = session.execute(
                stmt.values(status=held["claim_status"], decision=stored).returning(Claim.id)
            ).first()
            if done is None:
                log_event(log, "claim_hold_skipped", claim_id=str(self.claim_id), reason=held["reason"], status=before)
                return
            _audit(
                session,
                self.ctx,
                "agent",
                "claim_held",
                self.claim_id,
                {"status": "submitted"},
                {"status": held["claim_status"], "reason": held["reason"], "detail": held.get("detail")},
            )
            partners = duplicate_partners(session, self.ctx.firm_id, self.claim_id, self.phash_bits)
            if partners:
                reopen_duplicates(session, self.ctx, self.claim_id, partners, "agent")
        log_event(log, "claim_held_for_review", claim_id=str(self.claim_id), reason=held["reason"])
