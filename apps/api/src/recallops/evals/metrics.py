"""Extraction metrics exactly as pre-registered in evals/extraction/PREREG.md."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from recallops.rules.schemas import ExtractedRules

HEADLINE_FIELDS = (
    "models",
    "serial_ranges",
    "date_codes",
    "sold_at",
    "sold_from",
    "sold_to",
    "price_range_cents",
    "units_affected",
    "remedy_type",
    "has_destruction_instruction",
    "registration_url",
)

_EDGE_PUNCT = " \t\n.,;:\"'()[]"


def norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip(_EDGE_PUNCT)


def norm_model(value: str | None) -> str:
    return re.sub(r"[\s\-]", "", norm(value))


def norm_url(value: str | None) -> str:
    text = norm(value)
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/")


def norm_seller(value: str | None) -> str:
    return norm_url(value)


def set_f1(gold: set[object], pred: set[object]) -> float:
    if not gold and not pred:
        return 1.0
    if not gold or not pred:
        return 0.0
    tp = len(gold & pred)
    if tp == 0:
        return 0.0
    precision, recall = tp / len(pred), tp / len(gold)
    return 2 * precision * recall / (precision + recall)


def field_score(field: str, gold: ExtractedRules, pred: ExtractedRules) -> float:
    gi, pi, gr, pr = gold.identification, pred.identification, gold.remedy, pred.remedy
    if field == "models":
        return set_f1({norm_model(m) for m in gi.models}, {norm_model(m) for m in pi.models})
    if field == "serial_ranges":
        key: Callable[[object], object] = lambda s: (norm(s.prefix), norm(s.start), norm(s.end))  # type: ignore[attr-defined]  # noqa: E731
        return set_f1({key(s) for s in gi.serial_ranges}, {key(s) for s in pi.serial_ranges})
    if field == "date_codes":
        return set_f1(
            {(norm(d.start), norm(d.end)) for d in gi.date_codes},
            {(norm(d.start), norm(d.end)) for d in pi.date_codes},
        )
    if field == "sold_at":
        return set_f1({norm_seller(s) for s in gi.sold_at}, {norm_seller(s) for s in pi.sold_at})
    if field == "sold_from":
        return float(gi.sold_from == pi.sold_from)
    if field == "sold_to":
        return float(gi.sold_to == pi.sold_to)
    if field == "price_range_cents":
        return float(gi.price_range_cents == pi.price_range_cents)
    if field == "units_affected":
        return float(gi.units_affected == pi.units_affected)
    if field == "remedy_type":
        return float(gr.remedy_type == pr.remedy_type)
    if field == "has_destruction_instruction":
        return float(bool(gr.destruction_instruction) == bool(pr.destruction_instruction))
    if field == "registration_url":
        return float(norm_url(gr.registration_url) == norm_url(pr.registration_url))
    raise ValueError(f"unknown field {field}")


def span_grounding(pred: ExtractedRules, source_text: str) -> tuple[int, int]:
    """(grounded spans, total spans): a span is grounded when its quote equals source_text[start:end]."""
    spans = [*pred.identification.evidence, *pred.remedy.evidence]
    grounded = sum(1 for s in spans if source_text[s.start : s.end] == s.quote)
    return grounded, len(spans)


@dataclass(frozen=True)
class FieldTable:
    n: int
    per_field: dict[str, float]  # points, 0 to 100
    headline: float
    span_grounded_rate: float | None


def score(
    pairs: Sequence[tuple[ExtractedRules, ExtractedRules | None, str]],
    fields: Iterable[str] = HEADLINE_FIELDS,
) -> FieldTable:
    """pairs: (gold, prediction or None for a failed or capped extraction, source_text)."""
    fields = tuple(fields)
    empty = ExtractedRules.model_validate({"identification": {}, "remedy": {}})
    sums = dict.fromkeys(fields, 0.0)
    grounded = total = 0
    for gold, pred, text in pairs:
        prediction = pred or empty
        for f in fields:
            sums[f] += field_score(f, gold, prediction)
        g, t = span_grounding(prediction, text)
        grounded += g
        total += t
    n = len(pairs)
    per_field = {f: round(100 * sums[f] / n, 1) if n else 0.0 for f in fields}
    headline = round(sum(per_field.values()) / len(fields), 1) if fields else 0.0
    return FieldTable(
        n=n, per_field=per_field, headline=headline, span_grounded_rate=(grounded / total) if total else None
    )
