"""The deploy gate's comparisons (evals/gate.py)."""

from __future__ import annotations

from typing import Any

from recallops.evals.gate import check_ablation, check_extraction, check_reporting


def ext(headline: float, field: float) -> dict[str, Any]:
    return {"blocks": {"new60": {"baseline": {"headline": headline, "per_field": {"models": field}}, "graph": None}}}


def abl(acc: float, f1: float) -> dict[str, Any]:
    return {
        "blocks": {
            "golden300": {"loop_full": {"macro_f1": 0.9, "outcome_accuracy": acc, "per_class": {"flag": {"f1": f1}}}}
        }
    }


def test_extraction_tolerance_is_two_points() -> None:
    assert check_extraction(ext(92.0, 80.0), ext(90.5, 78.5)) == []
    problems = check_extraction(ext(92.0, 80.0), ext(89.9, 77.0))
    assert len(problems) == 2 and "headline" in problems[0]


def test_adjudication_tolerance_and_lost_rows() -> None:
    assert check_ablation(abl(0.95, 0.90), abl(0.935, 0.885)) == []
    assert len(check_ablation(abl(0.95, 0.90), abl(0.92, 0.85))) == 2
    lost = check_ablation(abl(0.95, 0.90), {"blocks": {"golden300": {"loop_full": {"status": "not run"}}}})
    assert lost and "recordings lost" in lost[0]


def test_reporting_must_reconcile_exactly() -> None:
    assert check_reporting({"passed": True, "discrepancies": 0}) == []
    assert check_reporting({"passed": False, "discrepancies": 1})
