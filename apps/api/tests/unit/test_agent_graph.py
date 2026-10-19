"""Adjudication graph with a scripted model: paths, caps, citations, interrupt and resume, crash recovery."""

from __future__ import annotations

from decimal import Decimal

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from recallops.claims.graph import VARIANTS, NoEffects, build_graph, run_budget, summarize
from recallops.llm.client import ModelCallFailed, ModelResponse
from recallops.settings import Settings
from recallops.tracing.writer import MemoryTrace
from tests import fakes
from tests.fakes import decide, destruction, label, tool_use, turn

S = Settings()


def first_turn() -> ModelResponse:
    return turn(
        tool_use(1, "read_label_photo"),
        tool_use(2, "check_purchase"),
        tool_use(3, "check_duplicates"),
        tool_use(4, "check_destruction_photo"),
        text="Checking the label, purchase, duplicates, and destruction photo.",
    )


def run_graph(  # type: ignore[no-untyped-def]
    responses: list[ModelResponse], claim=None, variant="loop_no_retrieval", others=None, budget=None, effects=None
):
    llm, transport = fakes.client(responses)
    trace = MemoryTrace("adjudicate")
    budget = budget or run_budget(S)
    effects = effects or NoEffects()
    ctx = fakes.context(llm, claim or fakes.facts(), fakes.rules(), others)
    graph = build_graph(ctx, llm, trace, budget, VARIANTS[variant], effects, human_in_loop=False)
    values = graph.invoke({})
    return summarize(values), trace, transport, budget, effects


def test_approve_path_counts_model_calls_only() -> None:
    responses = [
        first_turn(),
        label("TL1302Z"),
        destruction(True),
        turn(tool_use(5, "check_model", {"model_text": "TL1302Z"})),
        decide(6, "approve", ["T5", "R:models"]),
    ]
    result, trace, transport, budget, effects = run_graph(responses)
    assert result.outcome == "approve" and not result.routed_to_human
    assert len(transport.calls) == 5
    assert budget.steps == 5  # three agent turns and two vision calls
    assert sum(s.counted for s in trace.steps) == 5
    nodes = [s.node for s in trace.steps]
    assert nodes[0] == "load_context" and nodes[-2:] == ["decide", "route"]
    assert [s.result["result_id"] for s in trace.steps if s.result and "result_id" in s.result] == [
        "T1",
        "T2",
        "T3",
        "T4",
        "T5",
    ]
    assert effects.applied and effects.applied[0]["cited_rules"] == ["R:models", "T5"]
    assert all(s.nominal_cost_usd > 0 for s in trace.steps if s.counted)


def test_rejected_calls_count_and_uncited_decide_retries_once() -> None:
    responses = [
        turn(tool_use(1, "check_serial", {"wrong": "x"}), tool_use(2, "no_such_tool")),
        turn(tool_use(3, "check_model", {"model_text": "TL1302Z"}), tool_use(4, "decide", {"outcome": "approve"})),
        decide(5, "approve", ["T9"]),  # unknown citation: rejected, retried
        decide(6, "approve", ["T1", "R:models"]),
    ]
    result, trace, _, budget, _ = run_graph(responses)
    assert result.outcome == "approve"
    errors = [s.result.get("error", "") for s in trace.steps if s.result]
    assert any("invalid arguments" in e for e in errors) and any("unknown tool" in e for e in errors)
    assert any("call decide alone" in e for e in errors)
    assert any("unknown citation ids" in e for e in errors)
    assert budget.steps == 4 + 3  # four model turns, three rejected calls


def test_decide_rejected_twice_fails_and_holds_for_a_person() -> None:
    responses = [decide(1, "approve", []), decide(2, "approve", ["nope"])]
    result, _, _, _, effects = run_graph(responses)
    assert result.status == "failed" and result.routed_to_human
    assert effects.holds and effects.holds[0]["claim_status"] == "capped"
    assert effects.holds[0]["run_status"] == "failed"


def test_step_cap_stops_before_the_ninth_model_call() -> None:
    looping = [turn(tool_use(i, "check_model", {"model_text": "X"})) for i in range(20)]
    result, trace, transport, budget, effects = run_graph(looping)
    assert result.status == "capped" and result.routed_to_human
    assert len(transport.calls) == 8 and budget.steps == 8
    assert trace.steps[-2].result is not None and trace.steps[-2].result["cap"] == "steps"
    assert effects.holds and effects.holds[0]["reason"] == "capped"


def test_usd_cap_refuses_before_the_call() -> None:
    budget = run_budget(S)
    budget.max_usd = Decimal("0.0001")
    result, _, transport, _, _ = run_graph([first_turn()], budget=budget)
    assert result.status == "capped" and len(transport.calls) == 0


def test_seconds_cap_on_a_failed_call_is_a_cap_not_a_crash() -> None:
    budget = run_budget(S)
    budget.active_seconds = 61.0
    result, _, transport, _, _ = run_graph([first_turn()], budget=budget)
    assert result.status == "capped" and len(transport.calls) == 0


def test_flag_and_value_threshold_route_to_a_person() -> None:
    other = fakes.facts(claim_id=2, photos=fakes.facts().photos)  # same photo hashes as claim 1
    flagged, _, _, _, effects = run_graph(
        [turn(tool_use(1, "check_duplicates")), decide(2, "flag", ["T1"])], others=[other]
    )
    assert flagged.outcome == "flag" and flagged.routed_to_human
    assert effects.holds and effects.holds[0]["claim_status"] == "flagged"
    big = fakes.facts(refund=25_000)
    over, _, _, _, effects = run_graph([decide(1, "approve", ["R:models"])], claim=big)
    assert over.outcome == "approve" and over.routed_to_human
    assert effects.holds and effects.holds[0]["claim_status"] == "under_review"


def test_single_pass_runs_every_check_then_one_decide() -> None:
    responses = [label("TL1302Z"), destruction(False), decide(1, "needs_info", ["T5"], "Please send a photo")]
    result, trace, transport, budget, _ = run_graph(responses, variant="single_pass")
    assert result.outcome == "needs_info"
    tools = [s.tool for s in trace.steps]
    assert tools[:3] == [None, "read_label_photo", "check_model"]
    assert "retrieve_policy" in tools and "check_duplicates" in tools
    assert len(transport.calls) == 3 and budget.steps == 3


def test_interrupt_then_resume_completes_without_rerunning_steps() -> None:
    other = fakes.facts(claim_id=2, photos=fakes.facts().photos)
    llm, transport = fakes.client([turn(tool_use(1, "check_duplicates")), decide(2, "flag", ["T1"])])
    trace, effects = MemoryTrace("adjudicate"), NoEffects()
    ctx = fakes.context(llm, fakes.facts(), fakes.rules(), [other])
    graph = build_graph(
        ctx,
        llm,
        trace,
        run_budget(S),
        VARIANTS["loop_no_retrieval"],
        effects,
        human_in_loop=True,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "run-1"}}
    paused = summarize(graph.invoke({}, config, durability="sync"))
    assert paused.interrupted and paused.held and paused.held["reason"] == "flagged"
    steps_before = len(trace.steps)
    done = summarize(
        graph.invoke(Command(resume={"action": "mark_fraud", "actor": "rev-1", "message": "same photo"}), config)
    )
    assert done.final and done.final["outcome"] == "deny" and done.final["fraud"] is True
    assert len(transport.calls) == 2  # nothing before the interrupt ran again
    assert [s.node for s in trace.steps[steps_before:]] == ["human_review"]
    assert effects.applied and effects.applied[-1]["decided_by"] == "rev-1"


def test_crash_mid_run_resumes_from_the_checkpoint() -> None:
    llm, transport = fakes.client(
        [turn(tool_use(1, "check_model", {"model_text": "TL1302Z"})), decide(2, "approve", ["T1"])]
    )
    trace, saver = MemoryTrace("adjudicate"), InMemorySaver()
    ctx = fakes.context(llm, fakes.facts(), fakes.rules(destroy=False), [])
    budget = run_budget(S)

    def flaky_send(*args, **kwargs):  # type: ignore[no-untyped-def]
        if len(transport.calls) == 1:
            transport.calls.append(("crash", 0))  # type: ignore[arg-type]
            raise ModelCallFailed("connection reset")
        return original(*args, **kwargs)

    original = transport.send
    transport.send = flaky_send  # type: ignore[method-assign]
    graph = build_graph(
        ctx, llm, trace, budget, VARIANTS["loop_no_retrieval"], NoEffects(), human_in_loop=True, checkpointer=saver
    )
    config = {"configurable": {"thread_id": "run-2"}}
    with pytest.raises(ModelCallFailed):
        graph.invoke({}, config, durability="sync")
    assert graph.get_state(config).next == ("agent",)
    rows_before = [s.step_key for s in trace.steps]
    result = summarize(graph.invoke(None, config, durability="sync"))
    assert result.outcome == "approve"
    keys = [s.step_key for s in trace.steps]
    assert keys[: len(rows_before)] == rows_before and len(keys) == len(set(keys))
    assert keys.count("load_context") == 1


def test_guard_holds_a_model_more_lenient_than_the_rules() -> None:
    # The model approves although the label it read shows a model that is not recalled (an injected approve).
    responses = [
        turn(tool_use(1, "read_label_photo")),
        label("TL9999Z"),
        decide(2, "approve", ["T1"]),
    ]
    result, trace, _, _, effects = run_graph(responses, claim=fakes.facts(), variant="loop_no_retrieval")
    assert result.outcome == "approve" and result.routed_to_human and result.guard_hold
    assert effects.holds and effects.holds[0]["reason"] == "model_disagrees_with_rules"
    assert "deny" in effects.holds[0]["detail"]
    route = next(s for s in trace.steps if s.node == "route")
    assert route.result is not None and route.result["guard"]["model_more_lenient"] is True
    assert not effects.applied  # nothing was paid or decided automatically


def test_a_stricter_model_is_allowed() -> None:
    responses = [decide(1, "needs_info", ["R:models"], "Please send a clearer label photo")]
    result, _, _, _, effects = run_graph(responses)
    assert result.outcome == "needs_info" and not result.routed_to_human
    assert effects.applied and effects.applied[0]["outcome"] == "needs_info"
