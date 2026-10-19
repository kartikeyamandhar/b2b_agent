"""Payout state machine, retrieval over document_chunks, and the worker's failure paths (test database)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops.claims.worker import Worker
from recallops.payouts.db_ledger import DbPayoutLedger, IllegalTransition, PayoutNotFound
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


def test_payout_state_machine(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    first = ledger.create_pending(ctx, claim, 4999)
    again = ledger.create_pending(ctx, claim, 4999)
    assert first.payout_id == again.payout_id and first.status == "pending"
    assert ledger.mark_onboarding(ctx, first.payout_id, "acct_test").status == "onboarding"
    assert ledger.mark_failed(ctx, first.payout_id, "card declined").status == "failed"
    assert ledger.get(ctx, first.payout_id).attempts == 1
    assert ledger.mark_transferred(ctx, first.payout_id, "tr_1").status == "transferred"
    assert ledger.mark_transferred(ctx, first.payout_id, "tr_1").status == "transferred"  # replayed event
    with pytest.raises(IllegalTransition):
        ledger.mark_onboarding(ctx, first.payout_id, "acct_again")
    with pytest.raises(PayoutNotFound):
        ledger.get(ctx, uuid.uuid4())
    with clean_db.connect() as conn:
        actions = (
            conn.execute(text("SELECT action FROM audit_log WHERE target_type = 'payout' ORDER BY at")).scalars().all()
        )
    assert actions[0] == "payout_created" and "payout_transferred" in actions


def test_three_transfer_failures_fall_back_to_the_export_file(clean_db: Engine) -> None:
    ctx, _, claim = _firm_program_claim(clean_db)
    ledger = DbPayoutLedger(clean_db)
    ref = ledger.create_pending(ctx, claim, 4999)
    for _ in range(2):
        ledger.mark_failed(ctx, ref.payout_id, "timeout")
        ledger._move(ctx, ref.payout_id, "pending")  # the retry job puts it back
    final = ledger.mark_failed(ctx, ref.payout_id, "timeout")
    assert final.status == "pending_export"
    assert ledger.mark_exported(ctx, ref.payout_id, "batch-1").status == "exported"
    ctx2, _, claim2 = _firm_program_claim(clean_db)
    check = ledger.create_pending(ctx2, claim2, 100, method="check_export")
    assert check.status == "pending_export"


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
