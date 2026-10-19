"""Deploy gate (CLAUDE.md invariant 13; 01-backend.md section 10): after `make eval` regenerates evals/results/ in
replay mode, compare the fresh numbers with a committed baseline and fail on any regression beyond tolerance:

- extraction: every per-field score and the headline of both extractors, on every block, may drop at most 2 points;
- adjudication: outcome accuracy and fraud F1 of every complete ablation row on every block may drop at most 0.02,
  and a row complete in the baseline must still be complete (a lost recording is a failure);
- reporting: MPR reconciliation must pass with zero discrepancies.

python -m recallops.evals.gate --baseline-ref origin/main     (CI: the base branch; default HEAD)
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


def committed(ref: str, name: str) -> dict[str, Any] | None:
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
                if score - n[extractor]["per_field"].get(field, 0.0) > EXTRACTION_POINTS:
                    problems.append(
                        f"extraction {block} {extractor} {field} {score:.1f} -> {n[extractor]['per_field'][field]:.1f}"
                    )
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


def check_reporting(now: dict[str, Any]) -> list[str]:
    return (
        []
        if now.get("passed") and now.get("discrepancies") == 0
        else [f"reporting: {now.get('discrepancies')} discrepancies"]
    )


def run(ref: str, results: Path = RESULTS) -> list[str]:
    problems: list[str] = []
    for name, check in (("extraction.json", check_extraction), ("ablation.json", check_ablation)):
        base = committed(ref, name)
        if base is None:
            continue  # no baseline at that ref yet: nothing to regress from
        problems += check(base, fresh(name, results))
    problems += check_reporting(fresh("reporting.json", results))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline-ref", default="HEAD")
    args = parser.parse_args(argv)
    problems = run(args.baseline_ref)
    for p in problems:
        print(f"REGRESSION: {p}")
    print("gate: " + ("FAIL" if problems else "PASS") + f" against {args.baseline_ref}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
