"""Payment persistence declarations mirroring migration 0006_payments."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Numeric
from sqlalchemy.orm import Mapped, mapped_column

from recallops.models import Base, _now


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    firm_id: Mapped[UUID] = mapped_column(primary_key=True)
    subscription_id: Mapped[str | None]
    subscription_status: Mapped[str | None]
    paid_access: Mapped[bool] = mapped_column(default=False)
    program_item_id: Mapped[str | None]
    checkout_attempt: Mapped[UUID | None]
    checkout_started_at: Mapped[datetime | None]
    checkout_session_id: Mapped[str | None]
    checkout_expires_at: Mapped[datetime | None]
    checkout_request: Mapped[dict[str, Any] | None]
    customer_started_at: Mapped[datetime | None]
    quantity_version: Mapped[int] = mapped_column(default=0)
    quantity_synced_version: Mapped[int] = mapped_column(default=-1)
    quantity_request: Mapped[dict[str, Any] | None]
    last_sync_error: Mapped[str | None]


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    id: Mapped[str] = mapped_column(primary_key=True)
    firm_id: Mapped[UUID]
    subscription_id: Mapped[str]
    status: Mapped[str]
    attempt_count: Mapped[int]
    amount_due_cents: Mapped[int]
    amount_paid_cents: Mapped[int]
    currency: Mapped[str]
    created_at: Mapped[datetime]
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]


class ClaimUsage(Base):
    __tablename__ = "claim_usage"
    firm_id: Mapped[UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(primary_key=True)
    run_id: Mapped[UUID]
    counted_at: Mapped[datetime]
    growth_billable: Mapped[bool]


class RunUsageCost(Base):
    __tablename__ = "run_usage_costs"
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    firm_id: Mapped[UUID]
    period_start: Mapped[date]
    recorded_cost: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal(0))


class UsageReport(Base):
    __tablename__ = "usage_reports"
    firm_id: Mapped[UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    request: Mapped[dict[str, Any]]
    started_at: Mapped[datetime] = _now()
    reported_at: Mapped[datetime | None]
    last_error: Mapped[str | None]
