"""Identification and remedy rule schemas (01-backend.md section 5). Shared by both extractors,
the golden set, the adjudication tools, and the API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProofKind = Literal["label_photo", "destruction_photo", "receipt", "product_return", "registration", "none"]
RemedyKind = Literal["refund", "repair", "replacement"]
RemedyType = Literal["refund", "repair", "replacement", "multiple"]
PayoutMethod = Literal["check", "original_payment", "store_credit"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Span(_Model):
    field: str
    quote: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SerialRange(_Model):
    prefix: str | None = None
    start: str | None = None
    end: str | None = None
    raw: str


class DateCodeRule(_Model):
    format: str
    start: str | None = None
    end: str | None = None
    raw: str


class PriceRange(_Model):
    min_cents: int = Field(ge=0)
    max_cents: int = Field(ge=0)


class IdentificationRules(_Model):
    models: list[str] = Field(default_factory=list)
    model_label_location: str | None = None
    serial_ranges: list[SerialRange] = Field(default_factory=list)
    date_codes: list[DateCodeRule] = Field(default_factory=list)
    sold_at: list[str] = Field(default_factory=list)
    sold_from: date | None = None
    sold_to: date | None = None
    price_range_cents: PriceRange | None = None
    units_affected: int | None = None
    evidence: list[Span] = Field(default_factory=list)


class Payout(_Model):
    method: PayoutMethod | None = None
    amount_rule: str | None = None


class RemedyOption(_Model):
    type: RemedyKind
    steps: list[str] = Field(default_factory=list)
    proof_required: list[ProofKind] = Field(default_factory=list)
    payout: Payout = Field(default_factory=Payout)


class RemedyRules(_Model):
    remedy_type: RemedyType | None = None
    options: list[RemedyOption] = Field(default_factory=list)
    destruction_instruction: str | None = None
    registration_url: str | None = None
    contact: str | None = None
    evidence: list[Span] = Field(default_factory=list)


class ExtractedRules(_Model):
    identification: IdentificationRules
    remedy: RemedyRules
