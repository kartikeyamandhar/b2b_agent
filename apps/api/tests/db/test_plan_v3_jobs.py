"""The worker and payment-job rules plan v3 added: companies take turns for the worker, a product in draft does not run,
a product over its daily agent quota and a spent model budget fall back to the rules checker (closing a run the budget
stopped halfway), abandoned drafts are swept with their photos, a refund nobody chose a way to receive becomes a check,
and a transfer the platform balance cannot cover is retried, not mailed."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import stripe
from sqlalchemy import Engine, text

from recallops.claims.worker import Worker
from recallops.db.session import tenant_session
from recallops.llm.client import FakeTransport, LlmClient
from recallops.llm.meter import CostMeter, InMemoryLedger
from recallops.payouts import stripe_connect as connect
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.settings import Settings, get_settings
from recallops.storage import LocalStorage
from recallops.tenancy import FirmContext
from tests import fakes
from tests.db.test_payments_review_fixes import _connect_payout, _payout, _sdk

pytestmark = pytest.mark.db

ADDRESS = {"name": "Avery Test", "address_line1": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701"}


def _firm(engine: Engine, name: str) -> UUID:
    firm = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, :n)"), {"f": firm, "n": name})
    return firm


def _program(engine: Engine, firm: UUID, status: str = "open") -> UUID:
    program, rules = uuid.uuid4(), fakes.rules()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules, "
                "remedy_rules) VALUES (:p, :f, :s, 10000, CAST(:i AS jsonb), CAST(:r AS jsonb))"
            ),
            {
                "p": program,
                "f": firm,
                "s": status,
                "i": rules.identification.model_dump_json(),
                "r": rules.remedy.model_dump_json(),
            },
        )
    return program


def _claim(engine: Engine, firm: UUID, program: UUID, status: str = "submitted", age_min: int = 0) -> UUID:
    claim = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claims (id, firm_id, program_id, token_hash, status, model_claimed, retailer_claimed, "
                "refund_cents, created_at, submitted_at) VALUES (:c, :f, :p, :t, :s, 'TL1302Z', 'Amazon.com', 4999, "
                "now() - make_interval(mins => :m), CASE WHEN :s = 'draft' THEN NULL "
                "ELSE now() - make_interval(mins => :m) END)"
            ),
            {"c": claim, "f": firm, "p": program, "t": uuid.uuid4().hex, "s": status, "m": age_min},
        )
    return claim


def _worker(engine: Engine, settings: Settings | None = None, **kw: Any) -> Worker:
    return Worker(engine=engine, settings=settings, llm=fakes.client([])[0], worker_id="plan-v3", **kw)


def _row(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).one()


def test_companies_take_turns_and_a_draft_product_does_not_run(clean_db: Engine) -> None:
    a, b, c = _firm(clean_db, "Busy Co"), _firm(clean_db, "Quiet Co"), _firm(clean_db, "Draft Co")
    pa, pb, pc = _program(clean_db, a), _program(clean_db, b), _program(clean_db, c, status="draft")
    busy = [_claim(clean_db, a, pa, age_min=m) for m in (30, 20, 10)]  # a flood through one public link
    quiet = _claim(clean_db, b, pb, age_min=5)
    _claim(clean_db, c, pc, age_min=60)  # the oldest claim of all, in a product that is not open yet
    worker = _worker(clean_db)
    order = []
    while (leased := worker.lease_claim()) is not None:
        order.append(leased)
    assert [x.claim_id for x in order if x.firm_id == a] == busy  # the oldest first within a company
    assert quiet in [x.claim_id for x in order[:2]]  # the quiet company does not wait behind the flood
    assert len(order) == 4 and c not in {x.firm_id for x in order}


def test_a_product_over_its_daily_agent_quota_is_decided_by_the_rules_checker(clean_db: Engine) -> None:
    firm = _firm(clean_db, "Quota Co")
    claim = _claim(clean_db, firm, _program(clean_db, firm))
    worker = _worker(clean_db, Settings(intake_agent_claims_per_program_day=0))
    assert worker.tick() is True  # the scripted model has no responses: any model call would fail the claim
    decided = _row(clean_db, "SELECT status, decided_by FROM claims WHERE id = :c", c=claim)
    assert decided.decided_by == "rules_only" and decided.status == "needs_info"
    graphs = _row(clean_db, "SELECT array_agg(graph) AS g FROM agent_runs WHERE claim_id = :c", c=claim).g
    assert graphs == ["rules_only"]


def test_a_spent_budget_falls_back_to_rules_and_closes_the_run_it_stopped(clean_db: Engine) -> None:
    """The worker used to stop for everyone when the budget ran out; then (plan v3, first draft) it re-attached the
    stopped run on every lease and hit the budget again, forever. Now the stopped run is closed and the claim decided."""
    firm = _firm(clean_db, "Spent Co")
    claim = _claim(clean_db, firm, _program(clean_db, firm))
    settings = get_settings()
    spent = CostMeter(fakes.PRICES, InMemoryLedger(), Decimal(0), Decimal(0))
    llm = LlmClient(FakeTransport([]), spent, fakes.MODEL, settings)
    worker = Worker(engine=clean_db, settings=settings, llm=llm, worker_id="spent")
    assert worker.tick() is True
    assert worker.budget_fallback_until > time.monotonic()
    held = _row(clean_db, "SELECT status, attempts, lease_owner FROM claims WHERE id = :c", c=claim)
    assert (held.status, held.attempts, held.lease_owner) == ("submitted", 0, None)  # released, attempt undone
    assert worker.tick() is True
    decided = _row(clean_db, "SELECT status, decided_by FROM claims WHERE id = :c", c=claim)
    assert decided.decided_by == "rules_only"
    with clean_db.connect() as conn:
        runs = {r.graph: r for r in conn.execute(text("SELECT * FROM agent_runs WHERE claim_id = :c"), {"c": claim})}
    assert runs["adjudicate"].status == "failed" and runs["adjudicate"].outcome["budget_fallback"] is True
    assert runs["rules_only"].status == "done"
    assert worker.recover_orphans() == 0  # nothing left running for recovery to misread
    assert worker.tick() is False


def test_abandoned_drafts_are_swept_with_their_photos(clean_db: Engine, tmp_path: Path) -> None:
    firm = _firm(clean_db, "Sweep Co")
    program = _program(clean_db, firm)
    old = 60 * (get_settings().draft_ttl_hours + 1)
    abandoned = _claim(clean_db, firm, program, status="draft", age_min=old)
    registered = _claim(clean_db, firm, program, status="draft", age_min=old)
    fresh = _claim(clean_db, firm, program, status="draft")
    filed = _claim(clean_db, firm, program, status="submitted", age_min=old)
    storage = LocalStorage(tmp_path)
    key = f"{firm}/{abandoned}/label-x.jpg"
    storage.put(key, b"jpeg bytes", "image/jpeg")
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, exif) "
                "VALUES (:f, :c, 'label', :k, 'x', '0f0f0f0f0f0f0f0f', '{}')"
            ),
            {"f": firm, "c": abandoned, "k": key},
        )
        conn.execute(
            text("INSERT INTO registrations (firm_id, program_id, external_ref, claim_id) VALUES (:f, :p, 'r1', :c)"),
            {"f": firm, "p": program, "c": registered},
        )
    worker = _worker(clean_db, storage=storage)
    assert worker.sweep_due() is True
    assert worker.sweep_drafts() == 1
    assert worker.sweep_due() is False
    with clean_db.connect() as conn:
        left = set(conn.execute(text("SELECT id FROM claims WHERE firm_id = :f"), {"f": firm}).scalars())
        photos = conn.execute(text("SELECT count(*) FROM claim_photos WHERE firm_id = :f"), {"f": firm}).scalar_one()
    assert left == {registered, fresh, filed} and photos == 0
    assert storage.get(key) is None  # the object went after the row


def test_a_refund_nobody_chose_a_way_to_receive_becomes_a_check(clean_db: Engine) -> None:
    firm = _firm(clean_db, "Expiry Co")
    program = _program(clean_db, firm)
    ctx = FirmContext(firm, "tester", "admin")
    ledger = DbPayoutLedger(clean_db)
    claims = {}
    for name, address in (
        ("unchosen", ADDRESS),
        ("stalled", {**ADDRESS, "address_line1": "2 Oak St"}),
        ("recent", {**ADDRESS, "address_line1": "3 Elm St"}),
        ("shared", {**ADDRESS, "address_line1": "1 Main St"}),  # the unchosen claim's mailing address
    ):
        claims[name] = _claim(clean_db, firm, program, status="approved")
        with clean_db.begin() as conn:
            conn.execute(
                text("UPDATE claims SET remedy = 'refund', claimant = CAST(:a AS jsonb) WHERE id = :c"),
                {"a": json.dumps(address), "c": claims[name]},
            )
    payouts = {name: ledger.create_pending(ctx, c, 4999).payout_id for name, c in claims.items()}
    ledger.mark_onboarding(ctx, payouts["stalled"], "acct_stalled")
    settings = get_settings()
    with clean_db.begin() as conn:
        for name, minutes in (("unchosen", 1), ("shared", 0)):  # distinct ages: the job takes the older one first
            conn.execute(
                text(
                    "UPDATE payouts SET created_at = now() - make_interval(days => :d, mins => :m), "
                    "choice_opened_at = now() - make_interval(days => :d, mins => :m) WHERE id = :p"
                ),
                {"d": settings.payout_choice_days + 1, "m": minutes, "p": payouts[name]},
            )
        conn.execute(
            text(
                "UPDATE payouts SET created_at = now() - make_interval(days => :d), "
                "choice_opened_at = now() - make_interval(days => :d), "
                "connect_started_at = now() - make_interval(days => :d) WHERE id = :p"
            ),
            {"d": settings.payout_onboarding_days + 1, "p": payouts["stalled"]},
        )
    # The unchosen refund and the stalled setup move; the shared one is held with the first, before its own turn.
    assert connect.expire_choices(clean_db, settings) == 2
    with clean_db.connect() as conn:
        rows: dict[UUID, str] = dict(
            conn.execute(
                text("SELECT id, status || ':' || method FROM payouts WHERE id = ANY(:p)"),
                {"p": list(payouts.values())},
            ).all()
        )
    assert rows[payouts["stalled"]] == "pending_export:check_export"
    assert rows[payouts["recent"]] == "pending:stripe_connect"  # still inside the choice window
    # A check to a mailing address another claim uses is the redirect pattern: both claims go to a person.
    assert rows[payouts["unchosen"]] == "held:check_export" and rows[payouts["shared"]].startswith("held:")
    with clean_db.connect() as conn:
        held = (
            conn.execute(
                text("SELECT status FROM claims WHERE id = ANY(:c)"), {"c": [claims["unchosen"], claims["shared"]]}
            )
            .scalars()
            .all()
        )
    assert held == ["under_review", "under_review"]
    assert connect.expire_choices(clean_db, settings) == 0  # nothing moves twice


def test_a_transfer_the_balance_cannot_cover_becomes_a_check_with_an_alert(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A retry under the same idempotency key would get Stripe's stored error back, so the first draft's 30-minute
    retries could never pay by bank (plan v3 review): a short balance is a confirmed rejection like any other."""
    ctx, _, _, payout_id = _connect_payout(clean_db)

    def create(request: Any, **_: Any) -> Any:
        raise stripe.InvalidRequestError(  # type: ignore[no-untyped-call]
            "insufficient funds",
            None,
            code="balance_insufficient",
            http_status=400,
            json_body={"error": {"type": "invalid_request_error", "code": "balance_insufficient"}},
        )

    _sdk(monkeypatch, create)
    assert connect.process_payout(clean_db, get_settings(), ctx, payout_id) == "pending_export"
    row = _payout(clean_db, ctx, payout_id)
    assert (row.method, row.last_error, row.transfer_retry_at) == ("check_export", "balance_insufficient", None)
    assert "connect_balance_insufficient" in caplog.text  # the operator is told to fund the balance


def test_a_hold_released_late_gives_the_claimant_a_fresh_window(clean_db: Engine) -> None:
    """The choice window ran from the payout's creation, so a refund a person released after 14 days became a check
    on the next tick and the claimant saw a due date in the past (plan v3 review)."""
    from recallops.payouts.db_ledger import hold_for_claim
    from recallops.portal import payout_choice_due

    firm = _firm(clean_db, "Release Co")
    claim = _claim(clean_db, firm, _program(clean_db, firm), status="approved")
    ctx = FirmContext(firm, "tester", "admin")
    ledger = DbPayoutLedger(clean_db)
    payout_id = ledger.create_pending(ctx, claim, 4999).payout_id
    settings = get_settings()
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE payouts SET created_at = now() - make_interval(days => :d), "
                "choice_opened_at = now() - make_interval(days => :d) WHERE id = :p"
            ),
            {"d": settings.payout_choice_days + 5, "p": payout_id},
        )
    with tenant_session(ctx, clean_db) as session:
        hold_for_claim(session, ctx, claim, "payout_held_needs_review")
    ledger.create_pending(ctx, claim, 4999)  # a person approves it again: the hold is released
    assert connect.expire_choices(clean_db, settings) == 0
    with tenant_session(ctx, clean_db) as session:
        from recallops.models import Payout

        p = session.get(Payout, payout_id)
        assert p is not None and p.status == "pending"
        due = payout_choice_due(p)
    assert due is not None and (due - datetime.now(UTC).date()).days >= settings.payout_choice_days - 1


def test_an_approval_the_rules_checker_reaches_without_the_agent_waits_for_a_person(clean_db: Engine) -> None:
    """Past the product's daily agent quota (or with the budget spent) the rules checker decides, and it never looks
    at a photo: its approvals used to pay at once (plan v3 review, blocker)."""
    firm = _firm(clean_db, "Fallback Co")
    claim = _claim(clean_db, firm, _program(clean_db, firm))
    with clean_db.begin() as conn:
        for kind, phash in (("label", "0f0f0f0f0f0f0f0f"), ("destruction", "f0f0f0f0f0f0f0f0")):
            conn.execute(
                text(
                    "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, exif) "
                    "VALUES (:f, :c, :k, :p, :s, :h, '{}')"
                ),
                {
                    "f": firm,
                    "c": claim,
                    "k": kind,
                    "p": f"{firm}/{claim}/{kind}.jpg",
                    "s": kind + str(claim),
                    "h": phash,
                },
            )
    worker = _worker(clean_db, Settings(intake_agent_claims_per_program_day=0))
    assert worker.tick() is True
    held = _row(clean_db, "SELECT status, decision FROM claims WHERE id = :c", c=claim)
    assert held.status == "under_review" and held.decision["decided_by"] == "rules_only"
    assert held.decision["outcome"] == "approve" and held.decision["hold_reason"] == "agent_unavailable"
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM payouts WHERE claim_id = :c"), {"c": claim}) == 0  # no money

    rules_mode = _claim(clean_db, firm, _program(clean_db, firm))  # ADJUDICATOR=rules_only still decides alone
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, exif) "
                "SELECT firm_id, :n, kind, storage_path || '2', sha256 || '2', phash, exif FROM claim_photos "
                "WHERE claim_id = :c"
            ),
            {"n": rules_mode, "c": claim},
        )
    assert _worker(clean_db, Settings(adjudicator="rules_only")).tick() is True
    assert _row(clean_db, "SELECT status FROM claims WHERE id = :c", c=rules_mode).status in ("approved", "flagged")


def test_the_sweeper_skips_a_draft_someone_holds(clean_db: Engine, tmp_path: Path) -> None:
    """A claimant submitting at the 48-hour mark holds the claim row: the sweeper skips it and keeps its photos
    (plan v3 review: its paths were read before the delete and their objects removed anyway)."""
    firm = _firm(clean_db, "Race Co")
    draft = _claim(clean_db, firm, _program(clean_db, firm), status="draft", age_min=60 * 49)
    storage = LocalStorage(tmp_path)
    key = f"{firm}/{draft}/label-x.jpg"
    storage.put(key, b"jpeg bytes", "image/jpeg")
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, exif) "
                "VALUES (:f, :c, 'label', :k, 'x', '0f0f0f0f0f0f0f0f', '{}')"
            ),
            {"f": firm, "c": draft, "k": key},
        )
    with clean_db.connect() as holder, holder.begin():
        holder.execute(text("SELECT id FROM claims WHERE id = :c FOR UPDATE"), {"c": draft})  # a submit in progress
        assert _worker(clean_db, storage=storage).sweep_drafts() == 0
    assert storage.get(key) == b"jpeg bytes"


def test_the_claimant_sees_the_day_an_unchosen_refund_becomes_a_check(clean_db: Engine) -> None:
    from datetime import timedelta

    from recallops.models import Payout
    from recallops.portal import payout_choice_due

    ctx, _, _, payout_id = _connect_payout(clean_db)  # approved, bank setup started (onboarding)
    settings = get_settings()
    with tenant_session(ctx, clean_db) as session:
        p = session.get(Payout, payout_id)
        assert p is not None and p.status == "onboarding"
        started = p.connect_started_at or p.created_at
        assert payout_choice_due(p) == (started + timedelta(days=settings.payout_onboarding_days)).date()
        p.status, p.connect_started_at = "pending", None
        assert payout_choice_due(p) == (p.created_at + timedelta(days=settings.payout_choice_days)).date()
        p.method = "check_export"
        assert payout_choice_due(p) is None  # a check needs no choice
        session.rollback()
