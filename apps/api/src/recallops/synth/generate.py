"""Synthetic claims with planted violations (01-backend.md section 7).

Deterministic: every claim draws from its own RNG streams seeded by sha256(seed, program, index, stream), so
output does not depend on process hash seeds or iteration order. Ground truth comes from construction (an
out-of-range serial is built outside every bound), not from the adjudication tools.
"""

from __future__ import annotations

import calendar
import hashlib
import io
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import yaml
from PIL import Image, ImageEnhance

from recallops.claims.tools.checks import destruction_required, receipt_required
from recallops.fraud.phash import hamming, phash_bytes
from recallops.rules.schemas import ExtractedRules, SerialRange
from recallops.synth import claimants
from recallops.synth.render import draw_style, instruction_family, render_destruction, render_label, render_receipt

NAMESPACE = uuid.UUID("6f1f6c1e-5d1a-4b8e-9a52-1c1d2e3f4a5b")
DUPLICATES = ("duplicate_photo", "duplicate_address", "duplicate_serial", "duplicate_payout")
SEPARATION_BITS = 10  # non-planted photos must be farther apart than this
FICTIONAL_RETAILER = "Corner Hardware Co"


@dataclass
class SynthPhoto:
    kind: str
    data: bytes
    sha256: str
    phash: str


@dataclass
class SynthClaim:
    claim_id: uuid.UUID
    program_key: str
    index: int
    model_claimed: str | None
    serial_claimed: str | None
    date_code_claimed: str | None
    retailer_claimed: str | None
    purchase_date: date | None
    refund_cents: int | None
    remedy: str | None
    claimant: dict[str, Any]
    photos: list[SynthPhoto]
    violations: list[str] = field(default_factory=list)
    subtypes: dict[str, str] = field(default_factory=dict)
    cluster: dict[str, str] | None = None
    tier: str = "clean"
    expected_outcome: str = "approve"


def stream(seed: int, program_key: str, index: int, name: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{program_key}:{index}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def load_yaml(path: Any) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


# Independent range oracle for construction only (same documented semantics as check_serial, written separately).
def _norm(v: str) -> str:
    return re.sub(r"[\s\-.]", "", v.upper())


def _inside(s: str, rule: SerialRange) -> bool:
    s = _norm(s)
    if rule.prefix:
        return s.startswith(_norm(rule.prefix))
    if not (rule.start and rule.end):
        return False
    lo, hi = _norm(rule.start), _norm(rule.end)
    cut = min([i for i, c in enumerate(lo) if c == "X"] + [i for i, c in enumerate(hi) if c == "X"] + [99])
    if cut != 99:
        s, lo, hi = s[:cut], lo[:cut], hi[:cut]
    elif len(lo) == len(hi) and len(s) != len(lo):
        return False
    return lo <= s <= hi if lo <= hi else (s >= lo or s <= hi)


def _fill_mask(value: str, rng: random.Random) -> str:
    return "".join(str(rng.randint(0, 9)) if c in "Xx" else c for c in value)


def serial_inside(rules: ExtractedRules, rng: random.Random) -> str | None:
    ranges = rules.identification.serial_ranges
    if not ranges:
        return None
    rule = rng.choice(ranges)
    if rule.prefix:
        return f"{rule.prefix}{rng.randint(10_000, 99_999_999)}"
    start, end = rule.start or "", rule.end or ""
    m1, m2 = re.fullmatch(r"([A-Z]*)(\d+)", start), re.fullmatch(r"([A-Z]*)(\d+)", end)
    if m1 and m2 and m1.group(1) == m2.group(1) and len(m1.group(2)) == len(m2.group(2)) and start <= end:
        n = rng.randint(int(m1.group(2)), int(m2.group(2)))
        return f"{m1.group(1)}{n:0{len(m1.group(2))}d}"
    return _fill_mask(start, rng)


def serial_outside(rules: ExtractedRules, rng: random.Random) -> str | None:
    ranges = rules.identification.serial_ranges
    if not ranges:
        return None
    template = next((r.start for r in ranges if r.start), None) or f"{ranges[0].prefix or 'A'}12345678"
    candidates = [f"QQ{rng.randint(10_000_000, 99_999_999)}"]
    m = re.fullmatch(r"([A-Z]*)(\d+)", template)
    if m:
        width = len(m.group(2))
        candidates += [
            f"{m.group(1)}{(int(r.end[len(m.group(1)) :]) + rng.randint(500, 5000)):0{width}d}"
            for r in ranges
            if r.end and re.fullmatch(rf"{m.group(1)}\d{{{width}}}", r.end)
        ]
    for lead in "45390":
        candidates.append(_fill_mask(lead + template[1:], rng))
    # Systematic search over the leading three characters, for notices whose ranges wrap and cover most values.
    if len(template) > 3:
        offset = rng.randint(0, 999)
        candidates += [_fill_mask(f"{(offset + k) % 1000:03d}" + template[3:], rng) for k in range(1000)]
    for candidate in candidates:
        if len(candidate) and not any(_inside(candidate, r) for r in ranges):
            return candidate
    return None


def _date_value(fmt: str, year: int, month: int, like: str) -> str:
    if re.fullmatch(r"\d{2}/\d{2}", like):
        return f"{month:02d}/{year % 100:02d}"
    if re.fullmatch(r"\d/\d{2}", like):
        return f"{month}/{year % 100:02d}"
    if re.fullmatch(r"\d{2}/\d{4}", like):
        return f"{month:02d}/{year}"
    if re.fullmatch(r"\d{2}-\d{2}", like):
        return f"{month:02d}-{year % 100:02d}"
    if re.fullmatch(r"\d{4}", like) and "mmyy" in fmt.lower():
        return f"{month:02d}{year % 100:02d}"
    if re.fullmatch(r"(19|20)\d{2}", like):
        return str(year)
    return f"{calendar.month_name[month]} {year}"


def _parse(value: str, fmt: str = "") -> tuple[int, int] | None:
    months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    m = re.fullmatch(r"([A-Za-z]+) (\d{4})", value)
    if m and m.group(1).lower() in months:
        return int(m.group(2)), months[m.group(1).lower()]
    m = re.fullmatch(r"(\d{1,2})[/-](\d{2}|\d{4})", value)
    if m:
        y = int(m.group(2))
        return (y if y > 99 else 2000 + y), int(m.group(1))
    if re.fullmatch(r"(19|20)\d{2}", value):
        return int(value), 1
    if re.fullmatch(r"\d{4}", value) and "mmyy" in fmt.lower() and 1 <= int(value[:2]) <= 12:
        return 2000 + int(value[2:]), int(value[:2])
    return None


def date_code_inside(rules: ExtractedRules, rng: random.Random) -> str | None:
    codes = rules.identification.date_codes
    if not codes:
        return None
    rule = rng.choice(codes)
    if rule.start:
        return rule.start
    end = rule.end or ""
    parsed = _parse(end, rule.format)
    if parsed and "prior to" in rule.raw.lower():
        year, month = parsed
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        return _date_value(rule.format, year, month, end)
    return end or None


def date_code_outside(rules: ExtractedRules, rng: random.Random) -> str | None:
    codes = rules.identification.date_codes
    if not codes:
        return None
    rule = codes[0]
    bound = rule.end or rule.start or ""
    parsed = _parse(bound, rule.format)
    if parsed:
        year, month = parsed
        ends = [p[0] for c in codes for p in [_parse(c.end or "", c.format)] if p]
        later = max(year + 1, max(ends, default=year) + 1)
        return _date_value(rule.format, later, month or 1, bound)
    if bound.isdigit():
        return str(int(max(c.end or "0" for c in codes)) + 50).zfill(len(bound))
    return None


def sibling_model(models: list[str], rng: random.Random) -> str:
    """One non-confusable character changed, never equal to a recalled model after normalization."""
    base = rng.choice(models).rstrip("*")
    taken = {_norm(m).rstrip("*") for m in models}
    swaps = {str(d): str((d + 3) % 10) for d in range(10)} | {
        "A": "C",
        "C": "D",
        "D": "F",
        "E": "G",
        "F": "H",
        "G": "K",
        "H": "J",
        "K": "M",
        "M": "N",
        "N": "P",
        "P": "R",
        "R": "T",
        "T": "U",
        "U": "V",
        "V": "W",
        "W": "Y",
        "X": "Q",
        "Y": "Q",
    }
    positions = [i for i, c in enumerate(base) if c.upper() in swaps]
    rng.shuffle(positions)
    for i in positions:
        candidate = base[:i] + swaps[base[i].upper()] + base[i + 1 :]
        if _norm(candidate) not in taken:
            return candidate
    return base + "Q7"


def _label_lines(c: SynthClaim, rng: random.Random) -> list[str]:
    lines = [f"MODEL {c.model_claimed or '-'}", f"SER {c.serial_claimed or '-'}"]
    if c.date_code_claimed:
        lines.append(f"DATE {c.date_code_claimed}")
    distractors = [
        "120V~ 60Hz 1200W",
        f"ITEM # {rng.randint(100000, 999999)}",
        f"CAT. NO. {rng.randint(1000, 9999)}",
        "MADE IN CHINA",
        f"LOT {rng.randint(10, 99)}{rng.choice('ABCDEFGH')}",
    ]
    rng.shuffle(distractors)
    return lines + distractors[: rng.randint(2, 4)]


def _photo(kind: str, data: bytes) -> SynthPhoto:
    return SynthPhoto(kind=kind, data=data, sha256=hashlib.sha256(data).hexdigest(), phash=phash_bytes(data))


def _near_duplicate(data: bytes, rng: random.Random) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        brighter = ImageEnhance.Brightness(image.convert("RGB")).enhance(1 + rng.uniform(0.02, 0.06))
        out = io.BytesIO()
        brighter.save(out, format="JPEG", quality=rng.randint(60, 85))
    return out.getvalue()


def _choose_violations(
    rng: random.Random, applicable: list[str], weights: dict[str, float], second: float
) -> list[str]:
    pool = [v for v in applicable if weights.get(v, 0) > 0]
    first = rng.choices(pool, weights=[weights[v] for v in pool])[0]
    picked = [first]
    if rng.random() < second:
        rest = [v for v in pool if v != first and v not in DUPLICATES and first not in DUPLICATES]
        if rest:
            picked.append(rng.choice(rest))
    return picked


def applicable_violations(rules: ExtractedRules) -> list[str]:
    i, r = rules.identification, rules.remedy
    out = ["wrong_model"] if i.models else []
    if i.serial_ranges:
        out.append("serial_out_of_range")
    if i.date_codes:
        out.append("date_code_out_of_range")
    if i.sold_at:
        out.append("retailer_not_listed")
    if i.sold_from:
        out.append("purchase_before_window")
    if receipt_required(r):
        out.append("no_receipt_when_required")
    if destruction_required(r):
        out.append("destruction_not_shown")
    out += ["duplicate_photo", "duplicate_address", "duplicate_payout"]
    out.append("duplicate_serial")
    return out


def generate_program(
    rules: ExtractedRules,
    program_key: str,
    n: int,
    seed: int,
    planted: dict[str, Any],
    policy: dict[str, Any],
    recall_date: date | None = None,
) -> list[SynthClaim]:
    ident, remedy = rules.identification, rules.remedy
    remedy_kind = (
        remedy.options[0].type
        if remedy.options
        else (remedy.remedy_type if remedy.remedy_type != "multiple" else "refund")
    )
    weights: dict[str, float] = planted["violations"]
    applicable = applicable_violations(rules)
    claims: list[SynthClaim] = []
    used_serials: set[str] = set()
    for i in range(n):
        rc, rv, ri = (stream(seed, program_key, i, s) for s in ("claimant", "violations", "image"))
        person = claimants.person(rc, program_key, i)
        models = [m for m in ident.models] or ["UNSPECIFIED"]
        picked = rc.choice(models)
        model = picked.rstrip("*") + ("A1" if picked.endswith("*") else "")
        serial = serial_inside(rules, rc) or f"SN{rc.randint(10_000_000, 99_999_999)}"
        while serial in used_serials:
            serial = serial_inside(rules, rc) or f"SN{rc.randint(10_000_000, 99_999_999)}"
        used_serials.add(serial)
        start = ident.sold_from or (recall_date - timedelta(days=720) if recall_date else date(2023, 1, 1))
        end = ident.sold_to or (recall_date or start + timedelta(days=365))
        purchased = start + timedelta(days=rc.randint(0, max(0, (end - start).days)))
        price = ident.price_range_cents
        refund = None
        if remedy_kind == "refund":
            refund = rc.randint(price.min_cents, price.max_cents) if price else rc.randint(1500, 6000)
        claim = SynthClaim(
            claim_id=uuid.uuid5(NAMESPACE, f"{seed}:{program_key}:{i}"),
            program_key=program_key,
            index=i,
            model_claimed=model,
            serial_claimed=serial,
            date_code_claimed=date_code_inside(rules, rc),
            retailer_claimed=rc.choice(ident.sold_at) if ident.sold_at else "Online marketplace",
            purchase_date=purchased,
            refund_cents=refund,
            remedy=remedy_kind,
            claimant=person,
            photos=[],
        )
        if rv.random() >= planted["clean_share"]:
            claim.violations = _choose_violations(rv, applicable, weights, planted["second_violation_share"])
        style = draw_style(ri)
        claim.tier = style.tier
        destruction_complies = True
        unplantable: list[str] = []
        for v in claim.violations:
            if v == "wrong_model":
                claim.model_claimed = sibling_model(ident.models, rv)
            elif v == "serial_out_of_range":
                outside = serial_outside(rules, rv)
                if outside:
                    claim.serial_claimed = outside
                else:
                    unplantable.append(v)
            elif v == "date_code_out_of_range":
                outside_code = date_code_outside(rules, rv)
                if outside_code:
                    claim.date_code_claimed = outside_code
                else:
                    unplantable.append(v)
            elif v == "retailer_not_listed":
                claim.retailer_claimed = FICTIONAL_RETAILER
            elif v == "purchase_before_window" and ident.sold_from:
                claim.purchase_date = ident.sold_from - timedelta(days=rv.randint(30, 400))
            elif v == "destruction_not_shown":
                claim.subtypes[v] = rv.choice(["missing", "label_reupload", "intact"])
                destruction_complies = False
        # A violation that cannot be built (no value outside every range) is not planted, never mislabeled.
        claim.violations = [v for v in claim.violations if v not in unplantable]
        label = render_label(ri, style, _label_lines(claim, ri))
        claim.photos.append(_photo("label", label))
        if destruction_required(remedy):
            sub = claim.subtypes.get("destruction_not_shown")
            if sub == "label_reupload":
                claim.photos.append(_photo("destruction", label))
            elif sub != "missing":
                family = instruction_family(remedy.destruction_instruction)
                claim.photos.append(_photo("destruction", render_destruction(ri, style, family, destruction_complies)))
        wants_receipt = receipt_required(remedy) or ri.random() < 0.3
        if wants_receipt and "no_receipt_when_required" not in claim.violations:
            receipt = render_receipt(ri, style, claim.retailer_claimed or "", str(claim.purchase_date), refund or 0)
            claim.photos.append(_photo("receipt", receipt))
        claims.append(claim)
    _plant_duplicates(claims, seed, program_key)
    _separate_photos(claims, seed, program_key)
    for claim in claims:
        claim.expected_outcome = expected_outcome(claim.violations, policy)
    return claims


def _plant_duplicates(claims: list[SynthClaim], seed: int, program_key: str) -> None:
    copies = [c for c in claims if any(v in DUPLICATES for v in c.violations)]
    copy_ids = {c.claim_id for c in copies}
    for copy in copies:
        rng = stream(seed, program_key, copy.index, "duplicate")
        sources = [c for c in claims if c.claim_id not in copy_ids and c.cluster is None]
        if not sources:
            copy.violations = [v for v in copy.violations if v not in DUPLICATES]
            continue
        source = rng.choice(sources)
        kind = next(v for v in copy.violations if v in DUPLICATES)
        if kind == "duplicate_photo":
            original = next(p for p in source.photos if p.kind == "label")
            data = original.data
            if rng.random() < 0.5:
                near = _near_duplicate(original.data, rng)
                if hamming(phash_bytes(near), original.phash) <= 6:
                    data = near
            copy.photos = [p for p in copy.photos if p.kind != "label"] + [_photo("label", data)]
            copy.subtypes[kind] = "exact" if data == original.data else "near"
        elif kind == "duplicate_address":
            for key in ("address_line1", "city", "state", "zip", "email"):
                copy.claimant[key] = source.claimant[key]
        elif kind == "duplicate_serial":
            copy.serial_claimed = source.serial_claimed
        elif kind == "duplicate_payout":
            copy.claimant["payout_destination"] = source.claimant["payout_destination"]
        cluster_id = str(uuid.uuid5(NAMESPACE, f"cluster:{copy.claim_id}"))
        copy.cluster = {"cluster_id": cluster_id, "kind": kind, "role": "copy"}
        source.cluster = {"cluster_id": cluster_id, "kind": kind, "role": "original"}
        source.violations = [*source.violations, kind]


def _separate_photos(claims: list[SynthClaim], seed: int, program_key: str) -> None:
    """Re-render any non-planted label photo that lands within SEPARATION_BITS of another claim's."""
    planted = {c.claim_id for c in claims if c.cluster and c.cluster["kind"] == "duplicate_photo"}
    for attempt in range(5):
        moved = False
        for i, a in enumerate(claims):
            la = next(p for p in a.photos if p.kind == "label")
            for b in claims[i + 1 :]:
                if a.claim_id in planted and b.claim_id in planted:
                    continue
                lb = next(p for p in b.photos if p.kind == "label")
                if hamming(la.phash, lb.phash) <= SEPARATION_BITS and b.claim_id not in planted:
                    rng = stream(seed, program_key, b.index, f"image-retry-{attempt}")
                    data = render_label(rng, draw_style(rng), _label_lines(b, rng))
                    b.photos = [p for p in b.photos if p.kind != "label"] + [_photo("label", data)]
                    moved = True
        if not moved:
            return


def expected_outcome(violations: list[str], policy: dict[str, Any]) -> str:
    if not violations:
        return str(policy["clean"])
    outcomes = {str(policy["violations"][v]) for v in violations}
    return next(str(o) for o in policy["precedence"] if o in outcomes)
