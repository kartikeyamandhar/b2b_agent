"""Reporting eval (01-backend.md sections 8 and 10): MPR reconciliation on a 500-claim program in mixed states.

python -m recallops.evals.reporting     (make eval runs it) writes evals/results/reporting.md and reporting.json

Builds a deterministic scenario under an "MPR Eval" firm: 500 claims submitted from August to October 2026 in mixed
outcomes, refund payouts that move through pending, onboarding, failed, transferred, pending_export, and exported
with audit timestamps spread over the three months. For each month the SQL report (mpr/generate.py) is compared
with the independent Polars recount (mpr/recount.py). Zero tolerance: any difference fails the eval and the deploy
gate.
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from recallops.db.engine import internal_engine
from recallops.mpr.generate import Period, build_payload
from recallops.mpr.recount import COMPUTED, export, reconcile, recount
from recallops.settings import REPO_ROOT

RESULTS = REPO_ROOT / "evals" / "results"
FIRM = uuid.UUID("00000000-0000-4000-8000-0000000003a1")
PROGRAM = uuid.UUID("00000000-0000-4000-8000-0000000003b1")
NS = uuid.UUID("3a1a0000-0000-4000-8000-000000000500")
MONTHS = ("2026-08", "2026-09", "2026-10")
N = 500
SEED = 500


def _ts(rng: random.Random, start: date, days: int) -> datetime:
    return datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(
        seconds=rng.randint(0, days * 86400 - 1)
    )


def build_scenario(engine: Engine) -> dict[str, int]:
    rng = random.Random(SEED)
    window = date(2026, 8, 1)
    stats = {"claims": 0, "payouts": 0, "delivered_events": 0}
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))  # reset tooling only
        for table in ("payouts", "audit_log", "claims", "recall_programs"):
            session.execute(text(f"DELETE FROM {table} WHERE firm_id = :f"), {"f": FIRM})
        session.execute(text("DELETE FROM firms WHERE id = :f"), {"f": FIRM})
        session.execute(text("INSERT INTO firms (id, name, plan) VALUES (:f, 'MPR Eval', 'growth')"), {"f": FIRM})
        session.execute(
            text(
                "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules) "
                "VALUES (:p, :f, 'open', 10000, CAST(:i AS jsonb))"
            ),
            {"p": PROGRAM, "f": FIRM, "i": json.dumps({"models": ["TL1302Z"], "units_affected": 53634})},
        )
        for i in range(N):
            claim_id = uuid.uuid5(NS, f"claim:{i}")
            submitted = _ts(rng, window, 92)
            status = rng.choices(["approved", "denied", "needs_info", "flagged", "draft"], [55, 15, 12, 10, 8])[0]
            session.execute(
                text(
                    "INSERT INTO claims (id, firm_id, program_id, token_hash, status, submitted_at, refund_cents, "
                    "remedy) VALUES (:c, :f, :p, :t, :s, :at, 4999, 'refund')"
                ),
                {
                    "c": claim_id,
                    "f": FIRM,
                    "p": PROGRAM,
                    "t": f"mpr-{i}",
                    "s": status,
                    "at": None if status == "draft" else submitted,
                },
            )
            stats["claims"] += 1
            if status != "approved":
                continue
            payout_id = uuid.uuid5(NS, f"payout:{i}")
            path = rng.choices(
                [
                    ["pending"],
                    ["onboarding", "transferred"],
                    ["failed", "pending", "transferred"],
                    ["pending_export", "exported"],
                    ["onboarding"],
                ],
                [15, 35, 10, 30, 10],
            )[0]
            session.execute(
                text(
                    "INSERT INTO payouts (id, firm_id, claim_id, amount_cents, method, status) "
                    "VALUES (:id, :f, :c, 4999, :m, :s)"
                ),
                {
                    "id": payout_id,
                    "f": FIRM,
                    "c": claim_id,
                    "m": "check_export" if "exported" in path else "stripe_connect",
                    "s": path[-1],
                },
            )
            stats["payouts"] += 1
            at = submitted
            for step in path:
                at = at + timedelta(hours=rng.randint(1, 24 * 20))
                session.execute(
                    text(
                        "INSERT INTO audit_log (firm_id, actor, action, target_type, target_id, before, after, at) "
                        "VALUES (:f, 'system', :a, 'payout', :t, NULL, CAST(:after AS jsonb), :at)"
                    ),
                    {
                        "f": FIRM,
                        "a": f"payout_{step}",
                        "t": str(payout_id),
                        "after": json.dumps({"status": step}),
                        "at": at,
                    },
                )
                if step in ("transferred", "exported"):
                    stats["delivered_events"] += 1
    return stats


def run(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or internal_engine()
    scenario = build_scenario(engine)
    months: list[dict[str, Any]] = []
    with Session(engine) as session:
        frames = export(session, FIRM, PROGRAM)
        for month in MONTHS:
            period = Period.parse(month)
            payload = build_payload(session, FIRM, PROGRAM, period)
            recounted = recount(frames, period)
            diffs = reconcile(payload["boxes"], recounted)
            months.append(
                {
                    "period": month,
                    "report": {k: payload["boxes"][k] for k in COMPUTED},
                    "recount": recounted,
                    "diffs": diffs,
                }
            )
    passed = all(not m["diffs"] for m in months)
    return {
        "scenario": scenario,
        "months": months,
        "passed": passed,
        "discrepancies": sum(len(m["diffs"]) for m in months),
    }


def render(result: dict[str, Any]) -> str:
    lines = [
        "# Reporting eval: MPR reconciliation",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`. Scenario: "
        f"{result['scenario']['claims']} claims, {result['scenario']['payouts']} payouts, "
        f"{result['scenario']['delivered_events']} delivered refunds across August to October 2026.",
        "",
        "The SQL report (mpr/generate.py) against the independent Polars recount over row exports (mpr/recount.py), "
        "zero tolerance.",
        "",
        "| period | corrections (report / recount) | cumulative (report / recount) "
        "| email contacts (report / recount) | discrepancies |",
        "|---|---|---|---|---|",
    ]
    for m in result["months"]:
        r, c = m["report"], m["recount"]
        lines.append(
            f"| {m['period']} | {r['corrections_this_period']} / {c['corrections_this_period']} | "
            f"{r['cumulative_corrections']} / {c['cumulative_corrections']} | "
            f"{r['contacts_email']} / {c['contacts_email']} | "
            f"{len(m['diffs'])} |"
        )
    lines += [
        "",
        f"**Reconciliation: {'PASS' if result['passed'] else 'FAIL'}** ({result['discrepancies']} discrepancies).",
    ]
    return "\n".join(lines) + "\n"


def main(results_dir: Path = RESULTS, engine: Engine | None = None) -> dict[str, Any]:
    result = run(engine)
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "reporting.md").write_text(render(result), encoding="utf-8")
    (results_dir / "reporting.json").write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    out = main()
    print(json.dumps({"passed": out["passed"], "discrepancies": out["discrepancies"]}))
    sys.exit(0 if out["passed"] else 1)
