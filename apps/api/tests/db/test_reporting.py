"""The MPR reconciliation can fail: mutations of the report's definitions are caught by the recount and the truth."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from recallops.evals import reporting
from recallops.mpr import generate

pytestmark = pytest.mark.db


def test_the_scenario_reconciles_with_its_pre_registered_composition(clean_db: Engine) -> None:
    result = reporting.run(clean_db)
    assert result["composition_ok"] and result["scenario"]["refund_claims"] == 500
    assert result["passed"] and result["discrepancies"] == 0


def test_an_inclusive_month_end_is_caught(clean_db: Engine, monkeypatch: Any) -> None:
    """The classic boundary bug: the next month's first instant counted in this month."""

    def inclusive_end(self: generate.Period) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=UTC)

    monkeypatch.setattr(generate.Period, "end_ts", property(inclusive_end))
    result = reporting.run(clean_db)
    assert not result["passed"]
    assert any("corrections_this_period" in d for m in result["months"] for d in m["diffs"])


def test_counting_audit_rows_instead_of_deliveries_is_caught(clean_db: Engine, monkeypatch: Any) -> None:
    """The bug a careless report has: counting delivery audit rows instead of the first delivery per item. The
    scenario replays one August delivery in September, so this SQL is off by one in September and October."""
    from sqlalchemy import text

    real = generate.counts

    def rows_not_items(session: Any, firm_id: Any, program_id: Any, period: Any) -> dict[str, int]:
        out = real(session, firm_id, program_id, period)
        rows = session.execute(
            text(
                "SELECT count(*) FILTER (WHERE a.at BETWEEN :s AND :e) AS this_period, "
                "count(*) FILTER (WHERE a.at <= :e) AS cumulative FROM audit_log a "
                "JOIN payouts pay ON pay.id::text = a.target_id JOIN claims c ON c.id = pay.claim_id "
                "WHERE a.firm_id = :f AND c.program_id = :p AND a.target_type = 'payout' "
                "AND a.action IN ('payout_transferred', 'payout_exported')"
            ),
            {"f": firm_id, "p": program_id, "s": period.start_ts, "e": period.end_ts},
        ).one()
        remedies = session.execute(
            text(
                "SELECT count(*) FILTER (WHERE a.at BETWEEN :s AND :e), count(*) FILTER (WHERE a.at <= :e) "
                "FROM audit_log a JOIN claims c ON c.id::text = a.target_id WHERE a.firm_id = :f "
                "AND c.program_id = :p AND a.action = 'remedy_delivered'"
            ),
            {"f": firm_id, "p": program_id, "s": period.start_ts, "e": period.end_ts},
        ).one()
        out["corrections_this_period"] = int(rows.this_period) + int(remedies[0])
        out["cumulative_corrections"] = int(rows.cumulative) + int(remedies[1])
        return out

    monkeypatch.setattr(generate, "counts", rows_not_items)
    result = reporting.run(clean_db)
    assert result["composition_ok"] and not result["passed"]
    bad = {m["period"]: m["diffs"] for m in result["months"] if m["diffs"]}
    assert set(bad) == {"2026-09", "2026-10"}, bad  # August is right: its replay landed in September
