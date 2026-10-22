"""The signed-in company (owner decisions 2026-10-22): one account is one company. It names itself and picks a plan
at sign-up (onboarding) before it asks the operator for a recall, and the website reads everything it needs to route
the account from one summary (GET /v1/me)."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from recallops import access
from recallops.billing.policy import active_products, effective_plan
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Firm
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("company")

NAME_MAX = 200


class AlreadyOnboarded(ValueError):
    """The company already named itself and picked a plan; renames go through PATCH /v1/firm."""


class RenameLocked(PermissionError):
    """The operator approved the company under its name, which claimants then read on every claim page: after that, a
    new name goes through the operator (plan v3 review: an approved company could rename itself to anyone)."""


def clean_name(raw: str) -> str:
    """A company name as the operator will read it: no control, format, or bidirectional characters (a name could
    otherwise reorder itself on screen to imitate another company), whitespace collapsed, 1 to 200 characters."""
    kept = "".join(ch for ch in raw if unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs"))
    name = re.sub(r"\s+", " ", kept).strip()
    if not name:
        raise ValueError("the company name is empty")
    if len(name) > NAME_MAX:
        raise ValueError(f"the company name is longer than {NAME_MAX} characters")
    return name


def summary(session: Session, ctx: FirmContext, settings: Settings) -> dict[str, Any]:
    firm = session.get(Firm, ctx.firm_id)
    if firm is None:
        raise LookupError("company not found")
    plan = effective_plan(session, firm)
    from recallops.billing.models import BillingAccount

    account = session.get(BillingAccount, ctx.firm_id)
    return {
        "id": firm.id,
        "name": firm.name,
        "onboarded": firm.onboarded_at is not None,
        "plan": firm.plan,
        "effective_plan": plan,
        "subscription_status": account.subscription_status if account else None,
        "approved": access.firm_approved(session, ctx, settings),
        "active_products": active_products(session, ctx.firm_id),
        "product_limit": settings.free_active_products if plan == "free" else None,
    }


def _audit(session: Session, ctx: FirmContext, action: str, before: Any, after: Any) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="firm",
            target_id=str(ctx.firm_id),
            before=before,
            after=after,
        )
    )


def onboard(session: Session, ctx: FirmContext, name: str, plan: str) -> None:
    """Name the company and record the plan it chose. Growth starts only when Stripe confirms the Checkout (the
    webhook sets firms.plan); a Checkout that is cancelled leaves the company on Free, onboarded."""
    firm = session.get(Firm, ctx.firm_id, with_for_update=True)
    if firm is None:
        raise LookupError("company not found")
    if firm.onboarded_at is not None:
        raise AlreadyOnboarded("the company is already set up")
    before = {"name": firm.name}
    firm.name = clean_name(name)
    firm.onboarded_at = datetime.now(UTC)
    session.flush()
    _audit(session, ctx, "company_onboarded", before, {"name": firm.name, "plan_chosen": plan})
    log_event(log, "company_onboarded", firm_id=str(ctx.firm_id), plan=plan)


def rename(session: Session, ctx: FirmContext, settings: Settings, name: str) -> None:
    firm = session.get(Firm, ctx.firm_id, with_for_update=True)
    if firm is None:
        raise LookupError("company not found")
    if access.required(settings) and access.firm_approved(session, ctx, settings):
        raise RenameLocked("the operator approved your company under its current name; ask them to change it")
    before = {"name": firm.name}
    firm.name = clean_name(name)
    session.flush()
    _audit(session, ctx, "company_renamed", before, {"name": firm.name})


def plans(settings: Settings) -> list[dict[str, Any]]:
    """What each plan allows, from settings (prices are never hardcoded): the sign-up page reads this."""
    both = ["stripe_connect", "check_export"]
    return [
        {
            "id": "free",
            "product_limit": settings.free_active_products,
            "program_price_cents": None,
            "included_claims": None,
            "claim_price_cents": None,
            "currency": "usd",
            "refund_options": both,
        },
        {
            "id": "growth",
            "product_limit": None,
            "program_price_cents": settings.growth_program_price_cents,
            "included_claims": settings.growth_included_claims,
            "claim_price_cents": settings.growth_claim_price_cents,
            "currency": "usd",
            "refund_options": both,
        },
    ]
