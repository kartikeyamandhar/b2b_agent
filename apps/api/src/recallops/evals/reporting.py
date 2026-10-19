"""Reporting eval (01-backend.md sections 8 and 10): MPR reconciliation on a program in mixed states.

python -m recallops.evals.reporting     (make eval runs it) writes evals/results/reporting.md and reporting.json

Builds a deterministic scenario under an "MPR Eval" firm and checks three counts against each other for every month:
the SQL report (mpr/generate.py), the independent Polars recount over row exports (mpr/recount.py), and the truth the
scenario builder kept while it wrote the rows. Zero tolerance: any difference fails the eval and the deploy gate.

The scenario (red team, decisions log 2026-10-18) covers what a careless definition would get wrong: 500 refund
claims whose payouts move through every ledger state, held payouts that never deliver, a replayed delivery event
(counted once), delivery events on the last microsecond of a month and the first instant of the next, repair and
replacement claims a reviewer marked delivered, claims first submitted in one month and answered in the next (one
contact, in the first month), drafts, a second program of the same firm whose deliveries must not count, and manual
boxes including a contacts override (noted, never a discrepancy).
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from recallops.db.engine import internal_engine
from recallops.mpr.generate import Period, build_payload
from recallops.mpr.recount import export, reconcile, recount
from recallops.settings import REPO_ROOT

RESULTS = REPO_ROOT / "evals" / "results"
FIRM = uuid.UUID("00000000-0000-4000-8000-0000000003a1")
PROGRAM = uuid.UUID("00000000-0000-4000-8000-0000000003b1")
OTHER_PROGRAM = uuid.UUID("00000000-0000-4000-8000-0000000003b2")
NS = uuid.UUID("3a1a0000-0000-4000-8000-000000000500")
MONTHS = ("2026-08", "2026-09", "2026-10")
UNITS = 53634
N = 500
SEED = 500
MANUAL = {"2026-09": {"units_with_retailers": 1000, "incidents_after": 2}, "2026-10": {"contacts_email": 999}}
# Pre-registered composition (asserted by tests/db/test_reporting.py): what the scenario must contain.
EXPECTED_COMPOSITION = {
    "refund_claims": N,
    "remedy_claims": 40,
    "remedies_delivered": 25,
    "answered_next_month": 30,
    "boundary_events": 2,
    "replayed_events": 1,
    "other_program_deliveries": 5,
    "notices_sent": 46,
    "notice_drafts": 5,
    "other_program_notices": 2,
}


def _ts(rng: random.Random, start: date, days: int) -> datetime:
    return datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(
        seconds=rng.randint(0, days * 86400 - 1)
    )


def _month(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m")


class Truth:
    """What the builder wrote, counted as it wrote it: first delivery month per item, first contact per claim."""

    def __init__(self) -> None:
        self.delivered: dict[str, str] = {}
        self.contacts: Counter[str] = Counter()
        self.notified: dict[tuple[str, str], set[str]] = {}

    def notify(self, owner: str, channel: str, at: datetime) -> None:
        self.notified.setdefault((_month(at), channel), set()).add(owner)

    def deliver(self, item: str, at: datetime) -> None:
        month = _month(at)
        if item not in self.delivered or month < self.delivered[item]:
            self.delivered[item] = month

    def expected(self, period: str, manual: dict[str, Any]) -> dict[str, int]:
        this = sum(1 for m in self.delivered.values() if m == period)
        cumulative = sum(1 for m in self.delivered.values() if m <= period)
        held = sum(
            int(manual.get(k, 0)) for k in ("units_with_manufacturer", "units_with_distributor", "units_with_retailers")
        )
        return {
            "total_units_affected": UNITS,
            "corrections_this_period": this,
            "cumulative_corrections": cumulative,
            "units_with_consumers": max(0, UNITS - held - cumulative),
            "contacts_email": self.contacts[period],
            **{f"notified_{c}": len(self.notified.get((period, c), set())) for c in ("email", "mail", "phone_text")},
        }


def _insert_claim(session: Session, **values: Any) -> None:
    session.execute(
        text(
            "INSERT INTO claims (id, firm_id, program_id, token_hash, status, submitted_at, first_submitted_at, "
            "refund_cents, remedy) VALUES (:c, :f, :p, :t, :s, :at, :first, :refund, :remedy)"
        ),
        values,
    )


def _event(session: Session, target_type: str, target: uuid.UUID, action: str, at: datetime) -> None:
    session.execute(
        text(
            "INSERT INTO audit_log (firm_id, actor, action, target_type, target_id, before, after, at) "
            "VALUES (:f, 'system', :a, :tt, :t, NULL, CAST(:after AS jsonb), :at)"
        ),
        {
            "f": FIRM,
            "a": action,
            "tt": target_type,
            "t": str(target),
            "after": json.dumps({"action": action}),
            "at": at,
        },
    )


def build_scenario(engine: Engine) -> tuple[dict[str, int], Truth]:
    rng = random.Random(SEED)
    window = date(2026, 8, 1)
    truth = Truth()
    stats: Counter[str] = Counter()
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))  # reset tooling only
        for table in (
            "notifications",
            "registrations",
            "payouts",
            "export_batches",
            "audit_log",
            "claims",
            "recall_programs",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE firm_id = :f"), {"f": FIRM})
        session.execute(text("DELETE FROM firms WHERE id = :f"), {"f": FIRM})
        session.execute(text("INSERT INTO firms (id, name, plan) VALUES (:f, 'MPR Eval', 'growth')"), {"f": FIRM})
        for program in (PROGRAM, OTHER_PROGRAM):
            session.execute(
                text(
                    "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules) "
                    "VALUES (:p, :f, 'open', 10000, CAST(:i AS jsonb))"
                ),
                {"p": program, "f": FIRM, "i": json.dumps({"models": ["TL1302Z"], "units_affected": UNITS})},
            )

        for i in range(N):
            claim_id = uuid.uuid5(NS, f"claim:{i}")
            submitted = _ts(rng, window, 92)
            status = rng.choices(["approved", "denied", "needs_info", "flagged", "draft"], [55, 15, 12, 10, 8])[0]
            first = None if status == "draft" else submitted
            later = submitted
            if status != "draft" and stats["answered_next_month"] < 30 and submitted.month == 8:
                later = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=rng.randint(1, 600))  # answered later
                stats["answered_next_month"] += 1
            _insert_claim(
                session,
                c=claim_id,
                f=FIRM,
                p=PROGRAM,
                t=f"mpr-{i}",
                s=status,
                at=None if status == "draft" else later,
                first=first,
                refund=4999,
                remedy="refund",
            )
            stats["refund_claims"] += 1
            if first is not None:
                truth.contacts[_month(first)] += 1
            if status != "approved":
                continue
            payout_id = uuid.uuid5(NS, f"payout:{i}")
            path = rng.choices(
                [
                    ["pending"],
                    ["onboarding", "transferring", "transferred"],
                    ["transferring", "failed", "transferring", "transferred"],
                    ["pending_export", "exported"],
                    ["onboarding"],
                    ["pending_export", "held"],
                ],
                [12, 33, 10, 30, 8, 7],
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
                    "m": "check_export" if "pending_export" in path else "stripe_connect",
                    "s": path[-1],
                },
            )
            stats["payouts"] += 1
            at = submitted
            for step in path:
                at = at + timedelta(hours=rng.randint(1, 24 * 20))
                _event(session, "payout", payout_id, f"payout_{step}", at)
                if step in ("transferred", "exported"):
                    truth.deliver(f"payout:{payout_id}", at)
                    stats["delivered_events"] += 1
                    if stats["replayed_events"] == 0:  # a replayed delivery webhook: one more row, still one item
                        _event(session, "payout", payout_id, f"payout_{step}", at + timedelta(days=40))
                        stats["replayed_events"] += 1

        boundary = (
            datetime(2026, 8, 31, 23, 59, 59, 999999, tzinfo=UTC),
            datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC),
        )
        for j, at in enumerate(boundary):
            claim_id, payout_id = uuid.uuid5(NS, f"edge-claim:{j}"), uuid.uuid5(NS, f"edge-payout:{j}")
            first = datetime(2026, 8, 2, tzinfo=UTC)
            _insert_claim(
                session,
                c=claim_id,
                f=FIRM,
                p=PROGRAM,
                t=f"edge-{j}",
                s="paid",
                at=first,
                first=first,
                refund=4999,
                remedy="refund",
            )
            truth.contacts[_month(first)] += 1
            session.execute(
                text(
                    "INSERT INTO payouts (id, firm_id, claim_id, amount_cents, method, status) "
                    "VALUES (:id, :f, :c, 4999, 'stripe_connect', 'transferred')"
                ),
                {"id": payout_id, "f": FIRM, "c": claim_id},
            )
            _event(session, "payout", payout_id, "payout_transferred", at)
            truth.deliver(f"payout:{payout_id}", at)
            stats["boundary_events"] += 1

        for k in range(40):
            claim_id = uuid.uuid5(NS, f"remedy:{k}")
            first = _ts(rng, window, 60)
            delivered = k < 25
            _insert_claim(
                session,
                c=claim_id,
                f=FIRM,
                p=PROGRAM,
                t=f"remedy-{k}",
                s="closed" if delivered else "approved",
                at=first,
                first=first,
                refund=None,
                remedy="repair" if k % 2 else "replacement",
            )
            truth.contacts[_month(first)] += 1
            stats["remedy_claims"] += 1
            if delivered:
                at = first + timedelta(days=rng.randint(3, 25))
                _event(session, "claim", claim_id, "remedy_delivered", at)
                truth.deliver(f"claim:{claim_id}", at)
                stats["remedies_delivered"] += 1

        for m in range(5):  # another program's deliveries: never this program's corrections or contacts
            claim_id, payout_id = uuid.uuid5(NS, f"other-claim:{m}"), uuid.uuid5(NS, f"other-payout:{m}")
            first = _ts(rng, window, 60)
            _insert_claim(
                session,
                c=claim_id,
                f=FIRM,
                p=OTHER_PROGRAM,
                t=f"other-{m}",
                s="paid",
                at=first,
                first=first,
                refund=4999,
                remedy="refund",
            )
            session.execute(
                text(
                    "INSERT INTO payouts (id, firm_id, claim_id, amount_cents, method, status) "
                    "VALUES (:id, :f, :c, 4999, 'check_export', 'exported')"
                ),
                {"id": payout_id, "f": FIRM, "c": claim_id},
            )
            _event(session, "payout", payout_id, "payout_exported", first + timedelta(days=3))
            stats["other_program_deliveries"] += 1

        channels = ("email", "mail", "phone_text")
        for k in range(52):  # owners a campaign reached: sent in some month, a few drafts never sent
            program = PROGRAM if k < 50 else OTHER_PROGRAM
            reg = uuid.uuid5(NS, f"registration:{k}")
            session.execute(
                text(
                    "INSERT INTO registrations (id, firm_id, program_id, external_ref, model, matched) "
                    "VALUES (:r, :f, :p, :ref, 'TL1302Z', true)"
                ),
                {"r": reg, "f": FIRM, "p": program, "ref": f"reg-{k}"},
            )
            plans = [channels[k % 3]] + (["phone_text"] if k == 7 else [])  # one owner reached on two channels
            for channel in plans:
                sent = k < 45 or k == 7 or program == OTHER_PROGRAM
                at = _ts(rng, window, 92)
                session.execute(
                    text(
                        "INSERT INTO notifications (firm_id, program_id, registration_id, channel, status, subject, "
                        "body, drafted_at, sent_at) VALUES (:f, :p, :r, :c, :s, 'Safety recall', 'notice', :d, :at)"
                    ),
                    {
                        "f": FIRM,
                        "p": program,
                        "r": reg,
                        "c": channel,
                        "s": "sent" if sent else "draft",
                        "d": at,
                        "at": at if sent else None,
                    },
                )
                if program == OTHER_PROGRAM:
                    stats["other_program_notices"] += 1
                elif sent:
                    truth.notify(str(reg), channel, at)
                    stats["notices_sent"] += 1
                else:
                    stats["notice_drafts"] += 1
    return dict(stats), truth


def run(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or internal_engine()
    scenario, truth = build_scenario(engine)
    months: list[dict[str, Any]] = []
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as session, session.begin():
        frames = export(session, FIRM, PROGRAM)
        for month in MONTHS:
            period = Period.parse(month)
            manual = MANUAL.get(month, {})
            payload = build_payload(session, FIRM, PROGRAM, period, manual)
            recounted = recount(frames, month, manual)
            expected = truth.expected(month, manual)
            diffs = reconcile(payload["computed"], recounted) + [
                f"{k}: report {payload['computed'][k]} vs truth {v}"
                for k, v in expected.items()
                if payload["computed"][k] != v
            ]
            months.append(
                {
                    "period": month,
                    "report": payload["computed"],
                    "recount": recounted,
                    "truth": expected,
                    "boxes": payload["boxes"],
                    "manual": manual,
                    "diffs": diffs,
                }
            )
    passed = all(not m["diffs"] for m in months)
    return {
        "scenario": scenario,
        "composition_ok": all(scenario.get(k) == v for k, v in EXPECTED_COMPOSITION.items()),
        "months": months,
        "passed": passed,
        "discrepancies": sum(len(m["diffs"]) for m in months),
    }


def render(result: dict[str, Any]) -> str:
    sc = result["scenario"]
    lines = [
        "# Reporting eval: MPR reconciliation",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`.",
        "",
        f"Scenario: {sc['refund_claims']} refund claims ({sc['payouts']} payouts through every ledger state, held ones "
        f"included, {sc['replayed_events']} replayed delivery event), {sc['remedy_claims']} repair and replacement "
        f"claims ({sc['remedies_delivered']} marked delivered), {sc['answered_next_month']} claims answered the month "
        f"after their first submission, {sc['boundary_events']} deliveries on a month boundary instant, and "
        f"{sc['other_program_deliveries']} deliveries in another program of the same firm (must not count); "
        f"campaign notices: {sc['notices_sent']} sent, {sc['notice_drafts']} drafts never sent, and "
        f"{sc['other_program_notices']} in the other program.",
        "",
        "Three counts per computed box: the SQL report (mpr/generate.py), the independent Polars recount over row "
        "exports (mpr/recount.py), and the truth the scenario builder kept. Zero tolerance.",
        "",
        "| period | corrections (report / recount / truth) | cumulative | units with consumers | email contacts "
        "| notified email / mail / text | boxes entered by hand | discrepancies |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in result["months"]:
        r, c, t = m["report"], m["recount"], m["truth"]

        def triple(key: str, r: dict[str, Any] = r, c: dict[str, Any] = c, t: dict[str, Any] = t) -> str:
            return f"{r[key]} / {c[key]} / {t[key]}"

        lines.append(
            f"| {m['period']} | {triple('corrections_this_period')} | {triple('cumulative_corrections')} | "
            f"{triple('units_with_consumers')} | {triple('contacts_email')} | "
            f"{triple('notified_email')}; {triple('notified_mail')}; {triple('notified_phone_text')} | "
            f"{', '.join(f'{k}={v}' for k, v in m['manual'].items()) or 'none'} | {len(m['diffs'])} |"
        )
    lines += [
        "",
        "A contacts_email entered by hand replaces the box on the form and is noted in the report; reconciliation "
        "compares the computed value.",
        "",
        f"**Reconciliation: {'PASS' if result['passed'] else 'FAIL'}** ({result['discrepancies']} discrepancies; "
        f"scenario composition as pre-registered: {'yes' if result['composition_ok'] else 'NO'}).",
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
    sys.exit(0 if out["passed"] and out["composition_ok"] else 1)
