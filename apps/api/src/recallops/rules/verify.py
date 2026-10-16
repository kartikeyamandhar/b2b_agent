"""verify_spans: every extracted value must be grounded verbatim in source_text; others are dropped."""

from __future__ import annotations

from dataclasses import dataclass, field

from recallops.rules.schemas import ExtractedRules, Span
from recallops.text import find_span


@dataclass
class Verification:
    rules: ExtractedRules
    kept: int = 0
    dropped: list[str] = field(default_factory=list)

    @property
    def dropped_share(self) -> float:
        total = self.kept + len(self.dropped)
        return len(self.dropped) / total if total else 0.0


def _ground(text: str, field_name: str, quote: str | None, out: list[Span]) -> bool:
    if not quote:
        return False
    found = find_span(text, quote)
    if found is None:
        return False
    out.append(Span(field=field_name, quote=quote, start=found[0], end=found[1]))
    return True


def verify_spans(rules: ExtractedRules, source_text: str) -> Verification:
    """String values must appear verbatim. Typed values (dates, price, units, remedy type) are kept only when
    the extractor supplied an evidence quote for that field that appears verbatim."""
    ident = rules.identification.model_copy(deep=True)
    remedy = rules.remedy.model_copy(deep=True)
    quotes = {s.field: s.quote for s in [*ident.evidence, *remedy.evidence]}
    id_ev: list[Span] = []
    rem_ev: list[Span] = []
    v = Verification(rules=rules)

    def keep(ok: bool, label: str) -> bool:
        if ok:
            v.kept += 1
        else:
            v.dropped.append(label)
        return ok

    ident.models = [m for m in ident.models if keep(_ground(source_text, "models", m, id_ev), f"models:{m}")]
    ident.sold_at = [s for s in ident.sold_at if keep(_ground(source_text, "sold_at", s, id_ev), f"sold_at:{s}")]
    ident.serial_ranges = [
        s for s in ident.serial_ranges if keep(_ground(source_text, "serial_ranges", s.raw, id_ev), f"serial:{s.raw}")
    ]
    ident.date_codes = [
        d for d in ident.date_codes if keep(_ground(source_text, "date_codes", d.raw, id_ev), f"date_code:{d.raw}")
    ]
    if ident.model_label_location is not None and not keep(
        _ground(source_text, "model_label_location", ident.model_label_location, id_ev),
        "model_label_location",
    ):
        ident.model_label_location = None
    for name in ("sold_from", "sold_to", "price_range_cents", "units_affected"):
        if getattr(ident, name) is not None and not keep(_ground(source_text, name, quotes.get(name), id_ev), name):
            setattr(ident, name, None)
    for name in ("destruction_instruction", "registration_url", "contact"):
        value = getattr(remedy, name)
        if value is not None and not keep(_ground(source_text, name, value, rem_ev), name):
            setattr(remedy, name, None)
    if remedy.remedy_type is not None and not keep(
        _ground(source_text, "remedy_type", quotes.get("remedy_type"), rem_ev), "remedy_type"
    ):
        remedy.remedy_type = None
    ident.evidence = id_ev
    remedy.evidence = rem_ev
    v.rules = ExtractedRules(identification=ident, remedy=remedy)
    return v
