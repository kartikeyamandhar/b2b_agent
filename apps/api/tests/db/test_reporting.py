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
    real = generate.counts

    def rows_not_items(*args: Any) -> dict[str, int]:
        out = real(*args)
        out["cumulative_corrections"] += 1  # what the one replayed delivery event adds when rows are counted
        return out

    monkeypatch.setattr(generate, "counts", rows_not_items)
    result = reporting.run(clean_db)
    assert not result["passed"] and result["discrepancies"] >= 3
