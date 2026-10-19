"""API request and response schemas: the contract in docs/contracts/openapi.json. Breaking a field here needs a
decisions-log entry and a message to the team (CLAUDE.md, Working in a team)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from recallops.rules.schemas import IdentificationRules, RemedyRules

ClaimStatus = Literal[
    "draft", "submitted", "needs_info", "under_review", "approved", "denied", "flagged", "capped", "paid", "closed"
]
Outcome = Literal["approve", "deny", "needs_info", "flag"]
RunStatus = Literal["running", "done", "capped", "failed", "waiting_human"]
PayoutStatus = Literal[
    "pending", "pending_export", "onboarding", "transferring", "transferred", "failed", "exported", "held"
]

PROGRAM_ID = "6c1f2a8e-1b7d-4d1e-9a6f-2f6f5e0a9b11"
CLAIM_ID = "0b7f0d8e-3c2a-4b5e-8f1a-7d6c5b4a3e21"
RECALL_ID = "2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d"
RUN_ID = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Problem(Schema):
    """RFC 7807 problem detail. The UI shows `detail` plus `correlation_id`."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    correlation_id: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "about:blank",
                    "title": "Not Implemented",
                    "status": 501,
                    "detail": "POST /v1/programs arrives in M2 (backend owner); the contract is final.",
                    "instance": "/v1/programs",
                    "correlation_id": "c0ffee00-0000-4000-8000-000000000001",
                }
            ]
        }
    )


class RecallSummary(Schema):
    id: UUID
    source: str
    source_id: str = Field(description="CPSC recall number, digits only")
    recall_date: date
    title: str
    url: str
    units_affected: int | None
    units_status: Literal["ok", "ambiguous", "unparsed"]
    category: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": RECALL_ID,
                    "source": "cpsc",
                    "source_id": "27024",
                    "recall_date": "2026-10-15",
                    "title": "3M Company Recalls Scotch™ Thermal Laminators Due to Risk of Serious Injury or Death "
                    "from Fire Hazard",
                    "url": "https://cpsc.gov/Recalls/2027/3M-Company-Recalls-ScotchTM-Thermal-Laminators",
                    "units_affected": 53634,
                    "units_status": "ok",
                    "category": "Business and Office Machines",
                }
            ]
        }
    )


class RecallPage(Schema):
    items: list[RecallSummary]
    total: int


class ManualRecall(Schema):
    title: str
    description: str
    recall_date: date | None = None


class ProgramCreate(Schema):
    recall_id: UUID | None = Field(None, description="A recall from GET /v1/recalls; or give manual")
    manual: ManualRecall | None = None
    value_threshold_cents: int | None = Field(None, ge=0)

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"recall_id": RECALL_ID, "value_threshold_cents": 10000}]}
    )


class ProgramPatch(Schema):
    status: Literal["draft", "open", "closed"] | None = None
    identification_rules: IdentificationRules | None = None
    remedy_rules: RemedyRules | None = None
    value_threshold_cents: int | None = Field(None, ge=0)

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "open", "value_threshold_cents": 15000}]})


class Program(Schema):
    id: UUID
    recall_id: UUID | None
    title: str
    status: Literal["draft", "open", "closed"]
    units_affected: int | None
    identification_rules: IdentificationRules
    remedy_rules: RemedyRules
    value_threshold_cents: int
    claim_counts: dict[str, int] = Field(description="claims by status")
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": PROGRAM_ID,
                    "recall_id": RECALL_ID,
                    "title": "Scotch Thermal Laminators",
                    "status": "open",
                    "units_affected": 53634,
                    "identification_rules": {
                        "models": ["TL1302Z"],
                        "model_label_location": "on the sticker on the bottom",
                        "serial_ranges": [],
                        "date_codes": [],
                        "sold_at": ["Amazon.com"],
                        "sold_from": "2024-06-01",
                        "sold_to": "2026-09-30",
                        "price_range_cents": {"min_cents": 4400, "max_cents": 5600},
                        "units_affected": 53634,
                        "evidence": [],
                    },
                    "remedy_rules": {
                        "remedy_type": "refund",
                        "options": [],
                        "destruction_instruction": None,
                        "registration_url": None,
                        "contact": "800-772-4337",
                        "evidence": [],
                    },
                    "value_threshold_cents": 10000,
                    "claim_counts": {"approved": 270, "denied": 101, "needs_info": 52, "flagged": 77},
                    "opened_at": "2026-10-16T22:40:00Z",
                    "closed_at": None,
                    "created_at": "2026-10-16T22:40:00Z",
                }
            ]
        }
    )


class ProgramList(Schema):
    items: list[Program]


class Decision(Schema):
    outcome: Outcome
    reasons: list[str]
    cited_rules: list[str]
    confidence: float | None = None
    routed_to_human: bool
    decided_by: str | None = Field(None, description="agent | rules_only | a user id")


class ClaimSummary(Schema):
    id: UUID
    program_id: UUID
    status: ClaimStatus
    model_claimed: str | None
    retailer_claimed: str | None
    purchase_date: date | None
    refund_cents: int | None
    outcome: Outcome | None
    routed_to_human: bool
    created_at: datetime
    decided_at: datetime | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": CLAIM_ID,
                    "program_id": PROGRAM_ID,
                    "status": "approved",
                    "model_claimed": "TL1302Z",
                    "retailer_claimed": "Amazon.com",
                    "purchase_date": "2025-03-14",
                    "refund_cents": 4999,
                    "outcome": "approve",
                    "routed_to_human": False,
                    "created_at": "2026-10-16T22:40:01Z",
                    "decided_at": "2026-10-16T22:40:05Z",
                }
            ]
        }
    )


class ClaimPage(Schema):
    items: list[ClaimSummary]
    next_cursor: str | None = Field(description="pass as ?cursor= for the next page; null on the last page")


class Photo(Schema):
    id: UUID
    kind: Literal["label", "destruction", "receipt", "other"]
    url: str = Field(description="short-lived URL; in dev a tenant-scoped API path")
    sha256: str
    phash: str
    uploaded_at: datetime


class RunSummary(Schema):
    run_id: UUID
    graph: Literal["extract_rules", "adjudicate", "rules_only", "rules_plus_vision"]
    status: RunStatus
    steps: int
    cost_usd: str = Field(description="decimal string, USD")
    started_at: datetime
    finished_at: datetime | None


class Cluster(Schema):
    id: UUID
    kind: Literal["same_photo", "same_address", "same_payout", "same_serial"]
    member_claim_ids: list[UUID]
    score: float


class PayoutView(Schema):
    id: UUID
    amount_cents: int
    currency: str
    method: Literal["stripe_connect", "check_export", "ach_export"]
    status: PayoutStatus


class AuditEntry(Schema):
    actor: str
    action: str
    at: datetime
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class Claimant(Schema):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    synthetic: bool = False


class ClaimDetail(Schema):
    id: UUID
    program_id: UUID
    status: ClaimStatus
    claimant: Claimant
    model_claimed: str | None
    serial_claimed: str | None
    date_code_claimed: str | None
    retailer_claimed: str | None
    purchase_date: date | None
    quantity: int
    refund_cents: int | None
    remedy: Literal["refund", "repair", "replacement"] | None
    photos: list[Photo]
    decision: Decision | None
    run: RunSummary | None
    clusters: list[Cluster]
    payout: PayoutView | None
    audit: list[AuditEntry]
    created_at: datetime
    submitted_at: datetime | None
    decided_at: datetime | None


class TraceStep(Schema):
    idx: int
    node: str
    thought: str | None
    tool: str | None
    args: dict[str, Any] | None
    result: dict[str, Any] | None
    latency_ms: int
    tokens_in: int
    tokens_out: int
    cost_usd: str
    created_at: datetime
    counted: bool = Field(False, description="counts toward caps.max_steps (a model call or a rejected tool call)")
    nominal_cost_usd: str = Field("0.000000", description="cost from reported usage; cost_usd is 0 when replayed")


class Used(Schema):
    """What the run has used, to show against caps in the trace viewer header (counted steps, nominal cost)."""

    steps: int
    cost_usd: str
    seconds: float


class Caps(Schema):
    max_steps: int
    max_usd: str
    max_seconds: float


class Trace(Schema):
    run: RunSummary
    caps: Caps
    used: Used | None = None
    steps: list[TraceStep]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "run": {
                        "run_id": RUN_ID,
                        "graph": "rules_only",
                        "status": "done",
                        "steps": 7,
                        "cost_usd": "0.000000",
                        "started_at": "2026-10-16T22:40:04Z",
                        "finished_at": "2026-10-16T22:40:05Z",
                    },
                    "caps": {"max_steps": 8, "max_usd": "0.02", "max_seconds": 60.0},
                    "steps": [
                        {
                            "idx": 0,
                            "node": "check_model",
                            "thought": None,
                            "tool": "check_model",
                            "args": {"model_claimed": "TL1302Z"},
                            "result": {"verdict": "match", "cited_rule": "TL1302Z"},
                            "latency_ms": 0,
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "cost_usd": "0.000000",
                            "created_at": "2026-10-16T22:40:04Z",
                        }
                    ],
                }
            ]
        }
    )


class ClaimAction(Schema):
    action: Literal["resume_override", "request_info", "mark_fraud", "mark_delivered"] = Field(
        description="mark_delivered records that an approved repair or replacement reached the consumer (an MPR "
        "correction); the claim closes"
    )
    outcome: Literal["approve", "deny", "needs_info"] | None = Field(None, description="required for resume_override")
    message: str | None = Field(None, description="question for the claimant (request_info) or note")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"action": "resume_override", "outcome": "approve", "message": "label photo checked by hand"}]
        }
    )


class IssueRequest(Schema):
    n: int = Field(ge=1, le=10_000)


class IssuedLink(Schema):
    claim_id: UUID
    url: str


class IssueResponse(Schema):
    links: list[IssuedLink]


class MprManual(Schema):
    """Boxes the system cannot know (01-backend.md section 8). Omitted boxes keep their documented defaults;
    contacts_email overrides the computed count and the report notes it."""

    units_with_manufacturer: int | None = Field(None, ge=0)
    units_with_distributor: int | None = Field(None, ge=0)
    units_with_retailers: int | None = Field(None, ge=0)
    incidents_before: int | None = Field(None, ge=0)
    incidents_after: int | None = Field(None, ge=0)
    injuries_before: int | None = Field(None, ge=0)
    injuries_after: int | None = Field(None, ge=0)
    deaths_before: int | None = Field(None, ge=0)
    deaths_after: int | None = Field(None, ge=0)
    notified_phone_text: int | None = Field(None, ge=0)
    notified_email: int | None = Field(None, ge=0)
    notified_mail: int | None = Field(None, ge=0)
    contacts_phone: int | None = Field(None, ge=0)
    contacts_email: int | None = Field(None, ge=0)
    contacts_mail: int | None = Field(None, ge=0)
    contacts_registration_card: int | None = Field(None, ge=0)
    homepage_posting: bool | None = None
    homepage_posting_explanation: str | None = Field(None, max_length=500)
    website_hits: int | None = Field(None, ge=0)
    social_posts: dict[str, Any] | None = None
    marketplace_monitoring: dict[str, Any] | None = None


class MprReport(Schema):
    id: UUID
    program_id: UUID
    period_start: date
    period_end: date
    payload: dict[str, Any] = Field(description="the CPSC MPR boxes (01-backend.md section 8)")
    reconciled: bool
    generated_at: datetime


class Usage(Schema):
    period_start: date
    claims_adjudicated: int
    model_cost_usd: str
    open_programs: int


class PortalProgram(Schema):
    title: str
    models: list[str]
    model_label_location: str | None
    remedy_type: str | None
    destruction_instruction: str | None
    required_photos: list[Literal["label", "destruction", "receipt"]]


class PortalMessage(Schema):
    sender: Literal["firm", "claimant"]
    text: str
    at: datetime


class PortalView(Schema):
    claim_id: UUID
    status: ClaimStatus
    program: PortalProgram
    pending_question: str | None
    decision_reasons: list[str] = Field(description="plain-language reasons once decided")
    photos_uploaded: list[Literal["label", "destruction", "receipt", "other"]] = Field(default_factory=list)
    payout_status: PayoutStatus | None = None
    messages: list[PortalMessage] = Field(default_factory=list, description="needs_info questions and replies")


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: Any) -> Any:
    """Claimant text: control characters (newlines included) become spaces, runs of spaces collapse, the ends are
    stripped, and a value with no letter or digit left becomes None. A serial of "-" or an address of " " is then no
    value at all, so it can never match every other claim's missing value (red team, decisions log 2026-10-18)."""
    if not isinstance(value, str):
        return value
    cleaned = " ".join(_CONTROL.sub(" ", value).split())
    return cleaned if any(ch.isalnum() for ch in cleaned) else None


class PortalSubmit(Schema):
    name: str = Field(max_length=120)
    email: str = Field(max_length=200)
    phone: str | None = Field(None, max_length=40)
    address_line1: str = Field(max_length=200)
    city: str = Field(max_length=100)
    state: str = Field(max_length=40)
    zip: str = Field(max_length=20)
    model_claimed: str | None = Field(None, max_length=80)
    serial_claimed: str | None = Field(None, max_length=80)
    date_code_claimed: str | None = Field(None, max_length=40)
    retailer_claimed: str | None = Field(None, max_length=120)
    purchase_date: date | None = None
    quantity: int = Field(1, ge=1, le=20)

    _clean = field_validator(
        "name",
        "email",
        "phone",
        "address_line1",
        "city",
        "state",
        "zip",
        "model_claimed",
        "serial_claimed",
        "date_code_claimed",
        "retailer_claimed",
        mode="before",
    )(clean_text)

    @field_validator("name", "email", "address_line1", "city", "state", "zip")
    @classmethod
    def _required(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("required")
        return value


class PortalAnswer(Schema):
    message: str = Field(max_length=2000)


class PortalPayout(Schema):
    method: Literal["stripe_connect", "check_export"]
    mailing_address: str | None = Field(None, max_length=300)

    _clean = field_validator("mailing_address", mode="before")(clean_text)


class CheckoutSession(Schema):
    url: str


class Health(Schema):
    status: Literal["ok", "degraded"]
    database: bool
    worker_heartbeat_age_s: float | None = Field(None, description="seconds since the worker's last heartbeat")


class ExportBatchSummary(Schema):
    id: UUID
    created_at: datetime
    created_by: str
    row_count: int
    total_cents: int
    sha256: str = Field(description="sha256 of the CSV bytes, for the bank upload record")


class ExportBatchList(Schema):
    items: list[ExportBatchSummary]
