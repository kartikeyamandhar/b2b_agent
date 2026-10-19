"""Adjudication eval: rules-only row on the 300 synthetic golden claims (evals/adjudication/PREREG.md).
Writes evals/results/adjudication.md and adjudication.json."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from recallops.claims.rules_only import DECISION_TABLE_VERSION, Adjudication, adjudicate
from recallops.claims.tools import PHASH_KINDS, TOOLS_VERSION, ClaimFacts, PhotoRef
from recallops.evals.golden import load_program_rules
from recallops.settings import REPO_ROOT, get_settings
from recallops.tracing.writer import MemoryTrace

ADJ_DIR = REPO_ROOT / "evals" / "adjudication"
RESULTS_DIR = REPO_ROOT / "evals" / "results"
CLASSES = ("approve", "deny", "needs_info", "flag")


def facts_from_record(r: dict[str, Any]) -> ClaimFacts:
    claimant = r.get("claimant") or {}
    address = " ".join(str(claimant.get(k, "")) for k in ("address_line1", "city", "state", "zip")).strip() or None
    return ClaimFacts(
        claim_id=UUID(r["claim_id"]),
        model_claimed=r.get("model_claimed"),
        serial_claimed=r.get("serial_claimed"),
        date_code_claimed=r.get("date_code_claimed"),
        retailer_claimed=r.get("retailer_claimed"),
        purchase_date=date.fromisoformat(r["purchase_date"]) if r.get("purchase_date") else None,
        refund_cents=r.get("refund_cents"),
        email=claimant.get("email"),
        address=address,
        payout_destination=claimant.get("payout_destination"),
        photos=tuple(PhotoRef(kind=p["kind"], sha256=p["sha256"], phash=p["phash"]) for p in r.get("photos", [])),
    )


@dataclass
class Scored:
    claim_id: str
    program: str
    expected: str
    predicted: str
    routed: bool
    violations: list[str]
    subtypes: dict[str, str]
    steps: int
    cost_usd: float
    seconds: float


def f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0:
        return 0.0
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def summarize(rows: list[Scored]) -> dict[str, Any]:
    n = len(rows)
    per_class = {}
    for cls in CLASSES:
        tp = sum(1 for r in rows if r.predicted == cls and r.expected == cls)
        fp = sum(1 for r in rows if r.predicted == cls and r.expected != cls)
        fn = sum(1 for r in rows if r.predicted != cls and r.expected == cls)
        per_class[cls] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "f1": round(f1(tp, fp, fn), 4),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    return {
        "n": n,
        "outcome_accuracy": round(sum(r.predicted == r.expected for r in rows) / n, 4),
        "macro_f1": round(sum(per_class[c]["f1"] for c in CLASSES) / len(CLASSES), 4),  # type: ignore[misc]
        "approval_accuracy": round(sum((r.predicted == "approve") == (r.expected == "approve") for r in rows) / n, 4),
        "fraud_precision": per_class["flag"]["precision"],
        "fraud_recall": per_class["flag"]["recall"],
        "needs_info_precision": per_class["needs_info"]["precision"],
        "share_routed_to_human": round(sum(r.routed for r in rows) / n, 4),
        "mean_steps": round(statistics.mean(r.steps for r in rows), 2),
        "mean_cost_usd": round(statistics.mean(r.cost_usd for r in rows), 6),
        "p50_seconds": round(statistics.median(r.seconds for r in rows), 5),
        "per_class": per_class,
        "confusion": {e: dict(Counter(r.predicted for r in rows if r.expected == e)) for e in CLASSES},
    }


def per_violation(rows: list[Scored]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[Scored]] = defaultdict(list)
    for r in rows:
        for v in r.violations:
            buckets[v].append(r)
            if v in r.subtypes:
                buckets[f"{v}:{r.subtypes[v]}"].append(r)
    for name, items in sorted(buckets.items()):
        hit = sum(1 for r in items if r.predicted == r.expected)
        out[name] = {"n": len(items), "correct": hit, "recall": round(hit / len(items), 4)}
    return out


FIRST_RUN_PHASH_KINDS = frozenset({"label", "destruction", "receipt", "other"})


def run(
    golden_path: Path = ADJ_DIR / "golden_claims.json", phash_kinds: frozenset[str] = PHASH_KINDS
) -> tuple[list[Scored], list[Adjudication]]:
    settings = get_settings()
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    rules_by_program = load_program_rules(ADJ_DIR / "program_rules.json")
    by_program: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in payload["claims"]:
        by_program[record["program"]].append(record)
    rows: list[Scored] = []
    results: list[Adjudication] = []
    for program, records in by_program.items():
        facts = [facts_from_record(r) for r in records]
        for record, claim in zip(records, facts, strict=True):
            trace = MemoryTrace("rules_only")
            result = adjudicate(
                claim,
                rules_by_program[program],
                facts,
                settings.default_value_threshold_cents,
                trace,
                settings.phash_duplicate_bits,
                phash_kinds,
            )
            label = json.loads((ADJ_DIR / "labels" / f"{record['claim_id']}.json").read_text(encoding="utf-8"))
            rows.append(
                Scored(
                    record["claim_id"],
                    program,
                    label["expected_outcome"],
                    result.outcome,
                    result.routed_to_human,
                    label["violations"],
                    label["subtypes"],
                    len(trace.steps),
                    float(trace.cost_usd),
                    result.latency_ms / 1000,
                )
            )
            results.append(result)
    return rows, results


def _row(name: str, s: dict[str, Any]) -> str:
    return (
        f"| {name} | {s['n']} | {s['outcome_accuracy']:.3f} | {s['macro_f1']:.3f} | {s['approval_accuracy']:.3f} | "
        f"{s['fraud_precision']:.3f} | {s['fraud_recall']:.3f} | {s['needs_info_precision']:.3f} | "
        f"{s['share_routed_to_human']:.3f} | {s['mean_steps']:.1f} | {s['mean_cost_usd']:.4f} | "
        f"{s['p50_seconds']:.4f} |"
    )


def render(summary: dict[str, Any], violations: dict[str, dict[str, Any]], first_run: dict[str, Any]) -> str:
    s = summary
    lines = [
        "# Adjudication eval",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`. Pre-registration: "
        "evals/adjudication/PREREG.md.",
        "",
        f"- Golden claims: {s['n']} synthetic claims from 20 golden notices (gold rules), planted violations, "
        "expected outcomes from outcome_policy.yaml (decided 2026-10-17). The owner-reviewed and classmate blocks "
        "were replaced by probe60 and standin40 (PREREG Revision 2; see ablation.md).",
        f"- Rules-only decision table {DECISION_TABLE_VERSION}; pure-code tools; destruction photo presence only "
        "(no vision).",
        "- This table is the rules-only baseline. Every agent row, and the kill criterion (rules_only vs loop_full "
        "on these 300 claims), is in evals/results/ablation.md.",
        "- The first run (tools 1.0.0) flagged 44 legitimate claims: all 82 false duplicate hits were receipt photos "
        "matched by pHash, which ignores the text that distinguishes template documents. Tools 1.0.1 match receipts "
        "on exact bytes only (decisions log). Both rows are shown; 1.0.1 is the baseline going forward.",
        "",
        "| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed to human "
        "| mean steps | mean cost USD | p50 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        _row(f"rules_only (tools {TOOLS_VERSION}, receipts sha256 only)", s),
        _row("rules_only (tools 1.0.0, first run, pHash on receipts)", first_run),
        "",
        "Per class:",
        "",
        "| class | precision | recall | F1 |",
        "|---|---|---|---|",
    ]
    for cls in CLASSES:
        pc = s["per_class"][cls]
        lines.append(f"| {cls} | {pc['precision']} | {pc['recall']} | {pc['f1']} |")
    lines += [
        "",
        "Per planted violation (share of claims carrying it whose outcome matched the expected outcome):",
        "",
        "| violation | n | correct | recall |",
        "|---|---|---|---|",
    ]
    for name, v in violations.items():
        lines.append(f"| {name} | {v['n']} | {v['correct']} | {v['recall']:.3f} |")
    lines += ["", "Confusion (expected -> predicted counts):", "", "```", json.dumps(s["confusion"], indent=1), "```"]
    return "\n".join(lines) + "\n"


def main(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    rows, _ = run()
    summary = summarize(rows)
    violations = per_violation(rows)
    first_rows, _ = run(phash_kinds=FIRST_RUN_PHASH_KINDS)
    first_run = summarize(first_rows)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "adjudication.md").write_text(render(summary, violations, first_run), encoding="utf-8")
    (results_dir / "adjudication.json").write_text(
        json.dumps({"rules_only": summary, "rules_only_first_run": first_run, "per_violation": violations}, indent=1)
        + "\n",
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
