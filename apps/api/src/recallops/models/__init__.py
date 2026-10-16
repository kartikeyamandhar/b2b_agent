"""SQLAlchemy models mirroring migrations/versions/*.py (the migrations are the source of truth)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        dict[str, Any]: JSONB,
        list[str]: ARRAY(Text),
        list[UUID]: ARRAY(PG_UUID(as_uuid=True)),
        UUID: PG_UUID(as_uuid=True),
        str: Text,
        Decimal: Numeric(10, 6),
        datetime: DateTime(timezone=True),
        date: Date,
        bool: Boolean,
        int: Integer,
    }


def _pk() -> Mapped[UUID]:
    return mapped_column(primary_key=True, server_default=func.gen_random_uuid())


def _now() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


# Public reference tables (no firm_id; CLAUDE.md invariant 2).


class Recall(Base):
    __tablename__ = "recalls"
    id: Mapped[UUID] = _pk()
    source: Mapped[str]
    source_id: Mapped[str]
    source_record_id: Mapped[str | None]
    recall_date: Mapped[date]
    last_publish_date: Mapped[date | None]
    title: Mapped[str]
    description: Mapped[str]
    url: Mapped[str]
    raw: Mapped[dict[str, Any]]
    raw_sha256: Mapped[str]
    source_text: Mapped[str]
    source_text_sha256: Mapped[str]
    canonicalizer_version: Mapped[str]
    units_affected: Mapped[int | None]
    units_status: Mapped[str]
    units_text: Mapped[str]
    category: Mapped[str | None]
    retailers: Mapped[list[str]]
    manufacturers: Mapped[list[str]]
    importers: Mapped[list[str]]
    remedies: Mapped[list[str]]
    upcs: Mapped[list[str]]
    extracted_rules: Mapped[dict[str, Any] | None]
    extracted_at: Mapped[datetime | None]
    extractor: Mapped[str | None]
    extractor_version: Mapped[str | None]
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class FeedRun(Base):
    __tablename__ = "feed_runs"
    id: Mapped[UUID] = _pk()
    source: Mapped[str]
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(server_default="running")
    fetched: Mapped[int] = mapped_column(server_default="0")
    inserted: Mapped[int] = mapped_column(server_default="0")
    updated: Mapped[int] = mapped_column(server_default="0")
    unchanged: Mapped[int] = mapped_column(server_default="0")
    failed: Mapped[int] = mapped_column(server_default="0")
    error: Mapped[str | None]


class StripeEvent(Base):
    __tablename__ = "stripe_events"
    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str]
    received_at: Mapped[datetime] = _now()
    processed_at: Mapped[datetime | None]
    payload: Mapped[dict[str, Any]]


# Tenant tables (firm_id, RLS forced).


class Firm(Base):
    __tablename__ = "firms"
    id: Mapped[UUID] = _pk()
    name: Mapped[str]
    clerk_org_id: Mapped[str | None]
    stripe_customer_id: Mapped[str | None]
    plan: Mapped[str] = mapped_column(server_default="free")
    created_at: Mapped[datetime] = _now()


class FirmMember(Base):
    __tablename__ = "firm_members"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID] = mapped_column(ForeignKey("firms.id"))
    clerk_user_id: Mapped[str]
    role: Mapped[str]
    created_at: Mapped[datetime] = _now()


class RecallProgram(Base):
    __tablename__ = "recall_programs"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID] = mapped_column(ForeignKey("firms.id"))
    recall_id: Mapped[UUID | None] = mapped_column(ForeignKey("recalls.id"))
    status: Mapped[str] = mapped_column(server_default="draft")
    identification_rules: Mapped[dict[str, Any]]
    remedy_rules: Mapped[dict[str, Any]]
    value_threshold_cents: Mapped[int]
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    program_id: Mapped[UUID]
    kind: Mapped[str]
    title: Mapped[str]
    text: Mapped[str]
    created_at: Mapped[datetime] = _now()


class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    program_id: Mapped[UUID]
    token_hash: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="draft")
    claimant: Mapped[dict[str, Any]]
    model_claimed: Mapped[str | None]
    serial_claimed: Mapped[str | None]
    date_code_claimed: Mapped[str | None]
    retailer_claimed: Mapped[str | None]
    purchase_date: Mapped[date | None]
    quantity: Mapped[int] = mapped_column(server_default="1")
    decision: Mapped[dict[str, Any] | None]
    remedy: Mapped[str | None]
    refund_cents: Mapped[int | None]
    created_at: Mapped[datetime] = _now()
    submitted_at: Mapped[datetime | None]
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[str | None]


class ClaimPhoto(Base):
    __tablename__ = "claim_photos"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    claim_id: Mapped[UUID]
    kind: Mapped[str]
    storage_path: Mapped[str]
    sha256: Mapped[str]
    phash: Mapped[str]
    exif: Mapped[dict[str, Any]]
    uploaded_at: Mapped[datetime] = _now()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID | None]
    claim_id: Mapped[UUID | None]
    program_id: Mapped[UUID | None]
    recall_id: Mapped[UUID | None]
    graph: Mapped[str]
    thread_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="running")
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None]
    steps: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    model_id: Mapped[str | None]
    outcome: Mapped[dict[str, Any] | None]


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[UUID] = _pk()
    run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id"))
    firm_id: Mapped[UUID | None]
    idx: Mapped[int]
    node: Mapped[str]
    thought: Mapped[str | None]
    tool: Mapped[str | None]
    args: Mapped[dict[str, Any] | None]
    result: Mapped[dict[str, Any] | None]
    latency_ms: Mapped[int] = mapped_column(server_default="0")
    tokens_in: Mapped[int] = mapped_column(server_default="0")
    tokens_out: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = _now()


class FraudCluster(Base):
    __tablename__ = "fraud_clusters"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    program_id: Mapped[UUID]
    kind: Mapped[str]
    member_claim_ids: Mapped[list[UUID]]
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4), server_default="1")
    created_at: Mapped[datetime] = _now()


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    claim_id: Mapped[UUID]
    amount_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(server_default="usd")
    method: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="pending")
    stripe_transfer_id: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(server_default="0")
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class MprReport(Base):
    __tablename__ = "mpr_reports"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    program_id: Mapped[UUID]
    period_start: Mapped[date]
    period_end: Mapped[date]
    payload: Mapped[dict[str, Any]]
    reconciled: Mapped[bool] = mapped_column(server_default="false")
    generated_at: Mapped[datetime] = _now()
    generated_by: Mapped[str]


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    actor: Mapped[str]
    action: Mapped[str]
    target_type: Mapped[str]
    target_id: Mapped[str]
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    at: Mapped[datetime] = _now()


class UsageMeter(Base):
    __tablename__ = "usage_meters"
    id: Mapped[UUID] = _pk()
    firm_id: Mapped[UUID]
    period_start: Mapped[date]
    claims_adjudicated: Mapped[int] = mapped_column(server_default="0")
    model_cost_usd: Mapped[Decimal] = mapped_column(server_default="0")


TENANT_MODELS: tuple[type[Base], ...] = (
    FirmMember,
    RecallProgram,
    Document,
    Claim,
    ClaimPhoto,
    AgentRun,
    AgentStep,
    FraudCluster,
    Payout,
    MprReport,
    AuditLog,
    UsageMeter,
)
