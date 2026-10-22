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
                    "title": "Service Unavailable",
                    "status": 503,
                    "detail": "Stripe test prices are not configured.",
                    "instance": "/v1/billing/checkout",
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
    access_id: UUID | None = Field(
        None, description="with manual: the operator-approved typed-notice request the program comes from"
    )

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"recall_id": RECALL_ID, "value_threshold_cents": 10000}]}
    )


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: Any) -> Any:
    """Claimant text: control characters (newlines included) become spaces, runs of spaces collapse, the ends are
    stripped, and a value with no letter or digit left becomes None. A serial of "-" or an address of " " is then no
    value at all, so it can never match every other claim's missing value (red team, decisions log 2026-10-18)."""
    if not isinstance(value, str):
        return value
    cleaned = " ".join(_CONTROL.sub(" ", value).split())
    return cleaned if any(ch.isalnum() for ch in cleaned) else None


AccessStatusValue = Literal["requested", "approved", "rejected", "revoked"]


class TypedNotice(Schema):
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=200, description="the recalling company, as the notice names it")
    notice: str = Field(min_length=1, max_length=20000)

    _clean = field_validator("title", "company", mode="before")(clean_text)


class AccessRequestCreate(Schema):
    recall_id: UUID | None = Field(None, description="a recall from GET /v1/recalls; or give typed")
    typed: TypedNotice | None = Field(None, description="a recall not in the CPSC archive yet")


class AccessRequest(Schema):
    id: UUID
    recall_id: UUID | None
    title: str = Field(description="the recall's title, or the typed notice's")
    typed: bool
    typed_company: str | None = None
    typed_notice: str | None = Field(None, description="the typed notice's text, as this company sent it")
    program_id: UUID | None = Field(None, description="the product made from this request (opened on approval)")
    program_status: Literal["draft", "open", "closed"] | None = Field(
        None, description="that product's status; draft means it still needs something (a plan, a refund amount)"
    )
    status: AccessStatusValue
    note: str | None = Field(None, description="the operator's note on the decision")
    created_at: datetime
    decided_at: datetime | None


class AccessStatus(Schema):
    required: bool = Field(description="false only in tests (ACCESS_APPROVAL=off)")
    approved: bool = Field(description="the company holds at least one approved recall: the console is open")
    operator: bool = Field(description="always false for a company (the operator has none; read GET /v1/me)")
    requests: list[AccessRequest]


class ProductOutcome(Schema):
    id: UUID | None
    status: Literal["draft", "open", "closed"] | None
    error: Literal["over_plan", "missing_refund_amount", "conflict", "not_approved", "failed"] | None = Field(
        description="why the product is not open: over the company's plan, no refund amount in its rules, or other"
    )


class OperatorAccessRequest(Schema):
    id: UUID
    firm_id: UUID
    firm_name: str
    recall_id: UUID | None
    recall_number: str | None
    title: str
    typed: bool
    typed_company: str | None
    typed_notice: str | None = Field(None, description="the typed notice's text, first 4,000 characters")
    status: AccessStatusValue
    requested_by: str
    requester_domain: str | None = Field(None, description="the requester's work-email domain, from Clerk (a hint)")
    notice_company: str | None = Field(None, description="the company the notice names")
    notice_sites: list[str] = Field(default_factory=list, description="websites the notice names")
    domain_matches: bool = Field(description="a hint: the email domain matches the notice's company or a website")
    approved_elsewhere: str | None = Field(None, description="another company already approved for the recall")
    requester_email: str | None = Field(None, description="the requester's email, from Clerk, when they asked")
    requester_email_verified: bool | None = Field(None, description="whether Clerk had verified that email")
    requester_test_address: bool = Field(False, description="a +clerk_test address: proves nothing about a company")
    firm_plan: str | None = Field(None, description="the company's plan")
    firm_active_products: int = Field(0, description="the company's products that are not closed")
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    created_at: datetime
    product: ProductOutcome | None = Field(None, description="after approve or revoke: what happened to the product")


class AccessDecision(Schema):
    decision: Literal["approved", "rejected", "revoked"]
    note: str | None = Field(None, max_length=500)


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
    intake_url: str | None = Field(None, description="the product's one claim link, while it takes new claims")
    takes_new_claims: bool = Field(False, description="open, approved, and within the plan: the claim link works")
    needs_refund_amount: bool = Field(False, description="refunds with no price in the rules: set one to open")

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
    hold_reason: str | None = Field(
        None,
        description="why the claim waits for a person: flagged, over_value_threshold, model_disagrees_with_rules, "
        "capped, failed, payout_held_needs_review, shared_mailing_address (CHANGES item 6), claimant_reply, "
        "repeated_question (CHANGES item 53), agent_unavailable (CHANGES item 57)",
    )
    reopened_from: str | None = Field(None, description="the status a later duplicate reopened this claim from")
    duplicate_found_later: list[str] = Field(default_factory=list, description="claim ids that reopened this claim")
    duplicates_ack: list[str] = Field(default_factory=list, description="duplicates a reviewer saw when deciding")
    question_for_claimant: str | None = Field(None, description="the needs_info question sent to the claimant")


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
    kind: Literal["same_photo", "same_address", "same_payout", "same_serial", "same_mailing"]
    member_claim_ids: list[UUID]
    score: float


class PayoutView(Schema):
    id: UUID
    amount_cents: int
    currency: str
    method: Literal["stripe_connect", "check_export", "ach_export"]
    status: PayoutStatus
    stripe_transfer_id: str | None = None
    stripe_reversed_cents: int = 0


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


class PortalMessage(Schema):
    sender: Literal["firm", "claimant"]
    text: str
    at: datetime


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
    messages: list[PortalMessage] = Field(
        default_factory=list, description="questions to the claimant and their replies, oldest first"
    )
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


class BillingInvoiceSummary(Schema):
    id: str
    status: str
    attempt_count: int
    amount_due_cents: int
    amount_paid_cents: int
    currency: str


class Usage(Schema):
    period_start: date
    claims_adjudicated: int
    model_cost_usd: str
    open_programs: int
    active_products: int = Field(0, description="products that count toward the plan (not closed, approved)")
    plan: str
    effective_plan: str
    subscription_status: str | None
    limits: dict[str, int | None]
    invoices: list[BillingInvoiceSummary]
    billing_sync_error: str | None


class PortalProgram(Schema):
    company_name: str | None = Field(None, description="the company running the recall")
    contact: str | None = Field(None, description="how to reach the company, from the notice")
    title: str
    models: list[str]
    model_label_location: str | None
    remedy_type: str | None
    destruction_instruction: str | None
    required_photos: list[Literal["label", "destruction", "receipt"]]


class PortalAnswerField(Schema):
    name: Literal["model_claimed", "serial_claimed", "date_code_claimed", "retailer_claimed", "purchase_date"]
    value: str | None = Field(description="the claim's current value; purchase_date as YYYY-MM-DD")


class PortalView(Schema):
    connect_onboarding_url: str | None = None
    claim_id: UUID
    status: ClaimStatus
    program: PortalProgram
    pending_question: str | None
    decision_reasons: list[str] = Field(description="plain-language reasons once decided")
    photos_uploaded: list[Literal["label", "destruction", "receipt", "other"]] = Field(default_factory=list)
    payout_status: PayoutStatus | None = None
    payout_choice_due: date | None = Field(
        None,
        description="the day a bank-transfer refund still waiting on the claimant becomes a mailed check to the "
        "claimant's address; null when nothing is waiting",
    )
    messages: list[PortalMessage] = Field(default_factory=list, description="needs_info questions and replies")
    answer_fields: list[PortalAnswerField] = Field(
        default_factory=list,
        description="while needs_info: the claim fields the reply form shows, with their current values, from the "
        "checks the latest run could not pass",
    )
    answer_photos: list[Literal["label", "destruction", "receipt"]] = Field(
        default_factory=list, description="while needs_info: the photos the reply form asks for"
    )


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
    message: str = Field(
        "",
        max_length=2000,
        description="the claimant's note; text alone is read by a person and never sent to the agent",
    )
    model_claimed: str | None = Field(None, max_length=80)
    serial_claimed: str | None = Field(None, max_length=80)
    date_code_claimed: str | None = Field(None, max_length=40)
    retailer_claimed: str | None = Field(None, max_length=120)
    purchase_date: date | None = None

    _clean = field_validator("model_claimed", "serial_claimed", "date_code_claimed", "retailer_claimed", mode="before")(
        clean_text
    )


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
    graph: Literal["ok", "disabled", "unavailable"] = Field(
        "disabled", description="the Neo4j fraud graph; a projection, so it never makes the service unready"
    )


class ExportBatchSummary(Schema):
    id: UUID
    created_at: datetime
    created_by: str
    row_count: int
    total_cents: int
    sha256: str = Field(description="sha256 of the CSV bytes, for the bank upload record")


class ExportBatchList(Schema):
    items: list[ExportBatchSummary]


Channel = Literal["email", "mail", "phone_text"]


class RegistrationUpload(Schema):
    accepted: int
    rejected: list[dict[str, Any]] = Field(description="row number (1-based, header is row 1) and reason; first 50")
    rejected_total: int


class CampaignCounts(Schema):
    counts: dict[str, int]


class CampaignDraftRequest(Schema):
    channels: list[Channel] = Field(min_length=1, max_length=3)


class CampaignSendRequest(Schema):
    channel: Channel | None = Field(None, description="send one channel's drafts; all channels when omitted")


class CampaignSummary(Schema):
    registrations: int
    matched: int
    unmatched: int
    claim_link: str | None = Field(None, description="the product's claim link every notice carries")
    notifications: dict[str, int] = Field(description="channel:status -> count")
    sample: list[dict[str, Any]]
    simulated: bool = Field(True, description="sending is simulated: no message leaves the system")


# The signed-in account and onboarding (plan v3: one account is one company)


class MeFirm(Schema):
    id: UUID
    name: str
    onboarded: bool = Field(description="the company named itself and chose a plan")
    plan: str
    effective_plan: str = Field(description="free until a Growth Checkout is paid")
    subscription_status: str | None
    approved: bool = Field(description="the operator approved at least one recall for the company")
    active_products: int
    product_limit: int | None = Field(description="products the plan allows; none means no limit")


class Me(Schema):
    operator: bool = Field(description="the platform operator: approves companies, has no company")
    email: str | None
    firm: MeFirm | None


class OnboardingRequest(Schema):
    name: str = Field(min_length=1, max_length=200)
    plan: Literal["free", "growth"]


class OnboardingResult(Schema):
    firm: MeFirm
    checkout_url: str | None = Field(description="Stripe Checkout for Growth (test mode); none for Free")


class FirmPatch(Schema):
    name: str = Field(min_length=1, max_length=200)


class PlanInfo(Schema):
    id: Literal["free", "growth"]
    product_limit: int | None
    program_price_cents: int | None
    included_claims: int | None
    claim_price_cents: int | None
    currency: str
    refund_options: list[Literal["stripe_connect", "check_export"]]


# One claim link per product (invariant 10 amended)


class IntakeView(Schema):
    company_name: str
    title: str
    recall_number: str | None
    models: list[str]
    model_label_location: str | None
    sold_at: list[str]
    remedy_type: str | None
    destruction_instruction: str | None
    required_photos: list[Literal["label", "destruction", "receipt"]]
    contact: str | None


class IntakeStarted(Schema):
    claim_id: UUID
    claim_token: str = Field(description="the claimant's private claim link token: keep it, it is the only way back")
    claim_url: str
