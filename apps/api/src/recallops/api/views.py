"""Model rows to API schemas."""

from __future__ import annotations

from typing import Any

from recallops.api import schemas as s
from recallops.models import (
    AgentRun,
    AgentStep,
    AuditLog,
    Claim,
    ClaimPhoto,
    FraudCluster,
    Payout,
    Recall,
    RecallProgram,
)
from recallops.rules.schemas import IdentificationRules, RemedyRules


def recall_summary(r: Recall) -> s.RecallSummary:
    return s.RecallSummary(
        id=r.id,
        source=r.source,
        source_id=r.source_id,
        recall_date=r.recall_date,
        title=r.title,
        url=r.url,
        units_affected=r.units_affected,
        units_status=r.units_status,
        category=r.category,
    )


def program(p: RecallProgram, title: str, units: int | None, counts: dict[str, int]) -> s.Program:
    return s.Program(
        id=p.id,
        recall_id=p.recall_id,
        title=title,
        status=p.status,
        units_affected=units,
        identification_rules=IdentificationRules.model_validate(p.identification_rules or {}),
        remedy_rules=RemedyRules.model_validate(p.remedy_rules or {}),
        value_threshold_cents=p.value_threshold_cents,
        claim_counts=counts,
        opened_at=p.opened_at,
        closed_at=p.closed_at,
        created_at=p.created_at,
    )


def decision(c: Claim) -> s.Decision | None:
    d: dict[str, Any] | None = c.decision
    if not d:
        return None
    return s.Decision(
        outcome=d["outcome"],
        reasons=d.get("reasons", []),
        cited_rules=d.get("cited_rules", []),
        confidence=d.get("confidence"),
        routed_to_human=bool(d.get("routed_to_human")),
        decided_by=c.decided_by,
    )


def claim_summary(c: Claim) -> s.ClaimSummary:
    d = c.decision or {}
    return s.ClaimSummary(
        id=c.id,
        program_id=c.program_id,
        status=c.status,
        model_claimed=c.model_claimed,
        retailer_claimed=c.retailer_claimed,
        purchase_date=c.purchase_date,
        refund_cents=c.refund_cents,
        outcome=d.get("outcome"),
        routed_to_human=bool(d.get("routed_to_human")),
        created_at=c.created_at,
        decided_at=c.decided_at,
    )


def photo(p: ClaimPhoto) -> s.Photo:
    return s.Photo(
        id=p.id,
        kind=p.kind,
        url=f"/v1/claims/{p.claim_id}/photos/{p.id}",
        sha256=p.sha256,
        phash=p.phash,
        uploaded_at=p.uploaded_at,
    )


def run_summary(r: AgentRun) -> s.RunSummary:
    return s.RunSummary(
        run_id=r.id,
        graph=r.graph,
        status=r.status,
        steps=r.steps,
        cost_usd=f"{r.cost_usd:.6f}",
        started_at=r.started_at,
        finished_at=r.finished_at,
    )


def trace_step(t: AgentStep) -> s.TraceStep:
    return s.TraceStep(
        idx=t.idx,
        node=t.node,
        thought=t.thought,
        tool=t.tool,
        args=t.args,
        result=t.result,
        latency_ms=t.latency_ms,
        tokens_in=t.tokens_in,
        tokens_out=t.tokens_out,
        cost_usd=f"{t.cost_usd:.6f}",
        created_at=t.created_at,
    )


def cluster(f: FraudCluster) -> s.Cluster:
    return s.Cluster(id=f.id, kind=f.kind, member_claim_ids=f.member_claim_ids, score=float(f.score))


def payout(p: Payout) -> s.PayoutView:
    return s.PayoutView(id=p.id, amount_cents=p.amount_cents, currency=p.currency, method=p.method, status=p.status)


def audit(a: AuditLog) -> s.AuditEntry:
    return s.AuditEntry(actor=a.actor, action=a.action, at=a.at, before=a.before, after=a.after)


def claimant(data: dict[str, Any]) -> s.Claimant:
    keys = set(s.Claimant.model_fields)
    return s.Claimant.model_validate({k: v for k, v in data.items() if k in keys})
