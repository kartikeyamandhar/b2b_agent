"""Rule extraction graph (01-backend.md section 5, extractor 2).

load_notice -> draft_rules (one model call through llm/, structured output) -> verify_spans -> finalize.
At most EXTRACT_MAX_CALLS model calls: a draft that fails to parse, or loses more than half its values in
verification, is redrafted once. Every node writes an agent_steps row. A cap or budget refusal ends the run
with status capped and no rules. Compiled without a checkpointer: single pass, no interrupts.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from recallops.llm.client import LlmClient, ModelCallFailed, ModelRequest, ReplayMiss
from recallops.llm.meter import BudgetExhausted, RunBudget, RunCapExceeded
from recallops.rules.schemas import ExtractedRules
from recallops.rules.verify import verify_spans
from recallops.tracing.writer import TraceSink

EXTRACTOR = "graph"
EXTRACTOR_VERSION = "1.0.0"
REDRAFT_DROP_SHARE = 0.5

SYSTEM_PROMPT = """You extract machine-checkable rules from a U.S. CPSC product recall notice.
Return JSON matching the schema. Rules:
- Copy every string value exactly as printed in the notice (model numbers keep hyphens, spaces, and letters).
- models: model numbers or item codes that identify recalled units; not brand names, UPCs, or SKUs unless the
  notice presents them as model numbers.
- sold_at: one entry per named retailer or website from the SOLD AT section; omit generic phrases such as
  "other stores nationwide".
- sold_from and sold_to as YYYY-MM-DD; a month-only date means the first or last day of that month.
- price_range_cents in cents; units_affected is the U.S. unit count only.
- remedy_type is refund, repair, replacement, or multiple.
- destruction_instruction only when the consumer must destroy, cut, mark, or disassemble the product.
- registration_url only when the notice says to register, file a claim, or fill out a form at that URL.
- For every value, add an evidence item {field, quote, start: 0, end: 0} whose quote is copied verbatim from
  the notice. Offsets are computed later; leave them 0.
- Leave a field null or empty when the notice does not state it. Never guess."""

PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()


class ExtractionState(TypedDict, total=False):
    source_text: str
    calls: int
    draft: dict[str, Any] | None
    error: str | None
    rules: dict[str, Any] | None
    dropped: list[str]
    status: Literal["running", "done", "capped", "failed"]
    redraft: bool


@dataclass(frozen=True)
class ExtractionResult:
    rules: ExtractedRules | None
    status: str
    calls: int
    dropped: list[str]
    cost_usd: Decimal
    draft: ExtractedRules | None = None  # last model draft before verify_spans (diagnostics only)


def build_graph(llm: LlmClient, trace: TraceSink, run: RunBudget, max_calls: int) -> Any:
    schema = ExtractedRules.model_json_schema()

    def load_notice(state: ExtractionState) -> ExtractionState:
        text = state["source_text"]
        trace.step(
            "load_notice",
            thought="load canonical source_text",
            args={"chars": len(text)},
            result={"prompt_sha256": PROMPT_SHA256},
        )
        return {"calls": 0, "dropped": [], "status": "running", "redraft": False}

    def draft_rules(state: ExtractionState) -> ExtractionState:
        calls = state.get("calls", 0) + 1
        request = ModelRequest(
            system=SYSTEM_PROMPT,
            user_text=state["source_text"],
            output_schema=schema,
            max_tokens=llm.settings.llm_max_output_tokens,
            purpose="extract_rules",
        )
        t0 = time.monotonic()
        try:
            done = llm.complete(request, run)
        except (RunCapExceeded, BudgetExhausted) as exc:
            trace.step(
                "draft_rules",
                tool="llm",
                thought="refused by the cost meter before the call",
                result={"capped": str(exc)},
                latency_ms=round((time.monotonic() - t0) * 1000),
            )
            return {"calls": calls, "status": "capped", "error": str(exc), "draft": None}
        except ReplayMiss as exc:
            # No recording: nothing was sent, nothing is retried, and the attempt is not counted as a call.
            trace.step(
                "draft_rules",
                tool="llm",
                result={"replay_miss": str(exc)},
                latency_ms=round((time.monotonic() - t0) * 1000),
            )
            return {"calls": calls - 1, "status": "failed", "error": str(exc), "draft": None}
        except ModelCallFailed as exc:
            trace.step(
                "draft_rules",
                tool="llm",
                result={"error": str(exc)},
                latency_ms=round((time.monotonic() - t0) * 1000),
            )
            return {"calls": calls, "error": str(exc), "draft": None}
        usage = done.response.usage
        try:
            parsed = ExtractedRules.model_validate(json.loads(done.response.text))
            draft: dict[str, Any] | None = parsed.model_dump(mode="json")
            error = None
        except (ValueError, ValidationError) as exc:
            draft, error = None, f"invalid draft: {str(exc)[:300]}"
        trace.step(
            "draft_rules",
            tool="llm",
            args={"max_tokens": done.max_tokens, "attempt": calls},
            result={"error": error} if error else {"stop_reason": done.response.stop_reason},
            latency_ms=done.latency_ms,
            tokens_in=usage.total_input,
            tokens_out=usage.output_tokens,
            cost_usd=done.cost_usd,
        )
        return {"calls": calls, "draft": draft, "error": error}

    def verify(state: ExtractionState) -> ExtractionState:
        assert state.get("draft") is not None
        result = verify_spans(ExtractedRules.model_validate(state["draft"]), state["source_text"])
        redraft = result.dropped_share > REDRAFT_DROP_SHARE and state.get("calls", 0) < max_calls
        trace.step(
            "verify_spans",
            tool="verify_spans",
            result={"kept": result.kept, "dropped": result.dropped, "redraft": redraft},
        )
        return {"rules": result.rules.model_dump(mode="json"), "dropped": result.dropped, "redraft": redraft}

    def finalize(state: ExtractionState) -> ExtractionState:
        status = state.get("status", "running")
        if status == "running":
            status = "done" if state.get("rules") is not None else "failed"
        trace.step(
            "finalize",
            result={
                "status": status,
                "calls": state.get("calls", 0),
                "dropped": len(state.get("dropped", [])),
            },
        )
        return {"status": status}

    def after_draft(state: ExtractionState) -> str:
        if state.get("status") in ("capped", "failed"):
            return "finalize"
        if state.get("draft") is None:
            return "draft_rules" if state.get("calls", 0) < max_calls else "finalize"
        return "verify_spans"

    def after_verify(state: ExtractionState) -> str:
        return "draft_rules" if state.get("redraft") else "finalize"

    graph = StateGraph(ExtractionState)
    graph.add_node("load_notice", load_notice)
    graph.add_node("draft_rules", draft_rules)
    graph.add_node("verify_spans", verify)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_notice")
    graph.add_edge("load_notice", "draft_rules")
    graph.add_conditional_edges("draft_rules", after_draft, ["draft_rules", "verify_spans", "finalize"])
    graph.add_conditional_edges("verify_spans", after_verify, ["draft_rules", "finalize"])
    graph.add_edge("finalize", END)
    return graph.compile()


def extract(source_text: str, llm: LlmClient, trace: TraceSink) -> ExtractionResult:
    settings = llm.settings
    run = RunBudget(
        max_steps=settings.extract_max_calls,
        max_usd=settings.extract_run_cap_usd,
        max_seconds=settings.run_cap_seconds,
    )
    app = build_graph(llm, trace, run, settings.extract_max_calls)
    final: ExtractionState = app.invoke({"source_text": source_text})
    rules = ExtractedRules.model_validate(final["rules"]) if final.get("rules") is not None else None
    status = final.get("status", "failed")
    trace.finish(
        "done" if status == "done" else ("capped" if status == "capped" else "failed"),
        {"status": status, "calls": final.get("calls", 0)},
    )
    return ExtractionResult(
        rules=rules,
        status=status,
        calls=final.get("calls", 0),
        dropped=final.get("dropped", []),
        cost_usd=run.cost_usd,
        draft=ExtractedRules.model_validate(final["draft"]) if final.get("draft") is not None else None,
    )
