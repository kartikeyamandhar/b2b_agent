"""Scripted model responses for agent graph tests (CLAUDE.md: agent graphs are tested with a fake model that
replays recorded tool results)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from recallops.claims.agent_tools import ToolContext
from recallops.claims.tools import ClaimFacts, PhotoRef
from recallops.llm.client import FakeTransport, LlmClient, ModelResponse
from recallops.llm.meter import CostMeter, InMemoryLedger
from recallops.llm.pricing import PriceTable, Usage
from recallops.rules.schemas import ExtractedRules, IdentificationRules, RemedyOption, RemedyRules
from recallops.settings import Settings

MODEL = "claude-haiku-5-5"
PRICES = PriceTable(MODEL, Decimal("0.10"), Decimal("0.50"), 100_000, Decimal("0.50"), Decimal("2.50"))


def tool_use(i: int, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{i:02d}", "name": name, "input": args or {}}


def turn(*blocks: dict[str, Any], text: str = "", tokens_in: int = 1500, tokens_out: int = 100) -> ModelResponse:
    content = ([{"type": "text", "text": text}] if text else []) + list(blocks)
    return ModelResponse(
        text=text, usage=Usage(tokens_in, tokens_out), stop_reason="tool_use", model_id=MODEL, content=tuple(content)
    )


def vision(payload: dict[str, Any]) -> ModelResponse:
    return ModelResponse(text=json.dumps(payload), usage=Usage(700, 50), stop_reason="end_turn", model_id=MODEL)


def label(model_text: str | None, legible: bool = True) -> ModelResponse:
    return vision(
        {"legible": legible, "model_text": model_text, "serial_text": None, "date_code_text": None, "confidence": 0.9}
    )


def destruction(complies: bool) -> ModelResponse:
    return vision({"shows_product": True, "complies": complies, "observed": "a cord", "confidence": 0.8})


def decide(i: int, outcome: str, cites: list[str], question: str | None = None) -> ModelResponse:
    args = {
        "outcome": outcome,
        "reasons": [{"text": f"{outcome} per the checks", "cites": cites}],
        "question_for_claimant": question,
        "confidence": 0.9,
    }
    return turn(tool_use(i, "decide", args))


def client(responses: list[ModelResponse], settings: Settings | None = None) -> tuple[LlmClient, FakeTransport]:
    transport = FakeTransport(responses)
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1))
    return LlmClient(transport, meter, MODEL, settings or Settings()), transport


def rules(destroy: bool = True) -> ExtractedRules:
    return ExtractedRules(
        identification=IdentificationRules(models=["TL1302Z"], sold_at=["Amazon.com"]),
        remedy=RemedyRules(
            remedy_type="refund",
            options=[RemedyOption(type="refund", proof_required=["destruction_photo"] if destroy else [])],
            destruction_instruction="cut the power cord" if destroy else None,
        ),
    )


def facts(claim_id: int = 1, model: str | None = "TL1302Z", refund: int = 4999, **kw: Any) -> ClaimFacts:
    photos = (
        PhotoRef(kind="label", sha256=f"l{claim_id}", phash="0f0f0f0f0f0f0f0f"),
        PhotoRef(kind="destruction", sha256=f"d{claim_id}", phash="f0f0f0f0f0f0f0f0"),
    )
    return ClaimFacts(
        claim_id=UUID(int=claim_id),
        model_claimed=model,
        retailer_claimed="Amazon.com",
        refund_cents=refund,
        email=f"c{claim_id}@example.com",
        address=f"{claim_id} Main St",
        photos=kw.pop("photos", photos),
        **kw,
    )


def context(
    llm: LlmClient, claim: ClaimFacts, rule_set: ExtractedRules, others: list[ClaimFacts] | None = None
) -> ToolContext:
    return ToolContext(
        claim=claim,
        rules=rule_set,
        program_id=UUID(int=99),
        value_threshold_cents=10_000,
        others=lambda: list(others or []),
        photo_bytes=lambda kind: None,
        retriever=None,
        llm=llm,
    )
