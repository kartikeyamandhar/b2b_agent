"""Rules-only adjudicator: the M1 baseline row of the ablation table (01-backend.md section 6).

Pure-code tools in a fixed order and a fixed decision table implementing evals/adjudication/outcome_policy.yaml
(precedence flag > deny > needs_info > approve). No model, no vision: the destruction check is presence only.
It writes agent_runs (graph = rules_only) and one agent_steps row per tool through the trace sink, so steps,
latency, and its zero cost come from the same trace path as the agent's.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from recallops.claims.tools import (
    PHASH_KINDS,
    ClaimFacts,
    check_date_code,
    check_destruction_presence,
    check_duplicates,
    check_model,
    check_purchase,
    check_serial,
)
from recallops.logs import get_logger, log_event
from recallops.rules.schemas import ExtractedRules
from recallops.tracing.writer import TraceSink

log = get_logger("claims.rules_only")

Outcome = Literal["approve", "deny", "needs_info", "flag"]
PRECEDENCE: tuple[Outcome, ...] = ("flag", "deny", "needs_info", "approve")
DECISION_TABLE_VERSION = "1.0.0"


@dataclass
class Adjudication:
    outcome: Outcome
    reasons: list[str]
    cited_rules: list[str]
    routed_to_human: bool
    duplicate_kinds: list[str] = field(default_factory=list)
    duplicate_with: list[str] = field(default_factory=list)
    steps: int = 0
    latency_ms: float = 0.0


def _timed(trace: TraceSink, name: str, args: dict[str, Any], fn: Callable[[], BaseModel]) -> Any:
    t0 = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - t0) * 1000
    trace.step(name, tool=name, args=args, result=result.model_dump(mode="json"), latency_ms=round(elapsed))
    return result


def adjudicate(
    claim: ClaimFacts,
    rules: ExtractedRules,
    program_claims: list[ClaimFacts],
    value_threshold_cents: int,
    trace: TraceSink,
    phash_bits: int = 6,
    phash_kinds: frozenset[str] = PHASH_KINDS,
) -> Adjudication:
    t0 = time.perf_counter()
    ident, remedy = rules.identification, rules.remedy
    model = _timed(trace, "check_model", {"model_claimed": claim.model_claimed}, lambda: check_model(claim, ident))
    serial = _timed(trace, "check_serial", {"serial_claimed": claim.serial_claimed}, lambda: check_serial(claim, ident))
    code = _timed(
        trace, "check_date_code", {"date_code_claimed": claim.date_code_claimed}, lambda: check_date_code(claim, ident)
    )
    purchase = _timed(
        trace,
        "check_purchase",
        {"retailer_claimed": claim.retailer_claimed, "purchase_date": str(claim.purchase_date)},
        lambda: check_purchase(claim, rules),
    )
    destruction = _timed(
        trace,
        "check_destruction_presence",
        {"photos": [p.kind for p in claim.photos]},
        lambda: check_destruction_presence(claim, remedy, phash_bits),
    )
    dups = _timed(
        trace,
        "check_duplicates",
        {"program_claims": len(program_claims)},
        lambda: check_duplicates(claim, program_claims, phash_bits, phash_kinds),
    )

    triggered: dict[Outcome, list[str]] = {o: [] for o in PRECEDENCE}
    cited: list[str] = []
    for hit in dups.hits:
        triggered["flag"].append(f"duplicate {hit.kind} with claim {hit.other_claim_id} ({hit.detail})")
    if model.verdict == "mismatch":
        triggered["deny"].append("claimed model is not a recalled model")
    elif model.verdict in ("not_provided", "ambiguous"):
        triggered["needs_info"].append(f"model {model.verdict}")
    if serial.verdict == "out_of_range":
        triggered["deny"].append("serial outside the recalled ranges")
    elif serial.verdict == "not_provided":
        triggered["needs_info"].append("serial number needed")
    if code.verdict == "out_of_range":
        triggered["deny"].append("date code outside the recalled range")
    elif code.verdict in ("not_provided", "unparsed"):
        triggered["needs_info"].append(f"date code {code.verdict}")
    if purchase.window == "before":
        triggered["deny"].append("purchased before the recalled units were sold")
    elif purchase.window in ("after", "unknown"):
        triggered["needs_info"].append(f"purchase date {purchase.window}")
    if purchase.retailer in ("not_listed", "unknown"):
        triggered["needs_info"].append(f"retailer {purchase.retailer}; proof of purchase needed")
    if purchase.receipt == "missing":
        triggered["needs_info"].append("receipt required")
    if destruction.verdict == "missing":
        triggered["needs_info"].append(f"destruction photo needed: {destruction.reason}")
    for result in (model, serial, code):
        if result.cited_rule:
            cited.append(result.cited_rule)
    outcome: Outcome = next(o for o in PRECEDENCE if triggered[o] or o == "approve")
    reasons = triggered[outcome] or ["every check passed"]
    over_threshold = (claim.refund_cents or 0) > value_threshold_cents
    routed = outcome == "flag" or (outcome == "approve" and over_threshold)
    trace.step(
        "decide",
        tool="decision_table",
        args={"version": DECISION_TABLE_VERSION, "value_threshold_cents": value_threshold_cents},
        result={"outcome": outcome, "reasons": reasons, "routed_to_human": routed},
    )
    latency = (time.perf_counter() - t0) * 1000
    trace.finish("done", {"outcome": outcome, "routed_to_human": routed})
    log_event(
        log,
        "claim_adjudicated",
        claim_id=str(claim.claim_id),
        outcome=outcome,
        routed=routed,
        duration_ms=round(latency, 2),
    )
    return Adjudication(
        outcome=outcome,
        reasons=reasons,
        cited_rules=cited,
        routed_to_human=routed,
        duplicate_kinds=sorted({h.kind for h in dups.hits}),
        duplicate_with=sorted({str(h.other_claim_id) for h in dups.hits}),
        steps=7,
        latency_ms=latency,
    )
