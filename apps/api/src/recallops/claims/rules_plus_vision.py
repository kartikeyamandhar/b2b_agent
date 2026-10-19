"""The rules_plus_vision ablation row (PREREG Revision 2): the rules-only pure-code checks, plus the two vision tools,
decided by decision table 2.0.0 (claims/policy.py), which implements outcome_policy.yaml including photo_rules.

The vision calls go through the same tool executor as the agent, so their requests are byte-identical to the
agent's own read_label_photo and check_destruction_photo calls and share recordings.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from recallops.claims.agent_tools import ToolContext, execute
from recallops.claims.policy import DECISION_TABLE_VERSION, TableInputs, decide_table
from recallops.claims.tools import (
    check_date_code,
    check_duplicates,
    check_model,
    check_purchase,
    check_serial,
)
from recallops.claims.tools.checks import check_destruction_presence, destruction_required
from recallops.claims.vision import DestructionReading, LabelReading
from recallops.llm.meter import RunBudget, RunCapExceeded
from recallops.tracing.writer import TraceSink


@dataclass(frozen=True)
class VisionTableResult:
    outcome: str
    reasons: list[str]
    routed_to_human: bool
    capped: bool


def _cost(tr: Any) -> dict[str, Any]:
    done = tr.completion
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


def adjudicate_with_vision(ctx: ToolContext, run: RunBudget, trace: TraceSink) -> VisionTableResult:
    claim, rules = ctx.claim, ctx.rules
    ident, remedy = rules.identification, rules.remedy

    def step(name: str, args: dict[str, Any], fn: Any) -> Any:
        t0 = time.perf_counter()
        result = fn()
        trace.step(
            name,
            tool=name,
            args=args,
            result=result.model_dump(mode="json"),
            latency_ms=round((time.perf_counter() - t0) * 1000),
        )
        return result

    typed = step("check_model", {"model_claimed": claim.model_claimed}, lambda: check_model(claim, ident))
    serial = step("check_serial", {"serial_claimed": claim.serial_claimed}, lambda: check_serial(claim, ident))
    code = step(
        "check_date_code", {"date_code_claimed": claim.date_code_claimed}, lambda: check_date_code(claim, ident)
    )
    purchase = step(
        "check_purchase", {"retailer_claimed": claim.retailer_claimed}, lambda: check_purchase(claim, rules)
    )
    presence = step("check_destruction_presence", {}, lambda: check_destruction_presence(claim, remedy, ctx.phash_bits))
    dups = step(
        "check_duplicates",
        {"program_claims": len(ctx.other_claims())},
        lambda: check_duplicates(claim, ctx.other_claims(), ctx.phash_bits, ctx.phash_kinds),
    )
    label: LabelReading | None = None
    label_model = None
    destruction: DestructionReading | None = None
    try:
        if claim.photo("label") is not None:
            tr = execute("read_label_photo", {}, ctx, run)
            trace.step("read_label_photo", tool="read_label_photo", result=tr.result, counted=tr.counted, **_cost(tr))
            if tr.error is None:
                label = LabelReading.model_validate(tr.result)
                if label.legible and label.model_text:
                    label_model = check_model(claim.model_copy(update={"model_claimed": label.model_text}), ident)
        if destruction_required(remedy) and presence.verdict == "present":
            tr = execute("check_destruction_photo", {}, ctx, run)
            trace.step(
                "check_destruction_photo",
                tool="check_destruction_photo",
                result=tr.result,
                counted=tr.counted,
                **_cost(tr),
            )
            if tr.error is None and tr.result.get("verdict") == "checked":
                destruction = DestructionReading.model_validate({k: v for k, v in tr.result.items() if k != "verdict"})
    except RunCapExceeded as exc:
        trace.step("decide", tool="decision_table", result={"capped": str(exc)})
        return VisionTableResult("capped", [f"capped: {exc}"], routed_to_human=True, capped=True)
    table = decide_table(
        TableInputs(
            typed_model=typed,
            serial=serial,
            date_code=code,
            purchase=purchase,
            duplicates=dups,
            destruction_required=destruction_required(remedy),
            destruction_presence=presence.verdict,
            destruction_reason=presence.reason,
            label=label,
            label_model=label_model,
            destruction=destruction,
        )
    )
    over = table.outcome == "approve" and (claim.refund_cents or 0) > ctx.value_threshold_cents
    routed = table.outcome == "flag" or over
    trace.step(
        "decide",
        tool="decision_table",
        args={"version": DECISION_TABLE_VERSION},
        result={"outcome": table.outcome, "reasons": table.reasons, "routed_to_human": routed},
    )
    return VisionTableResult(table.outcome, table.reasons, routed, capped=False)
