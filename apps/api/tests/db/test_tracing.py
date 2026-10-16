"""DbTrace writes the run row and one step row per node; archive runs carry no firm."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import Engine, text

from recallops.tracing.writer import DbTrace

pytestmark = pytest.mark.db


def test_archive_extraction_trace(clean_db: Engine) -> None:
    trace = DbTrace(clean_db, "extract_rules", firm_id=None, model_id="claude-haiku-5-5")
    trace.step("load_notice", args={"chars": 10})
    trace.step("draft_rules", tool="llm", tokens_in=100, tokens_out=20, cost_usd=Decimal("0.000020"))
    trace.finish("done", {"status": "done"})
    with clean_db.connect() as conn:
        run = conn.execute(text("SELECT * FROM agent_runs WHERE id = :id"), {"id": trace.run_id}).one()
        steps = conn.execute(
            text("SELECT idx, node, firm_id, cost_usd FROM agent_steps WHERE run_id = :id ORDER BY idx"),
            {"id": trace.run_id},
        ).all()
    assert (run.status, run.steps, run.cost_usd, run.firm_id) == ("done", 2, Decimal("0.000020"), None)
    assert run.thread_id == str(trace.run_id)
    assert run.finished_at is not None
    assert [(s.idx, s.node, s.firm_id) for s in steps] == [(0, "load_notice", None), (1, "draft_rules", None)]


def test_tenant_trace_copies_firm_to_steps(clean_db: Engine) -> None:
    firm, program, claim = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'f')"), {"f": firm})
        conn.execute(
            text("INSERT INTO recall_programs (id, firm_id, value_threshold_cents) VALUES (:p, :f, 1)"),
            {"p": program, "f": firm},
        )
        conn.execute(
            text("INSERT INTO claims (id, firm_id, program_id, token_hash) VALUES (:c, :f, :p, 't')"),
            {"c": claim, "f": firm, "p": program},
        )
    trace = DbTrace(clean_db, "rules_only", firm_id=firm, claim_id=claim, program_id=program)
    trace.step("check_model", tool="check_model", result={"verdict": "match"})
    trace.finish("done")
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT firm_id FROM agent_steps")).scalar_one() == firm
