"""Tool implementations. Comparison rules, stated once:

- Models: case, spaces, hyphens, and dots are ignored; a rule ending in * matches as a prefix; equality only
  after folding OCR confusables (O/0, I/1/L, S/5, B/8, Z/2) is "ambiguous", never "match".
- Serial ranges: strings are uppercased with spaces and hyphens removed and compared lexicographically. Masked
  positions (X) in a bound are wildcards: comparison stops at the first masked position. A start greater
  than its end means the range wraps (year-digit serials such as 607 through 206). When start and end have
  the same length, a claimed serial of a different length is out of range.
- Date codes: parsed to (year, month) when the format allows (MM/YY, MM/YYYY, MM-YY, MMYY, Month YYYY, model
  year); otherwise compared as strings. "prior to" bounds are exclusive.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from recallops.fraud.phash import hamming
from recallops.rules.schemas import ExtractedRules, IdentificationRules, RemedyRules

IdentityVerdict = Literal[
    "match", "mismatch", "ambiguous", "not_provided", "skipped", "in_range", "out_of_range", "unparsed"
]
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"})


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PhotoRef(_M):
    kind: Literal["label", "destruction", "receipt", "other"]
    sha256: str
    phash: str


class ClaimFacts(_M):
    claim_id: UUID
    model_claimed: str | None = None
    serial_claimed: str | None = None
    date_code_claimed: str | None = None
    retailer_claimed: str | None = None
    purchase_date: date | None = None
    refund_cents: int | None = None
    email: str | None = None
    address: str | None = None
    payout_destination: str | None = None
    photos: tuple[PhotoRef, ...] = ()

    def photo(self, kind: str) -> PhotoRef | None:
        return next((p for p in self.photos if p.kind == kind), None)


class IdentityCheck(_M):
    verdict: IdentityVerdict
    cited_rule: str | None = None


class PurchaseCheck(_M):
    retailer: Literal["listed", "not_listed", "unknown", "skipped"]
    window: Literal["inside", "before", "after", "unknown", "skipped"]
    receipt: Literal["present", "missing", "not_required"]
    cited_rule: str | None = None


class DestructionCheck(_M):
    verdict: Literal["present", "missing", "not_required"]
    reason: str


class DuplicateHit(_M):
    kind: Literal["same_photo", "same_address", "same_payout", "same_serial"]
    other_claim_id: UUID
    detail: str


class DuplicatesCheck(_M):
    hits: tuple[DuplicateHit, ...] = Field(default_factory=tuple)


def norm_code(value: str | None) -> str:
    return re.sub(r"[\s\-.]", "", (value or "").upper())


def check_model(claim: ClaimFacts, rules: IdentificationRules) -> IdentityCheck:
    if not rules.models:
        return IdentityCheck(verdict="skipped")
    claimed = norm_code(claim.model_claimed)
    if not claimed:
        return IdentityCheck(verdict="not_provided")
    for rule in rules.models:
        target = norm_code(rule)
        if target.endswith("*") and claimed.startswith(target.rstrip("*")):
            return IdentityCheck(verdict="match", cited_rule=rule)
        if claimed == target:
            return IdentityCheck(verdict="match", cited_rule=rule)
    folded = claimed.translate(_CONFUSABLES)
    for rule in rules.models:
        if folded == norm_code(rule).rstrip("*").translate(_CONFUSABLES):
            return IdentityCheck(verdict="ambiguous", cited_rule=rule)
    return IdentityCheck(verdict="mismatch", cited_rule=", ".join(rules.models[:5]))


def _masked(bound: str) -> int:
    idx = bound.find("X")
    return idx if idx >= 0 else len(bound)


def serial_in_range(serial: str, start: str, end: str) -> bool:
    s, lo, hi = norm_code(serial), norm_code(start), norm_code(end)
    k = min(_masked(lo), _masked(hi))
    if k < max(len(lo), len(hi)):
        s, lo, hi = s[:k], lo[:k], hi[:k]
    elif len(lo) == len(hi) and len(s) != len(lo):
        return False
    if lo <= hi:
        return lo <= s <= hi
    return s >= lo or s <= hi


def check_serial(claim: ClaimFacts, rules: IdentificationRules) -> IdentityCheck:
    if not rules.serial_ranges:
        return IdentityCheck(verdict="skipped")
    if not norm_code(claim.serial_claimed):
        return IdentityCheck(verdict="not_provided")
    serial = claim.serial_claimed or ""
    for rule in rules.serial_ranges:
        if rule.prefix and norm_code(serial).startswith(norm_code(rule.prefix)):
            return IdentityCheck(verdict="in_range", cited_rule=rule.raw)
        if rule.start and rule.end and serial_in_range(serial, rule.start, rule.end):
            return IdentityCheck(verdict="in_range", cited_rule=rule.raw)
    return IdentityCheck(verdict="out_of_range", cited_rule=rules.serial_ranges[0].raw)


def date_key(value: str | None, fmt: str = "") -> tuple[int, int] | None:
    text = (value or "").strip().strip('".')
    if not text:
        return None
    lowered = fmt.lower()
    m = re.fullmatch(r"([A-Za-z]+) (\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return int(m.group(2)), _MONTHS[m.group(1).lower()]
    if re.fullmatch(r"(19|20)\d{2}", text) and ("year" in lowered or (len(text) == 4 and "yy" not in lowered)):
        return int(text), 0
    m = re.fullmatch(r"(\d{1,2})[/-](\d{2}|\d{4})", text)
    if m:
        year = int(m.group(2))
        return (year if year > 99 else 2000 + year), int(m.group(1))
    if re.fullmatch(r"\d{4}", text) and "mmyy" in lowered:
        return 2000 + int(text[2:]), int(text[:2])
    return None


def check_date_code(claim: ClaimFacts, rules: IdentificationRules) -> IdentityCheck:
    if not rules.date_codes:
        return IdentityCheck(verdict="skipped")
    claimed = (claim.date_code_claimed or "").strip()
    if not claimed:
        return IdentityCheck(verdict="not_provided")
    parsed_any = False
    for rule in rules.date_codes:
        key = date_key(claimed, rule.format)
        lo, hi = date_key(rule.start, rule.format), date_key(rule.end, rule.format)
        exclusive_end = "prior to" in rule.raw.lower()
        if key is not None and (lo is not None or hi is not None):
            parsed_any = True
            if lo is not None and key < lo:
                continue
            if hi is not None and (key > hi or (exclusive_end and key >= hi)):
                continue
            return IdentityCheck(verdict="in_range", cited_rule=rule.raw)
        if rule.start and rule.end and len(claimed) == len(rule.start) == len(rule.end):
            parsed_any = True
            if rule.start <= claimed <= rule.end:
                return IdentityCheck(verdict="in_range", cited_rule=rule.raw)
    if not parsed_any:
        return IdentityCheck(verdict="unparsed", cited_rule=rules.date_codes[0].raw)
    return IdentityCheck(verdict="out_of_range", cited_rule=rules.date_codes[0].raw)


def _seller_key(value: str) -> str:
    text = re.sub(r"^(https?://)?(www\.)?", "", value.strip().lower()).rstrip("/")
    return re.sub(r"\.(com|co|net|org|us)$", "", text).replace("'", "")


def receipt_required(remedy: RemedyRules) -> bool:
    return any("receipt" in o.proof_required for o in remedy.options)


def check_purchase(claim: ClaimFacts, rules: ExtractedRules) -> PurchaseCheck:
    ident = rules.identification
    retailer: Literal["listed", "not_listed", "unknown", "skipped"]
    if not ident.sold_at:
        retailer = "skipped"
    elif not claim.retailer_claimed:
        retailer = "unknown"
    else:
        claimed = _seller_key(claim.retailer_claimed)
        keys = [_seller_key(s) for s in ident.sold_at]
        retailer = "listed" if any(claimed == k or claimed in k or k in claimed for k in keys if k) else "not_listed"
    window: Literal["inside", "before", "after", "unknown", "skipped"]
    if ident.sold_from is None and ident.sold_to is None:
        window = "skipped"
    elif claim.purchase_date is None:
        window = "unknown"
    elif ident.sold_from and claim.purchase_date < ident.sold_from:
        window = "before"
    elif ident.sold_to and claim.purchase_date > ident.sold_to:
        window = "after"
    else:
        window = "inside"
    receipt: Literal["present", "missing", "not_required"] = "not_required"
    if receipt_required(rules.remedy):
        receipt = "present" if claim.photo("receipt") else "missing"
    cited = f"sold_at={ident.sold_at[:4]}, window={ident.sold_from}..{ident.sold_to}"
    return PurchaseCheck(retailer=retailer, window=window, receipt=receipt, cited_rule=cited)


def destruction_required(remedy: RemedyRules) -> bool:
    return bool(remedy.destruction_instruction) or any("destruction_photo" in o.proof_required for o in remedy.options)


def check_destruction_presence(claim: ClaimFacts, remedy: RemedyRules, phash_bits: int = 6) -> DestructionCheck:
    """M1 has no vision: a destruction photo counts as present when it exists and is not a copy of the
    label photo. Whether it actually shows the instructed destruction is the M2 vision tool's job."""
    if not destruction_required(remedy):
        return DestructionCheck(verdict="not_required", reason="remedy has no destruction step")
    photo = claim.photo("destruction")
    if photo is None:
        return DestructionCheck(verdict="missing", reason="no destruction photo")
    label = claim.photo("label")
    if label and (label.sha256 == photo.sha256 or hamming(label.phash, photo.phash) <= phash_bits):
        return DestructionCheck(verdict="missing", reason="destruction photo duplicates the label photo")
    return DestructionCheck(verdict="present", reason="destruction photo present (content not checked in M1)")


def _norm_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


# pHash ignores small changes by design, so template documents (receipts) from different purchases collide.
# Receipts therefore match on exact bytes only (decisions log, 2026-10-16, after the first adjudication run).
PHASH_KINDS = frozenset({"label", "destruction", "other"})
TOOLS_VERSION = "1.0.1"


def check_duplicates(
    claim: ClaimFacts, others: list[ClaimFacts], phash_bits: int = 6, phash_kinds: frozenset[str] = PHASH_KINDS
) -> DuplicatesCheck:
    hits: list[DuplicateHit] = []
    for other in others:
        if other.claim_id == claim.claim_id:
            continue
        for mine in claim.photos:
            for theirs in other.photos:
                if mine.kind != theirs.kind:
                    continue
                if mine.sha256 == theirs.sha256:
                    hits.append(
                        DuplicateHit(kind="same_photo", other_claim_id=other.claim_id, detail=f"{mine.kind} sha256")
                    )
                elif mine.kind in phash_kinds and (d := hamming(mine.phash, theirs.phash)) <= phash_bits:
                    hits.append(
                        DuplicateHit(kind="same_photo", other_claim_id=other.claim_id, detail=f"{mine.kind} phash {d}")
                    )
        if claim.address and _norm_text(claim.address) == _norm_text(other.address):
            hits.append(DuplicateHit(kind="same_address", other_claim_id=other.claim_id, detail="address"))
        elif claim.email and _norm_text(claim.email) == _norm_text(other.email):
            hits.append(DuplicateHit(kind="same_address", other_claim_id=other.claim_id, detail="email"))
        if claim.serial_claimed and norm_code(claim.serial_claimed) == norm_code(other.serial_claimed):
            hits.append(DuplicateHit(kind="same_serial", other_claim_id=other.claim_id, detail="serial"))
        if claim.payout_destination and claim.payout_destination == other.payout_destination:
            hits.append(DuplicateHit(kind="same_payout", other_claim_id=other.claim_id, detail="payout destination"))
    return DuplicatesCheck(hits=tuple(hits))
