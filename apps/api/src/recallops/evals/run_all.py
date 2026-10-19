"""make eval: every eval in the configured LLM_MODE (replay by default; no live model calls) and the results tables
in evals/results/: extraction, adjudication (rules_only), the ablation table, and MPR reconciliation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from recallops.evals import ablation, adjudication, extraction, reporting
from recallops.logs import configure
from recallops.settings import get_settings


def main(results_dir: Path = extraction.RESULTS_DIR) -> int:
    configure(get_settings().log_level)
    ext = extraction.main(results_dir)
    adj = adjudication.main(results_dir)
    abl = ablation.main([], results_dir)
    rep = reporting.main(results_dir)
    summary = {
        "extraction": {
            "label_status": ext["label_status"],
            "baseline_headline_all": ext["baseline_all"]["headline"],
            "baseline_headline_heldout": ext["baseline_heldout"]["headline"],
            "graph": ext["graph_status"],
            "graph_model_calls": ext["graph_calls"],
            "graph_cost_usd": ext["graph_cost_usd"],
        },
        "adjudication": {k: adj[k] for k in ("n", "outcome_accuracy", "macro_f1", "fraud_precision", "fraud_recall")},
        "ablation": ablation._summary(abl),
        "reporting": {
            "passed": rep["passed"],
            "discrepancies": rep["discrepancies"],
            "composition_ok": rep["composition_ok"],
        },
        "results": sorted(str(p.relative_to(results_dir.parent.parent)) for p in results_dir.glob("*")),
    }
    print(json.dumps(summary, indent=1))
    return 0 if rep["passed"] and rep["composition_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
