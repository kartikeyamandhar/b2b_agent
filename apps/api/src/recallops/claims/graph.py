"""Claim adjudication graph (01-backend.md section 6; CLAUDE.md invariants 3 to 6).

Loop variants (ReAct): load_context -> agent (model turn with tools) -> tools (validated, executed, results T1..Tn)
-> agent ... until decide is called and accepted, a cap stops the run, or the model fails twice; then route.
single_pass: load_context -> fixed_tools (every check once, fixed order) -> decide_once (one model call that may only
call decide) -> route. route applies the outcome, or holds the claim for a person: flagged, refund over the value
threshold, capped, or failed. With human_in_loop the graph then pauses in human_review (a LangGraph interrupt) and
the reviewer's payload resumes the same checkpointed run.

Crash safety: one thread per run (thread_id = agent_runs.id); invoke with durability="sync"; every trace row has a
step_key so a node re-executed after a crash writes nothing twice; route and human_review effects are idempotent
(claims/effects.py); the run budget is rebuilt from the run's counted rows on every attach.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from recallops.claims.agent_tools import (
    TOOLS_VERSION,
    DecideArgs,
    ToolContext,
    ToolRun,
    _rejected,
    claim_brief,
    decide_spec,
    execute,
    result_content,
    rule_catalog,
    tool_specs,
    validate_decision,
)
from recallops.claims.policy import POLICY_TEXT
from recallops.llm.client import Completion, LlmClient, ModelCallFailed, ModelRequest
from recallops.llm.meter import RunBudget, RunCapExceeded
from recallops.logs import get_logger, log_event
from recallops.retrieval.search import RetrievalMode
from recallops.tracing.writer import TraceSink

log = get_logger("claims.graph")

GRAPH_VERSION = "1.0.0"
NUDGE = "Call decide with the outcome, or call the checks you still need."


@dataclass(frozen=True)
class AgentConfig:
    variant: str = "loop_full"
    loop: bool = True
    retrieval: RetrievalMode = "full"
    max_agent_tokens: int = 900

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "loop": self.loop,
            "retrieval": self.retrieval,
            "graph_version": GRAPH_VERSION,
            "tools_version": TOOLS_VERSION,
        }


VARIANTS: dict[str, AgentConfig] = {
    "loop_full": AgentConfig("loop_full", True, "full"),
    "loop_no_retrieval": AgentConfig("loop_no_retrieval", True, "none"),
    "loop_keyword_only": AgentConfig("loop_keyword_only", True, "keyword"),
    "single_pass": AgentConfig("single_pass", False, "full"),
}


def system_prompt(config: AgentConfig) -> str:
    retrieval = (
        "- Call retrieve_policy only when the rules and the policy below do not settle a question.\n"
        if config.retrieval != "none"
        else ""
    )
    if not config.loop:
        return (
            "You adjudicate consumer claims for a U.S. product recall program. The checks have already been run; "
            "their results are in the message. Call decide once with the outcome.\n\n" + POLICY_TEXT
        )
    return (
        "You adjudicate consumer claims for a U.S. product recall program. You check each claim with tools, then "
        "record a decision.\n"
        "How to work:\n"
        "- Before your tool calls in each turn, write one short sentence saying what you are checking and why.\n"
        "- In your first turn, call in parallel every check the claim needs: read_label_photo, check_purchase, "
        "check_duplicates, check_destruction_photo when the rules include R:destruction, check_serial when they "
        "include R:serials, and check_date_code when they include R:date_codes (with the typed values).\n"
        "- Then call check_model with the model text read from the label, and with the typed model when the label "
        "is unreadable or the two differ.\n" + retrieval + "- When the results settle the outcome, call decide alone, "
        "with no other tool in the same turn.\n\n" + POLICY_TEXT
    )


class AdjState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    results: dict[str, dict[str, Any]]
    turn: int
    pending: list[dict[str, Any]]
    no_tool_turns: int
    decide_failures: int
    decision: dict[str, Any] | None
    status: Literal["running", "decided", "capped", "failed"]
    detail: str | None
    final: dict[str, Any] | None
    held: dict[str, Any] | None


class Effects(Protocol):
    def apply(self, final: dict[str, Any]) -> None:
        """Write the final outcome (idempotent)."""
        ...

    def hold(self, held: dict[str, Any]) -> None:
        """Put the claim in front of a person (idempotent)."""
        ...


@dataclass
class NoEffects:
    """Evals: record what would have been written."""

    applied: list[dict[str, Any]] | None = None
    holds: list[dict[str, Any]] | None = None

    def apply(self, final: dict[str, Any]) -> None:
        self.applied = [*(self.applied or []), final]

    def hold(self, held: dict[str, Any]) -> None:
        self.holds = [*(self.holds or []), held]


def _final_from_decision(decision: dict[str, Any], decided_by: str) -> dict[str, Any]:
    cited = sorted({c for r in decision["reasons"] for c in r["cites"]})
    return {
        "outcome": decision["outcome"],
        "reasons": [r["text"] for r in decision["reasons"]],
        "cited_rules": cited,
        "question_for_claimant": decision.get("question_for_claimant"),
        "confidence": decision.get("confidence"),
        "decided_by": decided_by,
    }


def _final_from_review(payload: dict[str, Any], held: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action", "resume_override")
    actor = str(payload.get("actor") or "reviewer")
    message = payload.get("message")
    agent_decision = held.get("decision") or {}
    if action == "mark_fraud":
        outcome, reasons = "deny", ["marked as fraud by a reviewer"]
    elif action == "request_info":
        outcome, reasons = "needs_info", ["a reviewer asked for more information"]
    else:
        outcome = str(payload["outcome"])
        reasons = [f"reviewer set the outcome to {outcome}"]
    if message:
        reasons.append(str(message))
    return {
        "outcome": outcome,
        "reasons": reasons,
        "cited_rules": agent_decision.get("cited_rules", []),
        "question_for_claimant": message if outcome == "needs_info" else None,
        "confidence": None,
        "decided_by": actor,
        "review_action": action,
        "agent_outcome": agent_decision.get("outcome"),
        "fraud": action == "mark_fraud",
    }


def _cost_fields(done: Completion | None) -> dict[str, Any]:
    if done is None:
        return {}
    usage = done.response.usage
    return {
        "latency_ms": done.latency_ms,
        "tokens_in": usage.total_input,
        "tokens_out": usage.output_tokens,
        "cost_usd": done.cost_usd,
        "nominal_cost_usd": done.nominal_cost_usd,
    }


def build_graph(
    ctx: ToolContext,
    llm: LlmClient,
    trace: TraceSink,
    run: RunBudget,
    config: AgentConfig,
    effects: Effects,
    *,
    human_in_loop: bool,
    checkpointer: Any = None,
) -> Any:
    catalog = rule_catalog(ctx.rules, ctx.value_threshold_cents)
    brief = claim_brief(ctx.claim, ctx.rules, ctx.value_threshold_cents)
    system = system_prompt(config)
    specs = tool_specs(retrieval=config.retrieval != "none")
    claim_id = str(ctx.claim.claim_id)

    def capped(node: str, key: str, exc: RunCapExceeded, t0: float) -> AdjState:
        trace.step(
            node,
            tool="llm" if node in ("agent", "decide_once") else node,
            thought="refused by the cost meter before the call",
            result={"capped": str(exc), "cap": exc.kind},
            latency_ms=round((time.monotonic() - t0) * 1000),
            step_key=key,
        )
        log_event(log, "adjudication_capped", claim_id=claim_id, run_id=str(trace.run_id), cap=exc.kind)
        return {"status": "capped", "detail": f"{exc.kind} cap: {exc}", "pending": []}

    def model_call(request: ModelRequest) -> Completion:
        try:
            return llm.complete(request, run)
        except ModelCallFailed as exc:
            if run.elapsed() >= run.max_seconds:
                raise RunCapExceeded("seconds", f"call failed at the time cap: {exc}") from exc
            raise

    # nodes

    def load_context(state: AdjState) -> AdjState:
        trace.step(
            "load_context",
            result={
                "rules": list(catalog),
                "photos": sorted({p.kind for p in ctx.claim.photos}),
                **config.as_dict(),
            },
            step_key="load_context",
        )
        return {
            "messages": [],
            "results": {},
            "turn": 0,
            "pending": [],
            "no_tool_turns": 0,
            "decide_failures": 0,
            "decision": None,
            "status": "running",
            "detail": None,
        }

    def agent(state: AdjState) -> AdjState:
        turn = state.get("turn", 0) + 1
        key = f"agent:{turn}"
        request = ModelRequest(
            system=system,
            user_text=brief,
            messages=tuple(state.get("messages", [])),
            tools=specs,
            max_tokens=config.max_agent_tokens,
            purpose="adjudicate_agent",
        )
        t0 = time.monotonic()
        try:
            done = model_call(request)
        except RunCapExceeded as exc:
            return {**capped("agent", key, exc, t0), "turn": turn}
        calls = done.response.tool_calls
        text = done.response.text.strip()
        trace.step(
            "agent",
            tool="llm",
            thought=text or None,
            args={"turn": turn},
            result={"tool_calls": [c.name for c in calls], "stop_reason": done.response.stop_reason},
            counted=True,
            step_key=key,
            **_cost_fields(done),
        )
        messages = [*state.get("messages", []), {"role": "assistant", "content": list(done.response.blocks)}]
        if not calls:
            no_tool = state.get("no_tool_turns", 0) + 1
            if no_tool >= 2:
                return {"status": "failed", "detail": "the model answered twice without a tool call", "turn": turn}
            messages.append({"role": "user", "content": [{"type": "text", "text": NUDGE}]})
            return {"messages": messages, "turn": turn, "pending": [], "no_tool_turns": no_tool}
        pending = [{"id": c.id, "name": c.name, "input": c.input} for c in calls]
        return {"messages": messages, "turn": turn, "pending": pending, "no_tool_turns": 0}

    def tools(state: AdjState) -> AdjState:
        turn = state.get("turn", 0)
        calls = state.get("pending", [])
        results = dict(state.get("results", {}))
        blocks: list[dict[str, Any]] = []
        decision: dict[str, Any] | None = None
        status: Literal["running", "decided", "capped", "failed"] = "running"
        detail: str | None = None
        failures = state.get("decide_failures", 0)
        for i, call in enumerate(calls):
            key = f"tool:{turn}:{i}"
            t0 = time.monotonic()
            try:
                if call["name"] == "decide" and len(calls) > 1:
                    tr = _rejected("decide", call["input"], "call decide alone, after reviewing the results", run)
                else:
                    tr = execute(call["name"], call["input"], ctx, run)
            except RunCapExceeded as exc:
                return {**capped(call["name"], key, exc, t0), "results": results}
            latency = round((time.monotonic() - t0) * 1000)
            if tr.decision is not None:
                error = validate_decision(tr.decision, set(catalog) | set(results))
                trace.step(
                    "decide",
                    tool="decide",
                    args=tr.args,
                    result={"accepted": error is None, **({"error": error} if error else {})},
                    latency_ms=latency,
                    step_key=key,
                )
                if error is None:
                    decision, status = tr.decision.model_dump(mode="json"), "decided"
                    break
                failures += 1
                if failures >= 2:
                    status, detail = "failed", f"decide rejected twice: {error}"
                    break
                content = json.dumps({"error": error}, sort_keys=True)
                blocks.append({"type": "tool_result", "tool_use_id": call["id"], "content": content, "is_error": True})
                continue
            rid = f"T{len(results) + 1}"
            results[rid] = {"tool": tr.name, "args": tr.args, "result": tr.result}
            fields = _cost_fields(tr.completion) or {"latency_ms": latency}
            trace.step(
                tr.name,
                tool=tr.name,
                args=tr.args,
                result={"result_id": rid, **tr.result},
                counted=tr.counted,
                step_key=key,
                **fields,
            )
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": result_content(rid, tr),
            }
            if tr.error:
                block["is_error"] = True
            blocks.append(block)
        update: AdjState = {"results": results, "pending": [], "decide_failures": failures, "status": status}
        if decision is not None:
            update["decision"] = decision
        if detail is not None:
            update["detail"] = detail
        if status == "running":
            update["messages"] = [*state.get("messages", []), {"role": "user", "content": blocks}]
        return update

    def fixed_tools(state: AdjState) -> AdjState:
        ident = ctx.rules.identification
        plan: list[tuple[str, dict[str, Any]]] = [("read_label_photo", {})]
        results: dict[str, dict[str, Any]] = {}

        def run_one(i: int, name: str, args: dict[str, Any]) -> ToolRun | None:
            key = f"fixed:{i}"
            t0 = time.monotonic()
            tr = execute(name, args, ctx, run)
            rid = f"T{len(results) + 1}"
            results[rid] = {"tool": tr.name, "args": tr.args, "result": tr.result}
            trace.step(
                tr.name,
                tool=tr.name,
                args=tr.args,
                result={"result_id": rid, **tr.result},
                counted=tr.counted,
                step_key=key,
                **(_cost_fields(tr.completion) or {"latency_ms": round((time.monotonic() - t0) * 1000)}),
            )
            return tr

        try:
            label = run_one(0, "read_label_photo", {})
            label_text = (label.result.get("model_text") if label and label.result.get("legible") else None) or None
            if ctx.claim.model_claimed:
                plan.append(("check_model", {"model_text": ctx.claim.model_claimed}))
            if label_text and label_text != ctx.claim.model_claimed:
                plan.append(("check_model", {"model_text": label_text}))
            if ident.serial_ranges and ctx.claim.serial_claimed:
                plan.append(("check_serial", {"serial": ctx.claim.serial_claimed}))
            if ident.date_codes and ctx.claim.date_code_claimed:
                plan.append(("check_date_code", {"date_code": ctx.claim.date_code_claimed}))
            plan += [("check_purchase", {}), ("check_destruction_photo", {}), ("check_duplicates", {})]
            if config.retrieval != "none":
                plan.append(("retrieve_policy", {"query": "proof of purchase receipt destruction photo eligibility"}))
            for i, (name, args) in enumerate(plan[1:], start=1):
                run_one(i, name, args)
        except RunCapExceeded as exc:
            return {**capped("fixed_tools", f"fixed:cap:{len(results)}", exc, time.monotonic()), "results": results}
        return {"results": results, "status": "running", "turn": 0, "decide_failures": 0, "messages": []}

    def decide_once(state: AdjState) -> AdjState:
        results = state.get("results", {})
        lines = [
            json.dumps({"result_id": rid, "tool": r["tool"], "args": r["args"], **r["result"]}, sort_keys=True)
            for rid, r in results.items()
        ]
        user_text = brief.replace(
            "Check the claim with the tools, then call decide.",
            "CHECK RESULTS (cite these ids)\n" + "\n".join(lines) + "\n\nCall decide.",
        )
        messages: list[dict[str, Any]] = []
        for attempt in (1, 2):
            key = f"decide_once:{attempt}"
            request = ModelRequest(
                system=system,
                user_text=user_text,
                messages=tuple(messages),
                tools=decide_spec(),
                max_tokens=config.max_agent_tokens,
                purpose="adjudicate_single_pass",
            )
            t0 = time.monotonic()
            try:
                done = model_call(request)
            except RunCapExceeded as exc:
                return capped("decide_once", key, exc, t0)
            calls = [c for c in done.response.tool_calls if c.name == "decide"]
            error = "call decide with the outcome" if not calls else None
            decision: DecideArgs | None = None
            if calls:
                try:
                    decision = DecideArgs.model_validate(calls[0].input)
                    error = validate_decision(decision, set(catalog) | set(results))
                except ValueError as exc:
                    error = f"invalid arguments: {exc}"
            trace.step(
                "decide_once",
                tool="llm",
                thought=done.response.text.strip() or None,
                args={"attempt": attempt},
                result={"accepted": error is None, **({"error": error} if error else {})},
                counted=True,
                step_key=key,
                **_cost_fields(done),
            )
            if error is None and decision is not None:
                return {"decision": decision.model_dump(mode="json"), "status": "decided"}
            messages = [{"role": "assistant", "content": list(done.response.blocks)}]
            if calls:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": calls[0].id, "content": error, "is_error": True}
                        ],
                    }
                )
            else:
                messages.append({"role": "user", "content": [{"type": "text", "text": "Call decide."}]})
        return {"status": "failed", "detail": "single pass: no valid decision in two attempts"}

    def route(state: AdjState) -> AdjState:
        status = state.get("status", "failed")
        held: dict[str, Any] | None = None
        final: dict[str, Any] | None = None
        if status == "decided":
            final = _final_from_decision(state["decision"] or {}, "agent")
            over = final["outcome"] == "approve" and (ctx.claim.refund_cents or 0) > ctx.value_threshold_cents
            if final["outcome"] == "flag" or over:
                held = {
                    "reason": "flagged" if final["outcome"] == "flag" else "over_value_threshold",
                    "detail": None,
                    "decision": final,
                    "claim_status": "flagged" if final["outcome"] == "flag" else "under_review",
                    "run_status": "waiting_human",
                }
        else:
            held = {
                "reason": status,
                "detail": state.get("detail"),
                "decision": None,
                "claim_status": "capped",
                "run_status": "capped" if status == "capped" else "failed",
            }
        trace.step(
            "route",
            result={
                "status": status,
                "outcome": (final or {}).get("outcome"),
                "routed_to_human": held is not None,
                "hold_reason": (held or {}).get("reason"),
            },
            step_key="route",
        )
        if held is not None:
            effects.hold(held)
            log_event(log, "claim_held", claim_id=claim_id, run_id=str(trace.run_id), reason=held["reason"])
            return {"held": held, "final": None}
        assert final is not None
        effects.apply(final)
        log_event(log, "claim_adjudicated", claim_id=claim_id, run_id=str(trace.run_id), outcome=final["outcome"])
        return {"final": final, "held": None}

    def human_review(state: AdjState) -> AdjState:
        held = state.get("held") or {}
        payload = interrupt({"reason": held.get("reason"), "decision": held.get("decision")})
        final = _final_from_review(payload, held)
        trace.step("human_review", args=dict(payload), result=final, step_key="human_review")
        effects.apply(final)
        log_event(log, "claim_reviewed", claim_id=claim_id, run_id=str(trace.run_id), outcome=final["outcome"])
        return {"final": final}

    def after_agent(state: AdjState) -> str:
        if state.get("status") in ("capped", "failed"):
            return "route"
        return "tools" if state.get("pending") else "agent"

    def after_tools(state: AdjState) -> str:
        return "agent" if state.get("status") == "running" else "route"

    def after_route(state: AdjState) -> str:
        return "human_review" if human_in_loop and state.get("held") else END

    graph = StateGraph(AdjState)
    graph.add_node("load_context", load_context)
    graph.add_node("route", route)
    graph.add_node("human_review", human_review)
    if config.loop:
        graph.add_node("agent", agent)
        graph.add_node("tools", tools)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "agent")
        graph.add_conditional_edges("agent", after_agent, ["tools", "agent", "route"])
        graph.add_conditional_edges("tools", after_tools, ["agent", "route"])
    else:
        graph.add_node("fixed_tools", fixed_tools)
        graph.add_node("decide_once", decide_once)
        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "fixed_tools")
        graph.add_conditional_edges(
            "fixed_tools", lambda s: "route" if s.get("status") == "capped" else "decide_once", ["decide_once", "route"]
        )
        graph.add_edge("decide_once", "route")
    graph.add_conditional_edges("route", after_route, ["human_review", END])
    graph.add_edge("human_review", END)
    return graph.compile(checkpointer=checkpointer)


@dataclass(frozen=True)
class RunResult:
    outcome: str | None
    routed_to_human: bool
    status: str
    final: dict[str, Any] | None
    held: dict[str, Any] | None
    interrupted: bool


def summarize(values: dict[str, Any]) -> RunResult:
    held, final = values.get("held"), values.get("final")
    outcome = (final or {}).get("outcome") or ((held or {}).get("decision") or {}).get("outcome")
    return RunResult(
        outcome=outcome,
        routed_to_human=held is not None,
        status=str(values.get("status")),
        final=final,
        held=held,
        interrupted="__interrupt__" in values,
    )


def run_budget(settings: Any, snapshot: Any = None) -> RunBudget:
    """Caps from settings (invariant 4), with what the run already used when a process attaches to it."""
    from decimal import Decimal

    budget = RunBudget(
        max_steps=settings.run_cap_steps, max_usd=settings.run_cap_usd, max_seconds=settings.run_cap_seconds
    )
    if snapshot is not None:
        budget.steps = snapshot.counted_steps
        budget.cost_usd = Decimal(snapshot.nominal_cost_usd)
        budget.active_seconds = float(snapshot.seconds)
    return budget


Runner = Callable[..., Any]
