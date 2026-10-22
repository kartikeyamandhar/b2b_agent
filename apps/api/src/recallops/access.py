"""Company verification (migration 0008; decisions log 2026-10-22): a firm runs a recall only after the platform
operator approves it, and one firm at most holds an approved request per recall.

Firms read and create their own requests through the tenant session. The operator's cross-firm list and every
decision go through two SECURITY DEFINER functions (recallops_access_requests, recallops_decide_access); the API calls
them only after checking that the caller's user id is in PLATFORM_OPERATOR_USER_IDS. The requester's work-email domain
and the company and websites named in the notice are hints for the operator, never a decision by themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from recallops.logs import get_logger, log_event
from recallops.models import Recall, RecallAccess, RecallProgram
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("access")

APPROVED = "approved"
PENDING_DETAIL = (
    "This organization is not approved to run a recall yet. Request access to your recall, and the platform "
    "operator will review it."
)


def approved_program_sql(program: str) -> str:
    """program_approved in SQL, for jobs that select across firms (worker, payout jobs); `program` is the alias of
    the claim's recall_programs row."""
    return (
        f"EXISTS (SELECT 1 FROM recall_access a WHERE a.firm_id = {program}.firm_id AND a.status = 'approved' "
        f"AND ((a.recall_id = {program}.recall_id) OR ({program}.recall_id IS NULL AND a.id = {program}.access_id)))"
    )


CLERK_USERS_URL = "https://api.clerk.com/v1/users/"
NOT_COMPANY_SITES = {"cpsc.gov", "saferproducts.gov", "www.cpsc.gov", "www.saferproducts.gov"}
_SITE = re.compile(r"\b(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+(?:com|net|org|co|us|io|biz|info|store|shop))\b")


class AccessDenied(PermissionError):
    """The firm is not approved for this recall (or for any recall)."""


class AccessConflict(ValueError):
    """An open request already exists, the move is not allowed, or another firm holds the recall."""


def required(settings: Settings) -> bool:
    return settings.access_approval == "required"


def firm_approved(session: Session, ctx: FirmContext, settings: Settings) -> bool:
    """At least one approved request: what lets a firm into the console at all. Run in the firm's tenant session."""
    if not required(settings):
        return True
    found = session.scalar(
        select(RecallAccess.id).where(RecallAccess.firm_id == ctx.firm_id, RecallAccess.status == APPROVED).limit(1)
    )
    return found is not None


def program_approved(session: Session, program: RecallProgram, settings: Settings) -> bool:
    """The firm holds an approved request for this program's recall (a CPSC recall, matched by recall id so a
    re-approval after a revocation counts) or for its typed notice (matched by the request it was created from)."""
    if not required(settings):
        return True
    q = select(RecallAccess.id).where(RecallAccess.firm_id == program.firm_id, RecallAccess.status == APPROVED)
    if program.recall_id is not None:
        q = q.where(RecallAccess.recall_id == program.recall_id)
    elif program.access_id is not None:
        q = q.where(RecallAccess.id == program.access_id)
    else:
        return False
    return session.scalar(q.limit(1)) is not None


def require_program(session: Session, program: RecallProgram, settings: Settings) -> None:
    if not program_approved(session, program, settings):
        raise AccessDenied("this organization is not approved to run this recall")


def is_operator(settings: Settings, user_id: str | None) -> bool:
    return bool(user_id) and user_id in settings.operator_ids()


def requester_domain(settings: Settings, user_id: str) -> str | None:
    """The domain of the requester's primary email, from Clerk; None when unavailable (a hint only, never required)."""
    key = settings.clerk_secret_key.get_secret_value()
    if settings.auth_provider != "clerk" or not key or not user_id.startswith("user_"):
        return None
    try:
        r = httpx.get(CLERK_USERS_URL + user_id, headers={"Authorization": f"Bearer {key}"}, timeout=3.0)
        r.raise_for_status()
        user = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log_event(log, "access_domain_lookup_failed", level=30, error=type(exc).__name__)
        return None
    primary = user.get("primary_email_address_id")
    for e in user.get("email_addresses") or []:
        address = str(e.get("email_address", ""))
        if e.get("id") == primary and "@" in address:
            return address.rsplit("@", 1)[1].lower()[:253]
    return None


@dataclass(frozen=True)
class Typed:
    title: str
    company: str
    notice: str


def request_access(
    session: Session,
    ctx: FirmContext,
    settings: Settings,
    recall_id: UUID | None,
    typed: Typed | None,
    domain: str | None,
) -> UUID:
    """A firm asks to run a recall (or a typed notice). Inserts only the columns the app role may write, so the new
    row is always 'requested'. A second open request for the same recall is refused."""
    if (recall_id is None) == (typed is None):
        raise ValueError("give exactly one of recall_id or typed")
    if recall_id is not None and session.get(Recall, recall_id) is None:
        raise LookupError("recall not found")
    values: dict[str, Any] = {
        "firm_id": ctx.firm_id,
        "recall_id": recall_id,
        "requested_by": ctx.user_id,
        "requester_domain": domain,
    }
    if typed is not None:
        values |= {"typed_title": typed.title, "typed_company": typed.company, "typed_notice": typed.notice}
    try:
        with session.begin_nested():
            access_id = session.execute(insert(RecallAccess).values(**values).returning(RecallAccess.id)).scalar_one()
    except IntegrityError as exc:
        raise AccessConflict("this organization already has an open request for this recall") from exc
    log_event(log, "access_requested", firm_id=str(ctx.firm_id), access_id=str(access_id), typed=typed is not None)
    return access_id


def own_requests(session: Session, ctx: FirmContext) -> list[RecallAccess]:
    return list(
        session.scalars(
            select(RecallAccess).where(RecallAccess.firm_id == ctx.firm_id).order_by(RecallAccess.created_at.desc())
        )
    )


def notice_hints(recall: Recall | None) -> tuple[str | None, list[str]]:
    """The recalling company (from the CPSC title, "<Company> Recalls ...") and the websites the notice names."""
    if recall is None:
        return None, []
    company = recall.title.split(" Recalls ", 1)[0].strip() if " Recalls " in recall.title else None
    sites = sorted({m.lower() for m in _SITE.findall((recall.source_text or "").lower())} - NOT_COMPANY_SITES)
    return company, sites[:10]


def domain_matches(domain: str | None, company: str | None, sites: list[str]) -> bool:
    """A hint: the requester's email domain is one the notice names, or its name is a word of the company's name."""
    if not domain:
        return False
    base = ".".join(domain.split(".")[-2:])
    if any(".".join(s.split(".")[-2:]) == base for s in sites):
        return True
    label = base.split(".")[0]
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    return len(label) >= 2 and label in words


def operator_list(session: Session) -> list[dict[str, Any]]:
    """Every firm's requests with the operator's hints (session: a plain app-role session)."""
    rows = [dict(r) for r in session.execute(text("SELECT * FROM recallops_access_requests()")).mappings()]
    recall_ids = {r["recall_id"] for r in rows if r["recall_id"]}
    recalls = {r.id: r for r in session.scalars(select(Recall).where(Recall.id.in_(recall_ids)))} if recall_ids else {}
    out = []
    for r in rows:
        recall = recalls.get(r["recall_id"])
        company, sites = notice_hints(recall)
        if recall is None:
            company = r["typed_company"]
        out.append(
            r
            | {
                "recall_title": recall.title if recall else r["typed_title"],
                "recall_number": recall.source_id if recall else None,
                "notice_company": company,
                "notice_sites": sites,
                "domain_matches": domain_matches(r["requester_domain"], company, sites),
            }
        )
    return out


def decide(session: Session, operator_id: str, access_id: UUID, status: str, note: str | None) -> UUID:
    """Approve, reject, or revoke; returns the request's firm id. A second approval for one recall is refused by the
    database (unique index), and so is any move other than requested to approved or rejected, approved to revoked."""
    try:
        with session.begin_nested():
            firm_id = session.execute(
                text("SELECT recallops_decide_access(:i, :s, :b, :n)"),
                {"i": access_id, "s": status, "b": operator_id, "n": note or ""},
            ).scalar_one()
    except DBAPIError as exc:  # psycopg files the function's check_violation under IntegrityError too: go by code
        code = getattr(exc.orig, "sqlstate", None)
        if code == "23505":  # unique_violation: recall_access_one_owner
            raise AccessConflict("another organization is already approved for this recall") from exc
        if code == "23514":  # check_violation, raised by the function for a move it does not allow
            raise AccessConflict("that decision is not allowed for this request's current status") from exc
        if code == "P0002":  # no_data_found
            raise LookupError("access request not found") from exc
        raise
    log_event(log, "access_decided", firm_id=str(firm_id), access_id=str(access_id), status=status)
    return UUID(str(firm_id))
