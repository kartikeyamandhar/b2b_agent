"""Regex and heuristics extractor (01-backend.md section 5, extractor 1). No model calls.

Reads only source_text. Every value it emits carries an evidence span located verbatim in source_text.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from recallops.ingest.units import parse_units_text
from recallops.rules.schemas import (
    DateCodeRule,
    ExtractedRules,
    IdentificationRules,
    PriceRange,
    ProofKind,
    RemedyKind,
    RemedyOption,
    RemedyRules,
    RemedyType,
    SerialRange,
    Span,
)
from recallops.text import find_span, section_bounds

EXTRACTOR = "baseline"
EXTRACTOR_VERSION = "1.0.0"

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTH_RE = "|".join(m for m in calendar.month_name if m)
_WHEN = rf"(?:(?:{_MONTH_RE})(?: of)? \d{{4}}|\d{{4}})"
_SOLD_WINDOW = re.compile(rf"(?:from|between) ({_WHEN}) (?:through|to|and|until) ({_WHEN})")
_SOLD_FROM_ONLY = re.compile(rf"(?:from|since) ({_WHEN})")
_PRICE_RANGE = re.compile(r"for between \$([\d,]+(?:\.\d+)?) and \$([\d,]+(?:\.\d+)?)")
_PRICE_ONE = re.compile(r"for (?:about|around|approximately) \$([\d,]+(?:\.\d+)?)")
# A code token: uppercase letters, digits and . - / *, at least 3 characters and at least one digit.
_CODE = r"(?=[A-Z0-9.\-/*]*\d)[A-Z0-9][A-Z0-9.\-/*]{2,}"
_CODE_RE = re.compile(rf"(?<![\d,.$])\b(?:[A-Z]{{2,}} - {_CODE}|{_CODE})")
_MODEL_INTRO = re.compile(r"\b(?:model|item)s?(?: numbers?| no\.?| #)?:?\s", re.IGNORECASE)
_SERIAL_PREFIX = re.compile(
    r"(?i:serial (?:numbers?|prefix)[^.]{0,60}?(?:begin(?:ning)?|start(?:ing)?|prefix)(?: with)?(?: of)?)"
    r"(?P<tokens>(?:,?\s*(?:and |or )?(?:a |an )?(?:\"[A-Z0-9]{1,6}[,.]?\"|[A-Z0-9]{2,6}\b))+)",
)
_PREFIX_TOKEN = re.compile(r"\"([A-Z0-9]{1,6})[,.]?\"|\b([A-Z0-9]*\d[A-Z0-9]*|[A-Z]{2,6})\b")
_MASKED_PAIR = re.compile(r"\b(\w*X{3,})\s+(\w*X{3,})\b")
_SERIAL_RANGE = re.compile(rf"\b({_CODE})\s?(?:thru|through|to|-)\s?({_CODE})\b")
_BETWEEN_CODES = re.compile(rf"[Bb]etween ({_CODE}) and ({_CODE})")
_DATE_CODE = re.compile(
    r"date codes? (?:of |between |ranging from |from )?(?:either )?\"?([0-9][0-9/\-]{2,6})\"?(?: NH)?"
    r",? (?:and|to|through|or|-) \"?([0-9][0-9/\-]{2,6})\"?",
    re.IGNORECASE,
)
_MANUFACTURED = re.compile(
    rf"(?:manufactured|produced|made) (?:between|from|in) ((?:{_MONTH_RE}) \d{{4}})"
    rf"(?: (?:and|through|to) ((?:{_MONTH_RE}) \d{{4}}))?",
)
_MODEL_YEARS = re.compile(r"model years? (\d{4})(?:\s?-\s?(\d{4}))?", re.IGNORECASE)
_PHONE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL = re.compile(r"(?:https?://|www\.)[^\s,;()\"]+[^\s,;().\"]")
_DOMAIN = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|co|net|org|us)\b/?", re.IGNORECASE)
_DESTRUCTION = re.compile(
    r"[^.]*\b(?:cut(?:ting)?|destroy|disassembl\w*|write \"?recall\w*|writing \"?recall\w*|throw away|"
    r"written on|permanent marker)\b[^.]*",
    re.IGNORECASE,
)
_LABEL_LOCATION = re.compile(
    r"model(?: number)?s?(?:,? (?:date code,? )?and serial numbers?(?: information)?)? (?:is|are) "
    r"(?:also )?((?:located|printed|found|stamped|engraved)[^.]*)",
    re.IGNORECASE,
)
_GENERIC_SELLERS = re.compile(
    r"^(?:other|independent|local|select|various|and|online|in-store|stores?|retailers?|dealers? nationwide)\b",
    re.IGNORECASE,
)
_NOT_MODELS = re.compile(
    r"^(?:\d{1,2}|(?:19|20)\d{2}|(?:19|20)\d{2}-(?:19|20)\d{2}|\d+(?:\.\d+)?(?:MM|IN|QT|LB|V|W)|"
    r"\d{2}[-/]\d{2,4}|[A-Z]{1,2}\d?)$"
)
_SERIAL_LIKE = re.compile(r"X{3,}|^VF\d+$|^[A-Z]{1,3}\d{7,}$|^\d{9,}[A-Z]")


def _when_to_dates(text: str) -> tuple[date, date] | None:
    text = text.replace(" of ", " ")
    parts = text.split()
    if len(parts) == 2 and parts[0].lower() in _MONTHS:
        month, year = _MONTHS[parts[0].lower()], int(parts[1])
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if len(parts) == 1 and parts[0].isdigit():
        year = int(parts[0])
        return date(year, 1, 1), date(year, 12, 31)
    return None


def _cents(amount: str) -> int:
    return round(float(amount.replace(",", "")) * 100)


class _Builder:
    def __init__(self, text: str) -> None:
        self.text = text
        self.bounds = section_bounds(text)

    def section(self, name: str) -> tuple[str, int]:
        start, end = self.bounds.get(name, (0, 0))
        return self.text[start:end], start

    def span(self, field: str, quote: str, offset: int = 0) -> Span | None:
        found = find_span(self.text, quote, offset)
        return Span(field=field, quote=quote, start=found[0], end=found[1]) if found else None


def _models(b: _Builder, ev: list[Span]) -> list[str]:
    desc, offset = b.section("DESCRIPTION")
    products, p_offset = b.section("PRODUCTS")
    models: list[str] = []
    for body, base in ((desc, offset), (products, p_offset)):
        for intro in _MODEL_INTRO.finditer(body):
            window = body[intro.end() : intro.end() + 1500]
            last, found = 0, 0
            for token in _CODE_RE.finditer(window):
                gap = window[last : token.start()]
                last = token.end()
                stop = r"\b(?:serial|date code|manufactured|sold|located|printed|units)\b"
                if found and re.search(stop, gap, re.I):
                    break
                stripped = re.sub(r"[\s,()&#/:]|\band\b|\bor\b|\bthe\b", "", gap)
                if len(stripped) > 60:
                    break
                value = token.group(0).rstrip(".")
                if _NOT_MODELS.match(value) or _SERIAL_LIKE.search(value) or value in models:
                    continue
                models.append(value)
                found += 1
                span = b.span("models", value, base + intro.end())
                if span:
                    ev.append(span)
    return models


def _serials(b: _Builder, ev: list[Span]) -> list[SerialRange]:
    desc, offset = b.section("DESCRIPTION")
    out: list[SerialRange] = []
    for m in _SERIAL_PREFIX.finditer(desc):
        raw = m.group(0).rstrip(" ,")
        for t in _PREFIX_TOKEN.finditer(m.group("tokens")):
            prefix = t.group(1) or t.group(2)
            if prefix and prefix not in {"A", "AN", "AND", "OR"}:
                out.append(SerialRange(prefix=prefix, raw=raw))
        span = b.span("serial_ranges", raw, offset)
        if span:
            ev.append(span)
    if "serial" in desc.lower():
        for pattern in (_SERIAL_RANGE, _BETWEEN_CODES, _MASKED_PAIR):
            for m in pattern.finditer(desc):
                start, end = m.group(1), m.group(2)
                if start.isdigit() and len(start) <= 4:
                    continue
                if any(s.start == start and s.end == end for s in out):
                    continue
                out.append(SerialRange(start=start, end=end, raw=m.group(0)))
                span = b.span("serial_ranges", m.group(0), offset)
                if span:
                    ev.append(span)
    return out


def _date_codes(b: _Builder, ev: list[Span]) -> list[DateCodeRule]:
    desc, offset = b.section("DESCRIPTION")
    out: list[DateCodeRule] = []
    for m in _DATE_CODE.finditer(desc):
        out.append(DateCodeRule(format="date code", start=m.group(1), end=m.group(2), raw=m.group(0)))
    for m in _MANUFACTURED.finditer(desc):
        out.append(
            DateCodeRule(format="Month YYYY", start=m.group(1), end=m.group(2) or m.group(1), raw=m.group(0))
        )
    for m in _MODEL_YEARS.finditer(desc):
        out.append(
            DateCodeRule(format="model year", start=m.group(1), end=m.group(2) or m.group(1), raw=m.group(0))
        )
    for rule in out:
        span = b.span("date_codes", rule.raw, offset)
        if span:
            ev.append(span)
    return out


def _sold_at(b: _Builder, ev: list[Span]) -> list[str]:
    body, offset = b.section("SOLD AT")
    head = re.split(rf"\b(?:from|between|since) (?:{_MONTH_RE}|\d{{4}})", body, maxsplit=1)[0]
    head = re.sub(
        r"^(?:Sold )?(?:Online )?(?:exclusively )?(?:online )?(?:at|on)?:?\s*", "", head, flags=re.I
    )
    pieces = re.split(r",|;|\band\b|\bonline at\b|\bonline\b|\bat\b|\.\s", head)
    sellers: list[str] = []
    for piece in pieces:
        if re.search(r"\barea\b", piece):
            continue
        name = re.split(r"\s+in\s+|\s*\(", piece)[0].strip(" .:\n")
        name = re.sub(r"\s+(?:stores?|locations?)(?: nationwide)?$", "", name, flags=re.I).strip()
        name = re.sub(r"\s+nationwide$", "", name, flags=re.I).strip()
        if not name or _GENERIC_SELLERS.match(name) or name.lower() in {"exclusively", "sold"}:
            continue
        if not (name[0].isupper() or _DOMAIN.search(name)):
            continue
        if name.lower().endswith(" area") or re.match(r"^[A-Z][a-z]+ing\b", name):
            continue
        if len(name.split()) > 6 or name in sellers:
            continue
        sellers.append(name)
        span = b.span("sold_at", name, offset)
        if span:
            ev.append(span)
    return sellers


def _sold_window(b: _Builder, ev: list[Span]) -> tuple[date | None, date | None]:
    body, offset = b.section("SOLD AT")
    m = _SOLD_WINDOW.search(body)
    if m:
        first, last = _when_to_dates(m.group(1)), _when_to_dates(m.group(2))
        span = b.span("sold_from", m.group(0), offset)
        if span:
            ev.append(span)
        return (first[0] if first else None), (last[1] if last else None)
    m = _SOLD_FROM_ONLY.search(body)
    if m and (first := _when_to_dates(m.group(1))):
        span = b.span("sold_from", m.group(0), offset)
        if span:
            ev.append(span)
        return first[0], None
    return None, None


def _price(b: _Builder, ev: list[Span]) -> PriceRange | None:
    body, offset = b.section("SOLD AT")
    m = _PRICE_RANGE.search(body)
    if m:
        price = PriceRange(min_cents=_cents(m.group(1)), max_cents=_cents(m.group(2)))
    elif m := _PRICE_ONE.search(body):
        cents = _cents(m.group(1))
        price = PriceRange(min_cents=cents, max_cents=cents)
    else:
        return None
    span = b.span("price_range_cents", m.group(0), offset)
    if span:
        ev.append(span)
    return price


def _units(b: _Builder, ev: list[Span]) -> int | None:
    body, offset = b.section("PRODUCTS")
    m = re.search(r"units: (.+)", body)
    if not m:
        return None
    parsed = parse_units_text(m.group(1))
    if parsed.value is not None:
        quote = re.match(r"(?:About |Approximately )?[\d,. ]+(?: million)?", m.group(1))
        span = b.span("units_affected", quote.group(0).strip() if quote else m.group(1), offset)
        if span:
            ev.append(span)
    return parsed.value


def _remedy(b: _Builder, ev: list[Span]) -> tuple[RemedyType | None, list[RemedyOption], str | None]:
    body, offset = b.section("REMEDY")
    m = re.search(r"Remedy options: (.+)", body)
    kinds: list[RemedyKind] = []
    if m:
        mapping: dict[str, RemedyKind] = {"refund": "refund", "replace": "replacement", "repair": "repair"}
        for option in re.split(r",\s*", m.group(1)):
            kind = mapping.get(option.strip().lower())
            if kind and kind not in kinds:
                kinds.append(kind)
        span = b.span("remedy_type", m.group(0), offset)
        if span:
            ev.append(span)
    remedy_type: RemedyType | None = None
    if len(kinds) == 1:
        remedy_type = kinds[0]
    elif len(kinds) > 1:
        remedy_type = "multiple"
    destruction = _DESTRUCTION.search(body)
    instruction = destruction.group(0).strip() if destruction else None
    lowered = body.lower()
    proof: list[ProofKind] = []
    if instruction and "photo" in lowered:
        proof.append("destruction_photo")
    if "receipt" in lowered or "proof of purchase" in lowered:
        proof.append("receipt")
    if re.search(r"\breturn\b", lowered):
        proof.append("product_return")
    if "register" in lowered or "registration" in lowered or "online form" in lowered:
        proof.append("registration")
    options = [RemedyOption(type=k, proof_required=proof or ["none"]) for k in kinds]
    if instruction:
        span = b.span("destruction_instruction", instruction, offset)
        if span:
            ev.append(span)
    return remedy_type, options, instruction


def _registration_url(b: _Builder, ev: list[Span]) -> str | None:
    remedy, r_offset = b.section("REMEDY")
    for m in _URL.finditer(remedy):
        context = (remedy[max(0, m.start() - 80) : m.start()] + " " + remedy[m.end() : m.end() + 40]).lower()
        if re.search(r"regist|form|claim|participate|file", context):
            span = b.span("registration_url", m.group(0), r_offset)
            if span:
                ev.append(span)
            return m.group(0)
    contact, c_offset = b.section("CONTACT")
    for m in _URL.finditer(contact):
        after = contact[m.end() : m.end() + 60].lower()
        if re.search(r"claim|regist|enter your information", after):
            span = b.span("registration_url", m.group(0), c_offset)
            if span:
                ev.append(span)
            return m.group(0)
    return None


def _contact(b: _Builder, ev: list[Span]) -> str | None:
    body, offset = b.section("CONTACT")
    m = _PHONE.search(body) or _EMAIL.search(body)
    if not m:
        return None
    span = b.span("contact", m.group(0), offset)
    if span:
        ev.append(span)
    return m.group(0)


def _label_location(b: _Builder, ev: list[Span]) -> str | None:
    desc, offset = b.section("DESCRIPTION")
    m = _LABEL_LOCATION.search(desc)
    if not m:
        return None
    value = m.group(1).strip()
    span = b.span("model_label_location", value, offset)
    if span:
        ev.append(span)
    return value


def extract(source_text: str) -> ExtractedRules:
    b = _Builder(source_text)
    id_ev: list[Span] = []
    rem_ev: list[Span] = []
    sold_from, sold_to = _sold_window(b, id_ev)
    identification = IdentificationRules(
        models=_models(b, id_ev),
        model_label_location=_label_location(b, id_ev),
        serial_ranges=_serials(b, id_ev),
        date_codes=_date_codes(b, id_ev),
        sold_at=_sold_at(b, id_ev),
        sold_from=sold_from,
        sold_to=sold_to,
        price_range_cents=_price(b, id_ev),
        units_affected=_units(b, id_ev),
        evidence=id_ev,
    )
    remedy_type, options, instruction = _remedy(b, rem_ev)
    remedy = RemedyRules(
        remedy_type=remedy_type,
        options=options,
        destruction_instruction=instruction,
        registration_url=_registration_url(b, rem_ev),
        contact=_contact(b, rem_ev),
        evidence=rem_ev,
    )
    return ExtractedRules(identification=identification, remedy=remedy)
