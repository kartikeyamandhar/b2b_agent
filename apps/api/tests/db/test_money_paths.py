"""Money-path guarantees from the red team on the completion plan (decisions log 2026-10-18), on the test database:
export batches are paid once under concurrency, an over-limit firm never blocks another, crashed runs are recovered,
a reviewer's acknowledgment stops a reopen, an automated re-approval never releases a held payout, and a portal
answer that lost its race is refused instead of dropped."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops import portal
from recallops.billing.interface import Action, Entitlement
from recallops.claims.effects import DbEffects, reopen_duplicates
from recallops.claims.review import submit_action
from recallops.claims.worker import Worker
from recallops.db.session import tenant_session
from recallops.models import Claim, RecallProgram
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.export import export_pending
from recallops.payouts.fake import FakeLedger
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from tests import fakes

pytestmark = pytest.mark.db


def _firm(engine: Engine) -> tuple[FirmContext, UUID]:
    firm, program = uuid.uuid4(), uuid.uuid4()
    rules = fakes.rules()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'Money Co')"), {"f": firm})
        conn.execute(
            text(
                "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules, "
                "remedy_rules) VALUES (:p, :f, 'open', 10000, CAST(:i AS jsonb), CAST(:r AS jsonb))"
            ),
            {"p": program, "f": firm, "i": rules.identification.model_dump_json(), "r": rules.remedy.model_dump_json()},
        )
    return FirmContext(firm_id=firm, user_id="tester", role="admin"), program


def _claim(engine: Engine, ctx: FirmContext, program: UUID, status: str, **extra: Any) -> UUID:
    claim = uuid.uuid4()
    claimant = extra.pop("claimant", {"name": "Pat Doe", "address_line1": f"{claim.hex[:6]} Main St"})
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claims (id, firm_id, program_id, token_hash, status, claimant, model_claimed, "
                "retailer_claimed, refund_cents, submitted_at, decided_by, decision, attempts) VALUES (:c, :f, :p, "
                ":t, :s, CAST(:who AS jsonb), 'TL1302Z', 'Amazon.com', 4999, now() - make_interval(secs => :age), "
                ":by, CAST(:d AS jsonb), :a)"
            ),
            {
                "c": claim,
                "f": ctx.firm_id,
                "p": program,
                "t": uuid.uuid4().hex,
                "s": status,
                "who": json.dumps(claimant),
                "age": extra.get("age", 0),
                "by": extra.get("decided_by"),
                "d": json.dumps(extra.get("decision")) if extra.get("decision") is not None else None,
                "a": extra.get("attempts", 0),
            },
        )
    return claim


def _status(engine: Engine, table: str, column: str, value: UUID) -> str | None:
    with engine.connect() as conn:
        found = conn.execute(text(f"SELECT status FROM {table} WHERE {column} = :v"), {"v": value}).scalar()
    return None if found is None else str(found)


def test_two_export_runs_at_once_pay_every_payout_exactly_once(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    ledger = DbPayoutLedger(clean_db)
    claims = [_claim(clean_db, ctx, program, "approved") for _ in range(20)]
    for c in claims:
        ledger.create_pending(ctx, c, 4999, method="check_export")
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: export_pending(clean_db), range(2)))
    assert sum(batch["rows"] for run in runs for batch in run) == 20
    with clean_db.connect() as conn:
        exported = conn.execute(
            text("SELECT count(*), count(DISTINCT export_batch) FROM payouts WHERE status = 'exported'")
        ).one()
        events = conn.execute(
            text("SELECT target_id, count(*) FROM audit_log WHERE action = 'payout_exported' GROUP BY target_id")
        ).all()
        paid = conn.execute(text("SELECT count(*) FROM claims WHERE status = 'paid'")).scalar_one()
        stored = conn.execute(text("SELECT sum(row_count) FROM export_batches")).scalar_one()
    assert exported[0] == 20 and 1 <= exported[1] <= 2
    assert len(events) == 20 and all(n == 1 for _, n in events)  # one delivery event per payout: the MPR counts these
    assert paid == 20 and stored == 20


def test_a_payout_whose_claim_left_approved_is_never_exported(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    ledger = DbPayoutLedger(clean_db)
    claim = _claim(clean_db, ctx, program, "approved")
    ledger.create_pending(ctx, claim, 4999, method="check_export")
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
    assert export_pending(clean_db) == []
    assert _status(clean_db, "payouts", "claim_id", claim) == "pending_export"


class OneFirmOverLimit:
    def __init__(self, blocked: UUID) -> None:
        self.blocked = blocked

    def check_entitlement(self, ctx: FirmContext, action: Action) -> Entitlement:
        return Entitlement(allowed=ctx.firm_id != self.blocked, reason="over the free plan's limit")

    def record_usage(self, ctx: FirmContext, claims_adjudicated: int, model_cost_usd: Decimal) -> None:
        return None


def _rules_only_worker(engine: Engine, billing: Any = None) -> Worker:
    settings = get_settings().model_copy(update={"adjudicator": "rules_only", "worker_poll_s": 30.0})
    llm, _ = fakes.client([])
    return Worker(engine=engine, settings=settings, llm=llm, billing=billing, ledger=FakeLedger(), worker_id="t")


def test_a_firm_over_its_limit_does_not_block_another_firms_claims(clean_db: Engine) -> None:
    blocked, p1 = _firm(clean_db)
    free, p2 = _firm(clean_db)
    stuck = [_claim(clean_db, blocked, p1, "submitted", age=600 + i) for i in range(5)]
    waiting = _claim(clean_db, free, p2, "submitted", age=1)
    worker = _rules_only_worker(clean_db, OneFirmOverLimit(blocked.firm_id))
    assert worker.tick() is True  # the over-limit firm's oldest claim: skipped, firm blocked for one interval
    assert worker.tick() is True  # the other firm's claim is decided in the same loop
    assert _status(clean_db, "claims", "id", waiting) != "submitted"
    assert all(_status(clean_db, "claims", "id", c) == "submitted" for c in stuck)
    with clean_db.connect() as conn:
        attempts = conn.execute(text("SELECT max(attempts) FROM claims WHERE id = ANY(:c)"), {"c": stuck}).scalar()
    assert attempts == 0  # a skipped lease does not count as a failed attempt


def _run(engine: Engine, ctx: FirmContext, program: UUID, claim: UUID, status: str = "running") -> UUID:
    run = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, firm_id, claim_id, program_id, graph, status) "
                "VALUES (:r, :f, :c, :p, 'adjudicate', :s)"
            ),
            {"r": run, "f": ctx.firm_id, "c": claim, "p": program, "s": status},
        )
    return run


def test_runs_left_running_by_a_crash_are_recovered_from_the_claims_status(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    held = _run(clean_db, ctx, program, _claim(clean_db, ctx, program, "flagged"))
    decided = _run(clean_db, ctx, program, _claim(clean_db, ctx, program, "approved"))
    in_flight = _run(clean_db, ctx, program, _claim(clean_db, ctx, program, "submitted"))
    worker = _rules_only_worker(clean_db)
    assert worker.recover_orphans() == 2
    assert _status(clean_db, "agent_runs", "id", held) == "waiting_human"
    assert _status(clean_db, "agent_runs", "id", decided) == "done"
    assert _status(clean_db, "agent_runs", "id", in_flight) == "running"  # its claim will be re-leased
    assert worker.recover_orphans() == 0


def test_an_exhausted_claim_closes_its_run_and_the_review_applies_directly(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _claim(clean_db, ctx, program, "submitted", attempts=get_settings().worker_max_attempts)
    run = _run(clean_db, ctx, program, claim)
    worker = _rules_only_worker(clean_db)
    assert worker.tick() is True
    assert _status(clean_db, "claims", "id", claim) == "capped"
    assert _status(clean_db, "agent_runs", "id", run) == "failed"
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Claim, claim)
        assert row is not None
        how = submit_action(
            session, clean_db, ctx, row, "resume_override", "deny", None, FakeLedger(), OneFirmOverLimit(uuid.uuid4())
        )
    assert how == "applied" and _status(clean_db, "claims", "id", claim) == "denied"


def test_a_reviewers_acknowledgment_stops_a_second_reopen(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    later = uuid.uuid4()
    acked = _claim(
        clean_db,
        ctx,
        program,
        "approved",
        decided_by="dev-reviewer",
        decision={"outcome": "approve", "duplicates_ack": [str(later)]},
    )
    fresh = _claim(clean_db, ctx, program, "approved", decided_by="agent", decision={"outcome": "approve"})
    with tenant_session(ctx, clean_db) as session:
        acted = reopen_duplicates(session, ctx, later, [acked, fresh], "agent")
    assert acted["reopened"] == [str(fresh)]
    assert _status(clean_db, "claims", "id", acked) == "approved"
    assert _status(clean_db, "claims", "id", fresh) == "submitted"


def test_an_automated_reapproval_never_releases_a_held_payout(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _claim(clean_db, ctx, program, "submitted")
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO payouts (firm_id, claim_id, amount_cents, method, status, last_error) "
                "VALUES (:f, :c, 4999, 'check_export', 'held', 'duplicate_found_later')"
            ),
            {"f": ctx.firm_id, "c": claim},
        )
    effects = DbEffects(clean_db, ctx, claim, 4999, DbPayoutLedger(clean_db), OneFirmOverLimit(uuid.uuid4()))
    effects.apply_from({"outcome": "approve", "decided_by": "agent"}, ("submitted",))
    assert _status(clean_db, "claims", "id", claim) == "under_review"
    assert _status(clean_db, "payouts", "claim_id", claim) == "held"
    effects.apply_from(
        {"outcome": "approve", "decided_by": "dev-reviewer", "review_action": "resume_override"}, ("under_review",)
    )
    assert _status(clean_db, "claims", "id", claim) == "approved"
    assert _status(clean_db, "payouts", "claim_id", claim) == "pending_export"  # a person released it


def test_a_portal_answer_that_lost_its_race_is_refused_not_dropped(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _claim(clean_db, ctx, program, "needs_info")
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Claim, claim)
        prog = session.get(RecallProgram, program)
        assert row is not None and prog is not None
        pc = portal.PortalClaim(claim=row, program=prog, title="t")
        session.execute(text("UPDATE claims SET status = 'submitted' WHERE id = :c"), {"c": claim})  # reopened
        with pytest.raises(portal.NotEditable):
            portal.answer(session, pc, "here is the photo")
