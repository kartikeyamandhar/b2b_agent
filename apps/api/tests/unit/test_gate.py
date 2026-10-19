"""The deploy gate's comparisons (evals/gate.py)."""

from __future__ import annotations

from typing import Any

from recallops.evals.gate import ablation_from_adjudication, check_ablation, check_extraction, check_reporting, main


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


def test_a_field_missing_from_the_fresh_results_is_a_regression_not_a_crash() -> None:
    base = ext(92.0, 80.0)
    now = {"blocks": {"new60": {"baseline": {"headline": 92.0, "per_field": {}}, "graph": None}}}
    assert check_extraction(base, now) == ["extraction new60 baseline models: field no longer scored"]


def test_the_legacy_adjudication_file_still_gates_rules_only() -> None:
    legacy = {"rules_only": {"macro_f1": 0.98, "outcome_accuracy": 0.987, "fraud_precision": 1.0, "fraud_recall": 1.0}}
    base = ablation_from_adjudication(legacy)
    worse = {
        "blocks": {
            "golden300": {"rules_only": {"macro_f1": 0.9, "outcome_accuracy": 0.9, "per_class": {"flag": {"f1": 0.8}}}}
        }
    }
    problems = check_ablation(base, worse)
    assert len(problems) == 2 and all("rules_only" in p for p in problems)


def test_a_ref_that_does_not_resolve_fails_the_gate(capsys: Any) -> None:
    assert main(["--baseline-ref", "no-such-ref-for-the-gate"]) == 1
    assert "does not resolve" in capsys.readouterr().out


def test_self_consistency_ignores_wall_clock_seconds_only() -> None:
    from recallops.evals.gate import _differences

    base = {"blocks": {"g": {"row": {"macro_f1": 0.9, "p50_seconds": 1.2, "per_class": {"flag": {"f1": 1.0}}}}}}
    same = {"blocks": {"g": {"row": {"macro_f1": 0.9, "p50_seconds": 9.9, "per_class": {"flag": {"f1": 1.0}}}}}}
    moved = {"blocks": {"g": {"row": {"macro_f1": 0.9, "p50_seconds": 1.2, "per_class": {"flag": {"f1": 0.99}}}}}}
    assert _differences(base, same) == []
    assert _differences(base, moved) == [".blocks.g.row.per_class.flag.f1"]


def test_cpsc_photos_regressions_fail_and_improvements_pass() -> None:
    from recallops.evals.gate import check_cpsc_photos

    base = {
        "metrics": {
            "missing": 0,
            "label_read_rate": 0.9,
            "label_misread_rate": 0.05,
            "label_hallucination_rate": 0.1,
            "product_rejected": 19,
            "destruction_accepted": 3,
            "destruction_not_shown_rejected": 1,
        }
    }
    assert check_cpsc_photos(base, base) == []
    better = {"metrics": {**base["metrics"], "label_read_rate": 0.95, "label_misread_rate": 0.0}}
    assert check_cpsc_photos(base, better) == []
    worse = {
        "metrics": {
            **base["metrics"],
            "missing": 2,
            "label_read_rate": 0.85,
            "label_hallucination_rate": 0.2,
            "product_rejected": 18,
        }
    }
    found = " ".join(check_cpsc_photos(base, worse))
    for key in ("2 photos", "label_read_rate", "label_hallucination_rate", "product_rejected"):
        assert key in found
    assert check_cpsc_photos({}, {"metrics": {"label_read_rate": None, "missing": 0}}) == []
