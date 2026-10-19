"""01-backend.md section 12 acceptance tests that had no outcome-level test (gap audit, decisions log 2026-10-18).
The others live next to the code they cover; the shared-cluster acceptance runs through the worker and the graph in
tests/api/test_m2_flows.py."""

from __future__ import annotations

from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from recallops.claims.graph import VARIANTS, NoEffects, build_graph, run_budget, summarize
from recallops.claims.rules_only import adjudicate
from recallops.claims.rules_plus_vision import adjudicate_with_vision
from recallops.rules.schemas import ExtractedRules, IdentificationRules, SerialRange
from recallops.settings import Settings
from recallops.tracing.writer import MemoryTrace
from tests import fakes
from tests.fakes import destruction, label, tool_use, turn

S = Settings()


def test_a_label_showing_another_model_is_denied_with_a_cited_rule() -> None:
    """The label photo is read (vision), the read model is checked against the rules, and the decision cites the
    model rule. rules_plus_vision's decision table is deterministic, so this pins the outcome, not a script."""
    llm, transport = fakes.client([label("TL9000Q"), destruction(True)])
    ctx = fakes.context(llm, fakes.facts(model="TL1302Z"), fakes.rules())
    result = adjudicate_with_vision(ctx, run_budget(S), MemoryTrace("rules_plus_vision"))
    assert result.outcome == "deny"
    assert any("R:models" in reason or "model" in reason.lower() for reason in result.reasons), result.reasons
    assert len(transport.calls) >= 1


def _with_serials(ranges: list[SerialRange]) -> ExtractedRules:
    base = fakes.rules()
    return ExtractedRules(
        identification=IdentificationRules(models=["TL1302Z"], sold_at=["Amazon.com"], serial_ranges=ranges),
        remedy=base.remedy,
    )


def test_an_out_of_range_serial_is_denied_only_when_the_notice_has_ranges() -> None:
    claim = fakes.facts(serial_claimed="SN9000")
    ranged = _with_serials([SerialRange(start="SN1000", end="SN2000", raw="SN1000 through SN2000")])
    denied = adjudicate(claim, ranged, [claim], 10_000, MemoryTrace("rules_only"))
    assert denied.outcome == "deny" and "SN1000 through SN2000" in denied.cited_rules  # the notice's own words
    assert denied.reasons == ["serial outside the recalled ranges"]
    ignored = adjudicate(claim, _with_serials([]), [claim], 10_000, MemoryTrace("rules_only"))
    assert ignored.outcome == "approve" and not any("serial" in r for r in ignored.reasons)


def test_a_capped_run_waits_for_a_person_and_resumes_without_rerunning_steps() -> None:
    llm, transport = fakes.client([turn(tool_use(1, "check_model", {"model_text": "TL1302Z"}))])
    trace, effects = MemoryTrace("adjudicate"), NoEffects()
    budget = run_budget(S)
    budget.max_usd = Decimal("0.0001")  # below one model call: the meter refuses before calling
    graph = build_graph(
        fakes.context(llm, fakes.facts(), fakes.rules()),
        llm,
        trace,
        budget,
        VARIANTS["loop_no_retrieval"],
        effects,
        human_in_loop=True,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "capped-1"}}
    paused = summarize(graph.invoke({}, config, durability="sync"))
    assert paused.interrupted and paused.held and paused.held["reason"] == "capped"
    assert effects.holds and effects.holds[0]["claim_status"] == "capped"
    before = len(trace.steps)
    done = summarize(
        graph.invoke(Command(resume={"action": "resume_override", "outcome": "approve", "actor": "rev-1"}), config)
    )
    assert done.final and done.final["outcome"] == "approve" and done.final["decided_by"] == "rev-1"
    assert [s.node for s in trace.steps[before:]] == ["human_review"]
    assert len(transport.calls) == 0  # no model call before the cap, none on resume
