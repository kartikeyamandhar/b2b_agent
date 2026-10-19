"""Fixes from the final review before merging to main (decisions log 2026-10-19), on the test database."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops import portal
from recallops.claims.review import submit_action
from recallops.db.session import tenant_session
from recallops.models import Claim, RecallProgram
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.fake import FakeLedger
from recallops.tenancy import FirmContext
from tests.db.test_money_paths import OneFirmOverLimit, _claim, _firm, _rules_only_worker, _run

pytestmark = pytest.mark.db


def _approved_with_payout(engine: Engine, ctx: FirmContext, program: UUID, home: str) -> UUID:
    claim = _claim(engine, ctx, program, "approved", claimant={"name": "Pat", "address_line1": home})
    DbPayoutLedger(engine).create_pending(ctx, claim, 4999)
    return claim


def _choose(engine: Engine, ctx: FirmContext, claim_id: UUID, mailing: str | None) -> str:
    with tenant_session(ctx, engine) as session:
        row = session.get(Claim, claim_id)
        prog = session.get(RecallProgram, row.program_id) if row else None
        assert row is not None and prog is not None
        return portal.choose_check_payout(session, portal.PortalClaim(row, prog, "t"), mailing, "test-secret")


def _status(engine: Engine, claim_id: UUID) -> tuple[str, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT c.status, p.status FROM claims c LEFT JOIN payouts p ON p.claim_id = c.id WHERE c.id = :c"),
            {"c": claim_id},
        ).one()
    return str(row[0]), row[1]


def test_a_home_address_equal_to_another_claims_mailing_address_is_held(clean_db: Engine) -> None:
    """The redirect pattern in the other order: A sets mailing address X first; B lives at X and picks a check
    without a mailing address. B's destination (its home) matches A's mailing address, so both go to a person."""
    ctx, program = _firm(clean_db)
    a = _approved_with_payout(clean_db, ctx, program, "1 Oak St")
    assert _choose(clean_db, ctx, a, "77 Redirect Ln") == "pending_export"  # nobody uses it yet
    b = _approved_with_payout(clean_db, ctx, program, "77 Redirect Ln")  # filed later, living at A's mailing address
    assert _choose(clean_db, ctx, b, None) == "held"
    assert _status(clean_db, b) == ("under_review", "held")
    assert _status(clean_db, a) == ("under_review", "held")


def test_an_address_with_no_ascii_letters_or_digits_is_refused_and_matches_nothing(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claims = [_approved_with_payout(clean_db, ctx, program, f"{i} Elm St") for i in range(3)]
    with pytest.raises(portal.PayoutConflict, match="letters or digits"):
        _choose(clean_db, ctx, claims[0], "東京都渋谷区")
    assert all(_status(clean_db, c) == ("approved", "pending") for c in claims)  # nobody was frozen


def test_two_concurrent_choices_of_the_same_new_address_both_hold(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    a = _approved_with_payout(clean_db, ctx, program, "1 Birch St")
    b = _approved_with_payout(clean_db, ctx, program, "2 Birch St")
    barrier = threading.Barrier(2)
    results: dict[UUID, str] = {}

    def choose(claim: UUID) -> None:
        barrier.wait()
        results[claim] = _choose(clean_db, ctx, claim, "PO Box 9 Reno NV")

    threads = [threading.Thread(target=choose, args=(c,)) for c in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    # The program lock serializes them: the second sees the first's address and holds both.
    assert sorted(results.values()) == ["held", "held"] or (
        _status(clean_db, a)[0] == "under_review" and _status(clean_db, b)[0] == "under_review"
    )
    assert _status(clean_db, a) == ("under_review", "held") and _status(clean_db, b) == ("under_review", "held")


def test_a_reviewer_action_on_a_post_decision_hold_applies_directly(clean_db: Engine) -> None:
    """A shared-mailing hold has no interrupt waiting: the run is done, so the action must not be queued as a
    resume that LangGraph would ignore."""
    ctx, program = _firm(clean_db)
    claim = _claim(
        clean_db,
        ctx,
        program,
        "under_review",
        decided_by="agent",
        decision={"outcome": "approve", "hold_reason": "shared_mailing_address"},
    )
    _run(clean_db, ctx, program, claim, status="waiting_human")
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Claim, claim)
        assert row is not None
        how = submit_action(
            session, clean_db, ctx, row, "resume_override", "deny", None, FakeLedger(), OneFirmOverLimit(uuid.uuid4())
        )
    assert how == "applied" and _status(clean_db, claim)[0] == "denied"


def test_an_orphan_run_of_a_post_decision_hold_is_recovered_as_done(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    held = _claim(
        clean_db,
        ctx,
        program,
        "under_review",
        decided_by="agent",
        decision={"outcome": "approve", "hold_reason": "payout_held_needs_review"},
    )
    flagged = _claim(clean_db, ctx, program, "flagged", decision={"outcome": "flag", "hold_reason": "flagged"})
    r1, r2 = _run(clean_db, ctx, program, held), _run(clean_db, ctx, program, flagged)
    assert _rules_only_worker(clean_db).recover_orphans() == 2
    with clean_db.connect() as conn:
        got: dict[UUID, str] = {
            row.id: row.status
            for row in conn.execute(text("SELECT id, status FROM agent_runs WHERE id = ANY(:r)"), {"r": [r1, r2]})
        }
    assert got == {r1: "done", r2: "waiting_human"}


def test_a_failing_resume_is_parked_after_the_attempt_limit(clean_db: Engine, monkeypatch: Any) -> None:
    from recallops.claims.worker import Worker

    ctx, program = _firm(clean_db)
    claim = _claim(clean_db, ctx, program, "flagged")
    run = _run(clean_db, ctx, program, claim, status="waiting_human")
    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE agent_runs SET resume_payload = CAST(:p AS jsonb) WHERE id = :r"),
            {"p": json.dumps({"action": "resume_override", "outcome": "deny"}), "r": run},
        )
    worker = _rules_only_worker(clean_db)

    def boom(*_: Any) -> str:
        raise RuntimeError("checkpoint unreadable")

    monkeypatch.setattr(Worker, "resume", boom)
    for _ in range(worker.settings.worker_max_attempts):
        assert worker.tick() is True  # contained: the worker keeps running
    with clean_db.connect() as conn:
        row = conn.execute(
            text("SELECT status, resume_payload, outcome FROM agent_runs WHERE id = :r"), {"r": run}
        ).one()
    assert row.status == "failed" and row.resume_payload is None
    assert row.outcome["exhausted"] and row.outcome["unapplied_review"]["outcome"] == "deny"


def test_a_reapproval_clears_a_requested_hold_and_a_delivered_refund_marks_paid(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    ledger = DbPayoutLedger(clean_db)
    claim = _claim(clean_db, ctx, program, "approved")
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.claim_for_transfer(ctx, ref.payout_id)
    with clean_db.begin() as conn:
        conn.execute(text("UPDATE payouts SET hold_requested = true WHERE id = :p"), {"p": ref.payout_id})
    ledger.create_pending(ctx, claim, 4999)  # a person approved the claim again
    with clean_db.connect() as conn:
        cleared = conn.execute(text("SELECT hold_requested FROM payouts WHERE id = :p"), {"p": ref.payout_id}).scalar()
    assert cleared is False
    ledger.mark_transferred(ctx, ref.payout_id, "tr_ok")
    assert _status(clean_db, claim) == ("paid", "transferred")
    other = _claim(clean_db, ctx, program, "flagged")
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO payouts (firm_id, claim_id, amount_cents, method, status) "
                "VALUES (:f, :c, 4999, 'stripe_connect', 'transferred')"
            ),
            {"f": ctx.firm_id, "c": other},
        )
        conn.execute(text("UPDATE claims SET status = 'approved' WHERE id = :c"), {"c": other})
    ledger.create_pending(ctx, other, 4999)  # re-approved after the money already moved
    assert _status(clean_db, other) == ("paid", "transferred")


def test_synth_program_mode_loads_submitted_claims(clean_db: Engine) -> None:
    from recallops import seed
    from recallops.synth.__main__ import main

    seeded = seed.run(clean_db, n=3)
    program = seeded["demo"]["program_id"]
    assert main(["--program", program, "--n", "4", "--seed", "11"]) == 0
    with clean_db.connect() as conn:
        submitted = conn.execute(
            text("SELECT count(*) FROM claims WHERE program_id = :p AND status = 'submitted'"), {"p": program}
        ).scalar_one()
    assert submitted == 4


def test_reopening_a_claim_closes_a_run_a_crash_left_running(clean_db: Engine) -> None:
    from recallops.claims.effects import reopen_duplicates

    ctx, program = _firm(clean_db)
    partner = _claim(clean_db, ctx, program, "approved", decided_by="agent", decision={"outcome": "approve"})
    stale = _run(clean_db, ctx, program, partner)  # decided, but the run was never marked done
    with tenant_session(ctx, clean_db) as session:
        reopen_duplicates(session, ctx, uuid.uuid4(), [partner], "agent")
    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT status, outcome FROM agent_runs WHERE id = :r"), {"r": stale}).one()
    assert row.status == "done" and row.outcome["superseded"] == "reopened"
    assert _status(clean_db, partner)[0] == "submitted"
