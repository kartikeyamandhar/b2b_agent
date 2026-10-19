"""Deploy gate (CLAUDE.md invariant 13; 01-backend.md section 10): after `make eval` regenerates evals/results/ in
replay mode, compare the fresh numbers with a committed baseline and fail on any regression beyond tolerance:

- extraction: every per-field score and the headline of both extractors, on every block, may drop at most 2 points;
- adjudication: outcome accuracy and fraud F1 of every complete ablation row on every block may drop at most 0.02,
  and a row complete in the baseline must still be complete (a lost recording is a failure);
- reporting: MPR reconciliation must pass with zero discrepancies.

A missing baseline is reported as a line, never silently skipped: a ref that does not resolve fails the gate, and
when the ref has no ablation.json the adjudication check falls back to adjudication.json (the rules_only row).

python -m recallops.evals.gate --baseline-ref origin/main     (pull request: the base branch)
python -m recallops.evals.gate --baseline-ref <github.event.before>   (push: the previous tip, never the commit itself)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from recallops.settings import REPO_ROOT

RESULTS = REPO_ROOT / "evals" / "results"
EXTRACTION_POINTS = 2.0
ADJUDICATION_DELTA = 0.02


class BaselineRefMissing(RuntimeError):
    pass


def verify_ref(ref: str) -> None:
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BaselineRefMissing(f"baseline ref {ref!r} does not resolve to a commit") from exc


def committed(ref: str, name: str) -> dict[str, Any] | None:
    """The results file at ref, or None when the file does not exist there (the ref itself is verified first)."""
    try:
        raw = subprocess.run(
            ["git", "show", f"{ref}:evals/results/{name}"], cwd=REPO_ROOT, capture_output=True, check=True, timeout=30
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    data: dict[str, Any] = json.loads(raw)
    return data


def fresh(name: str, results: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((results / name).read_text(encoding="utf-8"))
    return data


def _fraud_f1(m: dict[str, Any]) -> float:
    return float(m["per_class"]["flag"]["f1"])


def check_extraction(base: dict[str, Any], now: dict[str, Any]) -> list[str]:
    problems = []
    for block, b in base.get("blocks", {}).items():
        n = now.get("blocks", {}).get(block)
        if n is None:
            problems.append(f"extraction {block}: block missing")
            continue
        for extractor in ("baseline", "graph"):
            if not b.get(extractor):
                continue
            if not n.get(extractor):
                problems.append(f"extraction {block} {extractor}: no longer runs (recordings lost?)")
                continue
            if b[extractor]["headline"] - n[extractor]["headline"] > EXTRACTION_POINTS:
                problems.append(
                    f"extraction {block} {extractor} headline "
                    f"{b[extractor]['headline']:.1f} -> {n[extractor]['headline']:.1f}"
                )
            for field, score in b[extractor]["per_field"].items():
                now_score = n[extractor]["per_field"].get(field)
                if now_score is None:
                    problems.append(f"extraction {block} {extractor} {field}: field no longer scored")
                elif score - now_score > EXTRACTION_POINTS:
                    problems.append(f"extraction {block} {extractor} {field} {score:.1f} -> {now_score:.1f}")
    return problems


def check_ablation(base: dict[str, Any], now: dict[str, Any]) -> list[str]:
    problems = []
    for block, rows in base.get("blocks", {}).items():
        for row, b in rows.items():
            if "macro_f1" not in b:
                continue
            n = now.get("blocks", {}).get(block, {}).get(row, {})
            if "macro_f1" not in n:
                problems.append(f"ablation {block} {row}: complete in the baseline, not run now (recordings lost?)")
                continue
            if b["outcome_accuracy"] - n["outcome_accuracy"] > ADJUDICATION_DELTA:
                problems.append(
                    f"ablation {block} {row} accuracy {b['outcome_accuracy']:.3f} -> {n['outcome_accuracy']:.3f}"
                )
            if _fraud_f1(b) - _fraud_f1(n) > ADJUDICATION_DELTA:
                problems.append(f"ablation {block} {row} fraud F1 {_fraud_f1(b):.3f} -> {_fraud_f1(n):.3f}")
    return problems


def ablation_from_adjudication(adjudication: dict[str, Any]) -> dict[str, Any]:
    """The pre-ablation results file (rules_only on the 300 golden claims) in the ablation payload's shape."""
    row = adjudication.get("rules_only")
    if not row or "outcome_accuracy" not in row:
        return {"blocks": {}}
    flag = row.get("per_class", {}).get("flag", {})
    f1 = flag.get("f1")
    if f1 is None:
        p, r = row.get("fraud_precision") or 0.0, row.get("fraud_recall") or 0.0
        f1 = round(2 * p * r / (p + r), 4) if p + r else 0.0
    return {
        "blocks": {
            "golden300": {
                "rules_only": {
                    "macro_f1": row["macro_f1"],
                    "outcome_accuracy": row["outcome_accuracy"],
                    "per_class": {"flag": {"f1": f1}},
                }
            }
        }
    }


def check_reporting(now: dict[str, Any]) -> list[str]:
    return (
        []
        if now.get("passed") and now.get("discrepancies") == 0
        else [f"reporting: {now.get('discrepancies')} discrepancies"]
    )


def run(ref: str, results: Path = RESULTS) -> tuple[list[str], list[str]]:
    """(problems, notes). Notes name every check that had no baseline, so a skip is visible in the CI log."""
    verify_ref(ref)
    problems: list[str] = []
    notes: list[str] = []
    base = committed(ref, "extraction.json")
    if base is None:
        notes.append(f"no extraction baseline at {ref}")
    else:
        problems += check_extraction(base, fresh("extraction.json", results))
    base = committed(ref, "ablation.json")
    if base is None:
        legacy = committed(ref, "adjudication.json")
        if legacy is None:
            notes.append(f"no adjudication baseline at {ref}")
        else:
            notes.append(f"no ablation.json at {ref}: comparing rules_only on golden300 from adjudication.json")
            base = ablation_from_adjudication(legacy)
    if base is not None:
        problems += check_ablation(base, fresh("ablation.json", results))
    problems += check_reporting(fresh("reporting.json", results))
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-ref", default="HEAD")
    args = parser.parse_args(argv)
    try:
        problems, notes = run(args.baseline_ref)
    except BaselineRefMissing as exc:
        print(f"gate: FAIL ({exc})")
        return 1
    for note in notes:
        print(f"NOTE: {note}")
    for p in problems:
        print(f"REGRESSION: {p}")
    print("gate: " + ("FAIL" if problems else "PASS") + f" against {args.baseline_ref}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
