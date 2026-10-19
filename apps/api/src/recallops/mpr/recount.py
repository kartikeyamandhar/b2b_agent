"""The independent recount behind MPR reconciliation (01-backend.md section 8): plain row exports, counted in Polars.

It shares no query, constant, or date logic with mpr/generate.py (red team, decisions log 2026-10-18): its own
action names, its own month test (the event's UTC year-month string), distinct items rather than audit rows, and its
own unit arithmetic. The two must agree on every computed box (zero tolerance).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import text
from sqlalchemy.orm import Session

PAID_OUT = ["payout_transferred", "payout_exported"]  # the ledger's delivery events for refunds
HANDED_OVER = "remedy_delivered"  # a reviewer's delivery record for a repair or replacement
HOLDERS = ("units_with_manufacturer", "units_with_distributor", "units_with_retailers")


def _frame(rows: Any, schema: dict[str, Any]) -> pl.DataFrame:
    return pl.DataFrame([dict(r) for r in rows], schema=schema, orient="row")


def export(session: Session, firm_id: UUID, program_id: UUID) -> dict[str, pl.DataFrame]:
    """Row-level exports only: no period filter, no join, no aggregation in SQL."""

    def q(sql: str) -> Any:
        return session.execute(text(sql), {"f": firm_id}).mappings().all()

    ts = pl.Datetime("us", "UTC")
    return {
        "claims": _frame(
            q(
                "SELECT id::text AS claim_id, program_id::text AS program_id, status, first_submitted_at "
                "FROM claims WHERE firm_id = :f"
            ),
            {"claim_id": pl.String, "program_id": pl.String, "status": pl.String, "first_submitted_at": ts},
        ).filter(pl.col("program_id") == str(program_id)),
        "payouts": _frame(
            q("SELECT id::text AS payout_id, claim_id::text AS claim_id FROM payouts WHERE firm_id = :f"),
            {"payout_id": pl.String, "claim_id": pl.String},
        ),
        "audit": _frame(
            q("SELECT target_type, target_id, action, at FROM audit_log WHERE firm_id = :f"),
            {"target_type": pl.String, "target_id": pl.String, "action": pl.String, "at": ts},
        ),
        "notices": _frame(
            q(
                "SELECT registration_id::text AS owner, program_id::text AS program_id, channel, status, sent_at "
                "FROM notifications WHERE firm_id = :f"
            ),
            {"owner": pl.String, "program_id": pl.String, "channel": pl.String, "status": pl.String, "sent_at": ts},
        ).filter(pl.col("program_id") == str(program_id)),
        "program": _frame(
            session.execute(
                text(
                    "SELECT p.identification_rules->>'units_affected' AS rules_units, r.units_affected AS feed_units "
                    "FROM recall_programs p LEFT JOIN recalls r ON r.id = p.recall_id WHERE p.firm_id = :f "
                    "AND p.id = :p"
                ),
                {"f": firm_id, "p": program_id},
            )
            .mappings()
            .all(),
            {"rules_units": pl.String, "feed_units": pl.Int64},
        ),
    }


def _month(col: str) -> pl.Expr:
    return pl.col(col).dt.convert_time_zone("UTC").dt.strftime("%Y-%m")


def recount(frames: dict[str, pl.DataFrame], period: str, manual: dict[str, Any] | None = None) -> dict[str, int]:
    claims, payouts, audit, program = frames["claims"], frames["payouts"], frames["audit"], frames["program"]
    refunds = (
        audit.filter((pl.col("target_type") == "payout") & pl.col("action").is_in(PAID_OUT))
        .join(payouts, left_on="target_id", right_on="payout_id", how="inner")
        .join(claims.select("claim_id"), on="claim_id", how="inner")
        .select(pl.concat_str([pl.lit("refund "), pl.col("target_id")]).alias("item"), "at")
    )
    remedies = (
        audit.filter((pl.col("target_type") == "claim") & (pl.col("action") == HANDED_OVER))
        .join(claims.select("claim_id"), left_on="target_id", right_on="claim_id", how="inner")
        .select(pl.concat_str([pl.lit("remedy "), pl.col("target_id")]).alias("item"), "at")
    )
    first = (
        pl.concat([refunds, remedies]).group_by("item").agg(pl.col("at").min()).with_columns(_month("at").alias("m"))
    )
    this_period = first.filter(pl.col("m") == period).height
    cumulative = first.filter(pl.col("m") <= period).height
    contacted = claims.filter(
        (pl.col("status") != "draft")
        & pl.col("first_submitted_at").is_not_null()
        & (_month("first_submitted_at") == period)
    ).height
    rules_units = program["rules_units"][0] if program.height else None
    feed_units = program["feed_units"][0] if program.height else None
    total = int(rules_units) if rules_units not in (None, "") else int(feed_units or 0)
    held = sum(int((manual or {}).get(k, 0)) for k in HOLDERS)
    notices = frames["notices"]
    reached = (
        notices.filter((pl.col("status") == "sent") & pl.col("sent_at").is_not_null() & (_month("sent_at") == period))
        .group_by("channel")
        .agg(pl.col("owner").n_unique().alias("n"))
    )
    per_channel = {str(row["channel"]): int(row["n"]) for row in reached.iter_rows(named=True)}
    return {
        "total_units_affected": total,
        "corrections_this_period": this_period,
        "cumulative_corrections": cumulative,
        "units_with_consumers": max(0, total - held - cumulative),
        "contacts_email": contacted,
        "notified_email": per_channel.get("email", 0),
        "notified_mail": per_channel.get("mail", 0),
        "notified_phone_text": per_channel.get("phone_text", 0),
    }


def reconcile(computed: dict[str, Any], recounted: dict[str, int]) -> list[str]:
    """Every computed box, report against recount. Manual overrides are not compared: they are not computed."""
    return [f"{k}: report {computed[k]} vs recount {v}" for k, v in recounted.items() if computed.get(k) != v]
