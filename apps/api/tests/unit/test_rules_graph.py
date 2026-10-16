"""Extraction graph with a fake model (no network): span verification, redraft cap, meter caps, trace rows."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from recallops.llm.client import FakeTransport, LlmClient, ModelResponse
from recallops.llm.meter import CostMeter, InMemoryLedger
from recallops.llm.pricing import PriceTable, Usage
from recallops.rules.graph import extract
from recallops.rules.schemas import ExtractedRules
from recallops.rules.verify import verify_spans
from recallops.settings import Settings
from recallops.tracing.writer import MemoryTrace

MODEL = "claude-haiku-5-5"
PRICES = PriceTable(MODEL, Decimal("0.10"), Decimal("0.50"), 100_000, Decimal("0.50"), Decimal("2.50"))
NOTICE = """TITLE:
Acme Recalls Widgets

DESCRIPTION:
This recall involves Acme widgets with model number AB-1234.

PRODUCTS:
Acme Widgets; units: About 500

SOLD AT:
Target from January 2024 through February 2025 for about $20.

REMEDY:
Consumers should contact Acme for a full refund.
Remedy options: Refund

CONTACT:
Acme at 800-555-0100.
"""


def draft(models: list[str], extra_evidence: list[dict[str, Any]] | None = None) -> str:
    evidence = [
        {"field": "units_affected", "quote": "About 500", "start": 0, "end": 0},
        {"field": "sold_from", "quote": "January 2024", "start": 0, "end": 0},
    ]
    return json.dumps(
        {
            "identification": {
                "models": models,
                "sold_at": ["Target"],
                "units_affected": 500,
                "sold_from": "2024-01-01",
                "evidence": evidence + (extra_evidence or []),
            },
            "remedy": {
                "remedy_type": "refund",
                "contact": "800-555-0100",
                "evidence": [{"field": "remedy_type", "quote": "full refund", "start": 0, "end": 0}],
            },
        }
    )


def reply(text: str) -> ModelResponse:
    return ModelResponse(text=text, usage=Usage(3000, 400), stop_reason="end_turn", model_id=MODEL)


def run_graph(
    responses: list[ModelResponse], settings: Settings | None = None, spent: Decimal = Decimal(0)
) -> tuple[Any, FakeTransport, MemoryTrace]:
    settings = settings or Settings()
    transport = FakeTransport(responses)
    meter = CostMeter(PRICES, InMemoryLedger(spent), Decimal("2.00"), Decimal("1.50"))
    trace = MemoryTrace(graph="extract_rules")
    result = extract(NOTICE, LlmClient(transport, meter, MODEL, settings), trace)
    return result, transport, trace


def test_happy_path_keeps_grounded_values_and_drops_invented_ones() -> None:
    result, transport, trace = run_graph([reply(draft(["AB-1234", "ZZ-9999"]))])
    assert result.status == "done"
    assert result.rules is not None
    assert result.rules.identification.models == ["AB-1234"]
    assert result.dropped == ["models:ZZ-9999"]
    assert result.rules.identification.units_affected == 500
    assert len(transport.calls) == 1
    assert [s.node for s in trace.steps] == ["load_notice", "draft_rules", "verify_spans", "finalize"]
    assert trace.steps[1].cost_usd == result.cost_usd > 0
    assert trace.status == "done"
    spans = [*result.rules.identification.evidence, *result.rules.remedy.evidence]
    assert all(NOTICE[s.start : s.end] == s.quote for s in spans)


def test_invalid_json_is_redrafted_once() -> None:
    result, transport, trace = run_graph([reply("not json"), reply(draft(["AB-1234"]))])
    assert result.status == "done"
    assert len(transport.calls) == 2
    assert [s.node for s in trace.steps].count("draft_rules") == 2


def test_two_invalid_drafts_fail_after_the_call_cap() -> None:
    result, transport, trace = run_graph([reply("{}x"), reply("still bad"), reply(draft(["AB-1234"]))])
    assert result.status == "failed"
    assert result.rules is None
    assert len(transport.calls) == 2
    assert trace.status == "failed"


def test_mostly_ungrounded_draft_is_redrafted() -> None:
    hallucinated = reply(draft(["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]))
    result, transport, _ = run_graph([hallucinated, reply(draft(["AB-1234"]))])
    assert len(transport.calls) == 2
    assert result.rules is not None and result.rules.identification.models == ["AB-1234"]


def test_run_cost_cap_ends_capped_without_calling() -> None:
    result, transport, trace = run_graph([reply(draft(["AB-1234"]))], Settings(extract_run_cap_usd=Decimal("0.0001")))
    assert result.status == "capped"
    assert transport.calls == []
    assert trace.status == "capped"
    assert trace.steps[1].result is not None and "capped" in trace.steps[1].result


def test_global_budget_exhausted_ends_capped_without_calling() -> None:
    result, transport, _ = run_graph([reply(draft(["AB-1234"]))], spent=Decimal("2.00"))
    assert result.status == "capped"
    assert transport.calls == []


def test_verify_drops_typed_values_without_a_verbatim_quote() -> None:
    rules = ExtractedRules.model_validate(json.loads(draft(["AB-1234"])))
    rules.identification.evidence = []
    checked = verify_spans(rules, NOTICE)
    assert checked.rules.identification.units_affected is None
    assert checked.rules.identification.sold_from is None
    assert "units_affected" in checked.dropped
    assert checked.rules.identification.models == ["AB-1234"]


def test_replay_miss_fails_fast_without_counting_a_call(tmp_path: object) -> None:
    from pathlib import Path

    from recallops.llm.client import ReplayTransport

    transport = ReplayTransport(Path(str(tmp_path)))
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal("2.00"), Decimal("1.50"))
    trace = MemoryTrace(graph="extract_rules")
    result = extract(NOTICE, LlmClient(transport, meter, MODEL, Settings()), trace)
    assert (result.status, result.calls, result.cost_usd) == ("failed", 0, Decimal(0))
    assert len(transport.calls) == 1
    assert [s.node for s in trace.steps] == ["load_notice", "draft_rules", "finalize"]
