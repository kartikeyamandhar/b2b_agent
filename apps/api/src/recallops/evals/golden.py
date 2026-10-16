"""Golden notices: build from quote-carrying labels, load, and validate against source_text."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from recallops.rules.schemas import (
    DateCodeRule,
    ExtractedRules,
    IdentificationRules,
    Payout,
    PriceRange,
    RemedyOption,
    RemedyRules,
    SerialRange,
    Span,
)
from recallops.text import find_span, sha256_text

LABEL_STATUS = "model-labeled, unaudited"


class GoldenNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    recall_date: date
    title: str
    category: str
    source_text_sha256: str
    canonicalizer_version: str
    label_status: str
    rules: ExtractedRules
    notes: str = ""
    baseline_test: bool = False
    synth_source: bool = False


class QuoteNotFound(ValueError):
    pass


def _span(text: str, field: str, quote: str) -> Span:
    found = find_span(text, quote)
    if found is None:
        raise QuoteNotFound(f"{field}: quote not in source_text: {quote!r}")
    return Span(field=field, quote=quote, start=found[0], end=found[1])


def build_rules(label: dict[str, Any], text: str) -> ExtractedRules:
    """Turn one labeler record (values plus quotes) into rules with computed spans."""
    id_ev: list[Span] = []
    rem_ev: list[Span] = []

    def pair(field: str, item: Any, evidence: list[Span]) -> Any:
        if item is None:
            return None
        value, quote = item[0], item[-1]
        evidence.append(_span(text, field, quote))
        return value

    models = [pair("models", m, id_ev) for m in label["models"]]
    serials = []
    for s in label["serial_ranges"]:
        id_ev.append(_span(text, "serial_ranges", s["raw"]))
        serials.append(SerialRange(**s))
    codes = []
    for d in label["date_codes"]:
        id_ev.append(_span(text, "date_codes", d["raw"]))
        codes.append(DateCodeRule(**d))
    price = None
    if label["price_range"] is not None:
        lo, hi, quote = label["price_range"]
        id_ev.append(_span(text, "price_range_cents", quote))
        price = PriceRange(min_cents=lo, max_cents=hi)
    sold_from = pair("sold_from", label["sold_from"], id_ev)
    sold_to = pair("sold_to", label["sold_to"], id_ev)
    identification = IdentificationRules(
        models=models,
        model_label_location=pair("model_label_location", label["model_label_location"], id_ev),
        serial_ranges=serials,
        date_codes=codes,
        sold_at=[pair("sold_at", s, id_ev) for s in label["sold_at"]],
        sold_from=date.fromisoformat(sold_from) if sold_from else None,
        sold_to=date.fromisoformat(sold_to) if sold_to else None,
        price_range_cents=price,
        units_affected=pair("units_affected", label["units_affected"], id_ev),
        evidence=id_ev,
    )
    remedy = RemedyRules(
        remedy_type=pair("remedy_type", label["remedy_type"], rem_ev),
        options=[
            RemedyOption(
                type=o["type"],
                steps=o["steps"],
                proof_required=o["proof_required"],
                payout=Payout(method=o["payout_method"], amount_rule=o["amount_rule"]),
            )
            for o in label["options"]
        ],
        destruction_instruction=pair("destruction_instruction", label["destruction_instruction"], rem_ev),
        registration_url=pair("registration_url", label["registration_url"], rem_ev),
        contact=pair("contact", label["contact"], rem_ev),
        evidence=rem_ev,
    )
    return ExtractedRules(identification=identification, remedy=remedy)


def load_golden(path: Path) -> list[GoldenNotice]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [GoldenNotice.model_validate(n) for n in payload["notices"]]


def notice_text(notices_dir: Path, source_id: str) -> str:
    return (notices_dir / f"{source_id}.txt").read_text(encoding="utf-8")


def validate_golden(notices: list[GoldenNotice], notices_dir: Path) -> list[str]:
    """Every span quote must equal source_text[start:end] and the text hash must match."""
    problems: list[str] = []
    for n in notices:
        text = notice_text(notices_dir, n.source_id)
        if sha256_text(text) != n.source_text_sha256:
            problems.append(f"{n.source_id}: source_text sha256 mismatch")
        for span in [*n.rules.identification.evidence, *n.rules.remedy.evidence]:
            if text[span.start : span.end] != span.quote:
                problems.append(f"{n.source_id}: span {span.field} does not match text at {span.start}")
    return problems
