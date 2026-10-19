"""The independent recount behind MPR reconciliation (01-backend.md section 8): plain row exports, counted in Polars.
It shares no query with mpr/generate.py; the two must agree on every computed box (zero tolerance)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import text
from sqlalchemy.orm import Session

from recallops.mpr.generate import DELIVERED, Period

COMPUTED = ("corrections_this_period", "cumulative_corrections", "contacts_email")


def export(session: Session, firm_id: UUID, program_id: UUID) -> dict[str, pl.DataFrame]:
    """Row-level exports only: no filtering by period, no joins, no aggregation in SQL."""
    claims = (
        session.execute(
            text(
                "SELECT id::text AS claim_id, program_id::text AS program_id, submitted_at "
                "FROM claims WHERE firm_id = :f"
            ),
            {"f": firm_id},
        )
        .mappings()
        .all()
    )
    payouts = (
        session.execute(
            text("SELECT id::text AS payout_id, claim_id::text AS claim_id FROM payouts WHERE firm_id = :f"),
            {"f": firm_id},
        )
        .mappings()
        .all()
    )
    audit = (
        session.execute(
            text("SELECT target_type, target_id, action, at FROM audit_log WHERE firm_id = :f"), {"f": firm_id}
        )
        .mappings()
        .all()
    )
    schema_claims: dict[str, Any] = {
        "claim_id": pl.String,
        "program_id": pl.String,
        "submitted_at": pl.Datetime("us", "UTC"),
    }
    schema_payouts: dict[str, Any] = {"payout_id": pl.String, "claim_id": pl.String}
    schema_audit: dict[str, Any] = {
        "target_type": pl.String,
        "target_id": pl.String,
        "action": pl.String,
        "at": pl.Datetime("us", "UTC"),
    }
    return {
        "claims": pl.DataFrame([dict(r) for r in claims], schema=schema_claims).filter(
            pl.col("program_id") == str(program_id)
        ),
        "payouts": pl.DataFrame([dict(r) for r in payouts], schema=schema_payouts),
        "audit": pl.DataFrame([dict(r) for r in audit], schema=schema_audit),
    }


def recount(frames: dict[str, pl.DataFrame], period: Period) -> dict[str, int]:
    claims, payouts, audit = frames["claims"], frames["payouts"], frames["audit"]
    program_payouts = payouts.join(claims.select("claim_id"), on="claim_id", how="inner")
    delivered = audit.filter((pl.col("target_type") == "payout") & pl.col("action").is_in(list(DELIVERED))).join(
        program_payouts, left_on="target_id", right_on="payout_id", how="inner"
    )
    in_period = delivered.filter((pl.col("at") >= period.start_ts) & (pl.col("at") <= period.end_ts))
    upto = delivered.filter(pl.col("at") <= period.end_ts)
    submitted = claims.filter(
        pl.col("submitted_at").is_not_null()
        & (pl.col("submitted_at") >= period.start_ts)
        & (pl.col("submitted_at") <= period.end_ts)
    )
    return {
        "corrections_this_period": in_period.height,
        "cumulative_corrections": upto.height,
        "contacts_email": submitted.height,
    }


def reconcile(boxes: dict[str, Any], recounted: dict[str, int]) -> list[str]:
    return [f"{k}: report {boxes[k]} vs recount {recounted[k]}" for k in COMPUTED if boxes[k] != recounted[k]]
