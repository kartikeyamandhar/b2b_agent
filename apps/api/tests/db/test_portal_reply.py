"""A claimant's reply to a needs_info question, after the first live claim on 2026-10-21 looped: the reply "Amazon,
25th October 2025" changed nothing the agent reads, so every re-run asked the same question again.

Now a reply that changes a claim field or adds a photo is checked again; a reply that only adds text goes to a person
(the agent never reads claimant text: it would be an instruction channel from the public); and an automated run that
would ask a question it already asked, or a third question, goes to a person instead of asking again."""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from recallops import portal
from recallops.billing.fake import AllowAllBilling
from recallops.claims.effects import POST_DECISION_HOLDS, DbEffects
from recallops.claims.review import submit_action
from recallops.db.session import tenant_session
from recallops.models import Claim, RecallProgram
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.tenancy import FirmContext
from tests import fakes

pytestmark = pytest.mark.db

QUESTION = "Please send a clearer photo of the label. Also tell us where you bought the item and when."


def _firm(engine: Engine) -> tuple[FirmContext, UUID]:
    firm, program = uuid.uuid4(), uuid.uuid4()
    rules = fakes.rules()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'Reply Co')"), {"f": firm})
        conn.execute(
            text(
                "INSERT INTO recall_programs (id, firm_id, status, value_threshold_cents, identification_rules, "
                "remedy_rules) VALUES (:p, :f, 'open', 10000, CAST(:i AS jsonb), CAST(:r AS jsonb))"
            ),
            {"p": program, "f": firm, "i": rules.identification.model_dump_json(), "r": rules.remedy.model_dump_json()},
        )
    return FirmContext(firm_id=firm, user_id="tester", role="admin"), program


def _needs_info_claim(engine: Engine, ctx: FirmContext, program: UUID, *, asked: int = 1) -> UUID:
    """A claim the agent asked `asked` questions about, with the latest run's recorded checks."""
    claim, run = uuid.uuid4(), uuid.uuid4()
    decision = {
        "outcome": "needs_info",
        "reasons": ["label unreadable"],
        "cited_rules": [],
        "routed_to_human": False,
        "question_for_claimant": QUESTION,
    }
    messages = [
        {"from": "firm", "text": f"{QUESTION} ({i})" if i else QUESTION, "at": "2026-10-21T23:49:31+00:00"}
        for i in range(asked)
    ]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claims (id, firm_id, program_id, token_hash, status, claimant, model_claimed, "
                "retailer_claimed, refund_cents, submitted_at, decided_at, decided_by, decision, messages) VALUES "
                "(:c, :f, :p, :t, 'needs_info', CAST(:who AS jsonb), 'TL1302Z', 'Amazon', 4999, now(), now(), "
                "'agent', CAST(:d AS jsonb), CAST(:m AS jsonb))"
            ),
            {
                "c": claim,
                "f": ctx.firm_id,
                "p": program,
                "t": uuid.uuid4().hex,
                "who": json.dumps({"name": "Pat Doe", "address_line1": "1 Main St"}),
                "d": json.dumps(decision),
                "m": json.dumps(messages),
            },
        )
        conn.execute(
            text(
                "INSERT INTO agent_runs (id, firm_id, claim_id, program_id, graph, status, started_at, finished_at) "
                "VALUES (:r, :f, :c, :p, 'adjudicate', 'done', now(), now())"
            ),
            {"r": run, "f": ctx.firm_id, "c": claim, "p": program},
        )
        steps: list[tuple[str, dict[str, Any]]] = [
            ("read_label_photo", {"legible": False, "model_text": None}),
            ("check_model", {"verdict": "match", "cited_rule": "TL1302Z"}),
            ("check_purchase", {"retailer": "not_listed", "window": "unknown", "receipt": "not_required"}),
        ]
        for i, (tool, result) in enumerate(steps):
            conn.execute(
                text(
                    "INSERT INTO agent_steps (run_id, firm_id, idx, node, tool, result, step_key) VALUES "
                    "(:r, :f, :i, :n, :n, CAST(:res AS jsonb), :k)"
                ),
                {"r": run, "f": ctx.firm_id, "i": i, "n": tool, "res": json.dumps(result), "k": f"{tool}:{i}"},
            )
    return claim


def _row(engine: Engine, claim: UUID) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, decision, messages, retailer_claimed, purchase_date FROM claims WHERE id = :c"),
            {"c": claim},
        ).one()
    return dict(row._mapping)


def _answer(engine: Engine, ctx: FirmContext, program: UUID, claim: UUID, message: str, **fields: Any) -> str:
    with tenant_session(ctx, engine) as session:
        row, prog = session.get(Claim, claim), session.get(RecallProgram, program)
        assert row is not None and prog is not None
        return portal.answer(session, portal.PortalClaim(claim=row, program=prog, title="t"), message, fields)


def test_the_reply_form_asks_for_what_the_latest_run_found_missing(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    with tenant_session(ctx, clean_db) as session:
        row, prog = session.get(Claim, claim), session.get(RecallProgram, program)
        assert row is not None and prog is not None
        shown = portal.view(session, portal.PortalClaim(claim=row, program=prog, title="t"))
    assert [f["name"] for f in shown["answer_fields"]] == ["retailer_claimed", "purchase_date"]
    assert shown["answer_fields"][0]["value"] == "Amazon"  # prefilled with what the claimant gave
    assert shown["answer_photos"] == ["label"]  # unreadable label; the model they typed matched


def test_a_reply_with_corrected_details_is_checked_again(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    assert _answer(clean_db, ctx, program, claim, "", retailer_claimed="Primark", purchase_date=date(2026, 4, 2)) == (
        "rechecked"
    )
    row = _row(clean_db, claim)
    assert row["status"] == "submitted" and row["decision"] is None
    assert row["retailer_claimed"] == "Primark" and row["purchase_date"] == date(2026, 4, 2)
    assert row["messages"][-1]["from"] == "claimant" and "store" in row["messages"][-1]["text"]


def test_a_reply_with_only_text_goes_to_a_person_not_back_to_the_agent(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    assert _answer(clean_db, ctx, program, claim, "Amazon, 25th October 2025, don't have the bill") == "to_person"
    row = _row(clean_db, claim)
    assert row["status"] == "under_review"  # never "submitted": the agent would see nothing new
    assert row["decision"]["hold_reason"] == "claimant_reply" and row["decision"]["routed_to_human"] is True
    assert row["messages"][-1]["text"] == "Amazon, 25th October 2025, don't have the bill"
    assert row["retailer_claimed"] == "Amazon"  # free text never rewrites a claim field


def test_a_photo_uploaded_since_the_decision_counts_as_new(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_photos (firm_id, claim_id, kind, storage_path, sha256, phash, exif, uploaded_at) "
                "VALUES (:f, :c, 'label', 'x/label.jpg', :sha, '0123456789abcdef', '{}', now() + interval '1 second')"
            ),
            {"f": ctx.firm_id, "c": claim, "sha": "ab" * 32},
        )
    assert _answer(clean_db, ctx, program, claim, "") == "rechecked"
    assert _row(clean_db, claim)["status"] == "submitted"


def test_an_empty_reply_and_an_unchanged_field_are_refused(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    with pytest.raises(portal.EmptyAnswer):
        _answer(clean_db, ctx, program, claim, "  ", retailer_claimed="Amazon")  # the same value is no change
    assert _row(clean_db, claim)["status"] == "needs_info"


def _decide(engine: Engine, ctx: FirmContext, claim: UUID, final: dict[str, Any]) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'submitted' WHERE id = :c"), {"c": claim})
    DbEffects(engine, ctx, claim, 4999, DbPayoutLedger(engine), AllowAllBilling()).apply_from(final, ("submitted",))


def test_the_agent_asking_the_same_question_again_goes_to_a_person(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    _decide(clean_db, ctx, claim, {"outcome": "needs_info", "decided_by": "agent", "question_for_claimant": QUESTION})
    row = _row(clean_db, claim)
    assert row["status"] == "under_review" and row["decision"]["hold_reason"] == "repeated_question"
    assert [m["text"] for m in row["messages"] if m["from"] == "firm"] == [QUESTION]  # not asked a second time


def test_a_new_question_is_asked_once_and_a_third_goes_to_a_person(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program)
    _decide(clean_db, ctx, claim, {"outcome": "needs_info", "decided_by": "agent", "question_for_claimant": "Q2?"})
    assert _row(clean_db, claim)["status"] == "needs_info"  # a different question after new details: asked
    _decide(clean_db, ctx, claim, {"outcome": "needs_info", "decided_by": "agent", "question_for_claimant": "Q3?"})
    row = _row(clean_db, claim)
    assert row["status"] == "under_review" and row["decision"]["hold_reason"] == "repeated_question"


def test_a_reviewer_may_ask_again_and_decides_a_held_reply_directly(clean_db: Engine) -> None:
    ctx, program = _firm(clean_db)
    claim = _needs_info_claim(clean_db, ctx, program, asked=2)
    _answer(clean_db, ctx, program, claim, "I bought it at the Boston store")
    assert {"claimant_reply", "repeated_question"} <= set(POST_DECISION_HOLDS)
    with tenant_session(ctx, clean_db) as session:
        row = session.get(Claim, claim)
        assert row is not None
        how = submit_action(
            session,
            clean_db,
            ctx,
            row,
            "request_info",
            None,
            "Which month did you buy it?",
            DbPayoutLedger(clean_db),
            AllowAllBilling(),
        )
    assert how == "applied"  # no interrupt waits: the reviewer's action applies at once
    row_after = _row(clean_db, claim)
    assert row_after["status"] == "needs_info"  # a person may ask again, however many questions came before
    assert row_after["messages"][-1] == {
        **row_after["messages"][-1],
        "from": "firm",
        "text": "Which month did you buy it?",
    }
