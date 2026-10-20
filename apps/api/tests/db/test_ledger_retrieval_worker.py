"""Payout state machine, retrieval over document_chunks, and the worker's failure paths (test database)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops.claims.worker import Worker
from recallops.db.session import tenant_session
from recallops.payouts.db_ledger import DbPayoutLedger, IllegalTransition, NotPayable, PayoutNotFound, hold_for_claim
from recallops.retrieval.documents import chunk, program_documents
from recallops.retrieval.embed import HashNgramEmbedder
from recallops.retrieval.search import PgRetriever, index_program
from recallops.settings import get_settings
from recallops.tenancy import FirmContext
from tests import fakes

pytestmark = pytest.mark.db


def _firm_program_claim(engine: Engine, status: str = "approved") -> tuple[FirmContext, UUID, UUID]:
    firm, program, claim = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rules = fakes.rules()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'Ledger Co')"), {"f": firm})
        conn.execute(
            text(
                "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules, "
                "remedy_rules) VALUES (:p, :f, 'open', 10000, CAST(:i AS jsonb), CAST(:r AS jsonb))"
            ),
            {"p": program, "f": firm, "i": rules.identification.model_dump_json(), "r": rules.remedy.model_dump_json()},
        )
        conn.execute(
            text(
                "INSERT INTO claims (id, firm_id, program_id, token_hash, status, model_claimed, retailer_claimed, "
                "refund_cents, submitted_at) VALUES (:c, :f, :p, :t, :s, 'TL1302Z', 'Amazon.com', 4999, now())"
            ),
            {"c": claim, "f": firm, "p": program, "t": uuid.uuid4().hex, "s": status},
        )
    return FirmContext(firm_id=firm, user_id="tester", role="admin"), program, claim


def _claim_status(engine: Engine, claim: UUID) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT status FROM claims WHERE id = :c"), {"c": claim}).scalar_one())


def test_payout_state_machine(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    first = ledger.create_pending(ctx, claim, 4999)
    again = ledger.create_pending(ctx, claim, 4999)
    assert first.payout_id == again.payout_id and first.status == "pending"
    assert ledger.mark_onboarding(ctx, first.payout_id, "acct_test").status == "onboarding"
    assert ledger.mark_failed(ctx, first.payout_id, "account restricted").status == "failed"
    assert ledger.get(ctx, first.payout_id).attempts == 1
    assert ledger.claim_for_transfer(ctx, first.payout_id).status == "transferring"
    with pytest.raises(NotPayable):
        ledger.claim_for_transfer(ctx, first.payout_id)  # already claimed: no second transfer
    assert ledger.mark_transferred(ctx, first.payout_id, "tr_1").status == "transferred"
    assert ledger.mark_transferred(ctx, first.payout_id, "tr_1").status == "transferred"  # replayed event
    assert _claim_status(clean_db, claim) == "paid"
    with pytest.raises(IllegalTransition):
        ledger.mark_onboarding(ctx, first.payout_id, "acct_again")
    with pytest.raises(PayoutNotFound):
        ledger.get(ctx, uuid.uuid4())
    with clean_db.connect() as conn:
        actions = (
            conn.execute(text("SELECT action FROM audit_log WHERE target_type = 'payout' ORDER BY at")).scalars().all()
        )
        account = conn.execute(
            text("SELECT connect_account_id FROM payouts WHERE id = :p"), {"p": first.payout_id}
        ).scalar_one()
    assert actions[0] == "payout_created" and actions.count("payout_transferred") == 1
    assert account == "acct_test"


def test_three_transfer_failures_fall_back_to_the_export_batch(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999)
    for _ in range(2):
        ledger.claim_for_transfer(ctx, ref.payout_id)
        assert ledger.mark_failed(ctx, ref.payout_id, "rate_limit_before_execution").status == "failed"
    ledger.claim_for_transfer(ctx, ref.payout_id)
    final = ledger.mark_failed(ctx, ref.payout_id, "rate_limit_before_execution")
    assert final.status == "pending_export"
    batch = str(uuid.uuid4())
    with clean_db.begin() as conn:  # mark_exported needs its batch row (export.py writes both in one transaction)
        conn.execute(
            text(
                "INSERT INTO export_batches (id, firm_id, created_by, row_count, total_cents, sha256, csv) "
                "VALUES (:b, :f, 'test', 1, 4999, 'x', 'x'::bytea)"
            ),
            {"b": batch, "f": ctx.firm_id},
        )
    assert ledger.mark_exported(ctx, ref.payout_id, batch).status == "exported"
    assert _claim_status(clean_db, claim) == "paid"
    ctx2, _, claim2 = _firm_program_claim(clean_db)
    check = ledger.create_pending(ctx2, claim2, 100, method="check_export")
    assert check.status == "pending_export"


def test_a_hold_stops_money_and_only_a_reapproval_releases_it(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999, method="check_export")
    with tenant_session(ctx, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
        assert hold_for_claim(session, ctx, claim, "duplicate_found_later") == "held"
        assert hold_for_claim(session, ctx, claim, "again") == "held"  # idempotent
    with pytest.raises(NotPayable):
        ledger.claim_for_transfer(ctx, ref.payout_id)
    with pytest.raises(IllegalTransition):
        ledger.to_export(ctx, ref.payout_id)  # the portal cannot release a hold
    with tenant_session(ctx, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'approved' WHERE id = :c"), {"c": claim})
        released = ledger.create_pending(ctx, claim, 4999, method="check_export", session=session)
    assert released.payout_id == ref.payout_id and released.status == "pending_export"


def test_a_transfer_in_flight_records_the_hold_and_the_real_result(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.claim_for_transfer(ctx, ref.payout_id)  # component 3 is about to call Transfer.create
    with tenant_session(ctx, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim})
        assert hold_for_claim(session, ctx, claim, "reviewer override") == "transferring"
    assert ledger.mark_failed(ctx, ref.payout_id, "insufficient balance").status == "held"
    ctx2, _, claim2 = _firm_program_claim(clean_db)
    ref2 = ledger.create_pending(ctx2, claim2, 4999)
    ledger.claim_for_transfer(ctx2, ref2.payout_id)
    with tenant_session(ctx2, clean_db) as session:
        session.execute(text("UPDATE claims SET status = 'flagged' WHERE id = :c"), {"c": claim2})
        hold_for_claim(session, ctx2, claim2, "duplicate_found_later")
    assert ledger.mark_transferred(ctx2, ref2.payout_id, "tr_9").status == "transferred"  # money moved: record it
    with clean_db.connect() as conn:
        alerts = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'payout_paid_despite_hold' AND target_id = :p"),
            {"p": str(ref2.payout_id)},
        ).scalar_one()
    assert alerts == 1 and _claim_status(clean_db, claim2) == "flagged"


def test_webhook_guard_and_ledger_delivery_roll_back_and_commit_together(
    clean_db: Engine,
    app_engine_test: Engine,
) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(app_engine_test)
    ref = ledger.create_pending(ctx, claim, 4999)
    ledger.claim_for_transfer(ctx, ref.payout_id)
    event = "evt_" + uuid.uuid4().hex
    with pytest.raises(RuntimeError), tenant_session(ctx, app_engine_test) as session:
        assert session.scalar(
            text("SELECT recallops_record_stripe_event(:id, 'transfer.created', '{}'::jsonb)"), {"id": event}
        )
        ledger.mark_transferred(ctx, ref.payout_id, "tr_atomic", session=session)
        raise RuntimeError("crash before webhook commit")
    assert ledger.get(ctx, ref.payout_id).ref.status == "transferring"
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM stripe_events WHERE id = :id"), {"id": event}) == 0
    with tenant_session(ctx, app_engine_test) as session:
        assert session.scalar(
            text("SELECT recallops_record_stripe_event(:id, 'transfer.created', '{}'::jsonb)"), {"id": event}
        )
        ledger.mark_transferred(ctx, ref.payout_id, "tr_atomic", session=session)
        session.execute(text("SELECT recallops_mark_stripe_event_processed(:id)"), {"id": event})
    with tenant_session(ctx, app_engine_test) as session:
        assert not session.scalar(
            text("SELECT recallops_record_stripe_event(:id, 'transfer.created', '{}'::jsonb)"), {"id": event}
        )
    assert ledger.get(ctx, ref.payout_id).ref.status == "transferred"
    assert _claim_status(clean_db, claim) == "paid"


def test_retrieval_keyword_vector_and_none(clean_db: Engine) -> None:
    ctx, program, _ = _firm_program_claim(clean_db)
    from sqlalchemy.orm import Session

    embedder = HashNgramEmbedder(1024)
    docs = program_documents(
        "Scotch Thermal Laminators", "TITLE:\nLaminators\n\nDESCRIPTION:\nModel TL1302Z.", fakes.rules(), 10_000
    )
    with Session(clean_db) as session, session.begin():
        n = index_program(session, ctx.firm_id, program, docs, embedder)
    assert n >= 3
    with Session(clean_db) as session:
        full = PgRetriever(session, ctx.firm_id, embedder, "full").search(
            program, "is a receipt required for secondhand owners"
        )
        keyword = PgRetriever(session, ctx.firm_id, embedder, "keyword").search(program, "TL1302Z")
        none = PgRetriever(session, ctx.firm_id, embedder, "none").search(program, "receipt")
        again = PgRetriever(session, ctx.firm_id, embedder, "full").search(
            program, "is a receipt required for secondhand owners"
        )
    assert full and all(h.ref.split("#")[0] in ("faq", "notice") for h in full)
    assert any("secondhand" in h.text for h in full)
    assert keyword and all("TL1302Z" in h.text for h in keyword)
    assert "notice#0" in [h.ref for h in keyword]
    assert none == []
    assert [h.ref for h in full] == [h.ref for h in again]  # deterministic order
    assert chunk("a. " * 400, 100) and all(len(c) <= 100 for c in chunk("a. " * 400, 100))


def test_worker_holds_a_claim_after_repeated_failures(clean_db: Engine) -> None:
    _ctx, _, claim = _firm_program_claim(clean_db, status="submitted")
    llm, _transport = fakes.client([])  # every model call fails: the scripted transport is empty
    worker = Worker(engine=clean_db, llm=llm, worker_id="flaky")
    for _ in range(get_settings().worker_max_attempts):
        assert worker.tick() is True
        with clean_db.begin() as conn:
            conn.execute(
                text("UPDATE claims SET lease_until = now() - interval '1 second' WHERE id = :c"), {"c": claim}
            )
    assert worker.tick() is True  # attempts exhausted: held for a person
    with clean_db.connect() as conn:
        status, attempts = conn.execute(text("SELECT status, attempts FROM claims WHERE id = :c"), {"c": claim}).one()
    assert status == "capped" and attempts == get_settings().worker_max_attempts + 1
    assert worker.tick() is False


def test_worker_rules_only_mode_decides_offline(clean_db: Engine) -> None:
    from recallops.settings import Settings

    _ctx, _, claim = _firm_program_claim(clean_db, status="submitted")
    settings = Settings(adjudicator="rules_only")
    worker = Worker(engine=clean_db, settings=settings, llm=fakes.client([])[0], worker_id="rules")
    assert worker.tick() is True
    with clean_db.connect() as conn:
        status, by = conn.execute(text("SELECT status, decided_by FROM claims WHERE id = :c"), {"c": claim}).one()
        graph = conn.execute(text("SELECT graph FROM agent_runs WHERE claim_id = :c"), {"c": claim}).scalar_one()
    assert by == "rules_only" and graph == "rules_only"
    assert status == "needs_info"  # no photos: the destruction photo is missing
