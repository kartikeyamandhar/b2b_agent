"""Company verification (migration 0008; decisions log 2026-10-22): a firm runs a recall only after the platform
operator approves it, and one firm at most holds an approved request per recall.

Firms read and create their own requests through the tenant session. The operator's cross-firm list and every
decision go through two SECURITY DEFINER functions (recallops_access_requests, recallops_decide_access); the API calls
them only after checking that the caller is the operator: a user id in PLATFORM_OPERATOR_USER_IDS, or an account with
a verified address in PLATFORM_OPERATOR_EMAILS. The operator has every access (owner decision 2026-10-22): the console
gate lets them through, and the organization they act in is approved the moment they ask, through the same request and
decision rows as any firm. The requester's work-email domain and the company and websites named in the notice are
hints for the operator, never a decision by themselves.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import httpx
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from recallops.logs import get_logger, log_event
from recallops.models import Recall, RecallAccess, RecallProgram
from recallops.settings import EXAMPLE_CLERK_SECRET_KEY, Settings, canonical_email
from recallops.tenancy import FirmContext

log = get_logger("access")

APPROVED = "approved"
PENDING_DETAIL = (
    "This company is not approved to run a recall yet. Request access to your recall, and the platform "
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


class AccessAlreadyOpen(AccessConflict):
    """This firm already has an open (requested or approved) request for the recall."""


class AccessHeldElsewhere(AccessConflict):
    """Another firm holds the approved request for the recall (the one-owner unique index)."""


class AccessStateConflict(AccessConflict):
    """The request's current status does not allow this decision (it was decided at the same moment, for example)."""


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
        raise AccessDenied("this company is not approved to run this recall")


@dataclass(frozen=True)
class ClerkEmails:
    primary: str | None
    verified: frozenset[str]  # canonical_email forms


class ClerkLookupFailed(Exception):
    """Clerk's answer was too slow, too large, or not a user record."""


CLERK_LOOKUP_TTL_S = 60.0
CLERK_FAILURE_TTL_S = 15.0  # a failed lookup is kept briefly, so a Clerk outage costs one wait per account per window
CLERK_DEADLINE_S = 3.0  # for the whole call: httpx's timeouts bound each phase, not the total
CLERK_MAX_BYTES = 256_000
CLERK_CACHE_MAX = 1000
CLERK_USER_ID = re.compile(r"user_[A-Za-z0-9]{1,64}")
_clerk_cache: dict[str, tuple[float, ClerkEmails | None]] = {}
_clerk_locks: dict[str, threading.Lock] = {}
_clerk_locks_guard = threading.Lock()
_clerk_http: httpx.Client | None = None


def _http() -> httpx.Client:
    global _clerk_http
    if _clerk_http is None:
        _clerk_http = httpx.Client(timeout=httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0))
    return _clerk_http


def _fetch_user(key: str, user_id: str) -> Any:
    deadline = time.monotonic() + CLERK_DEADLINE_S
    body = bytearray()
    with _http().stream("GET", CLERK_USERS_URL + user_id, headers={"Authorization": f"Bearer {key}"}) as r:
        r.raise_for_status()  # a redirect is refused here too: the client does not follow redirects
        for chunk in r.iter_bytes():
            body += chunk
            if len(body) > CLERK_MAX_BYTES or time.monotonic() > deadline:
                raise ClerkLookupFailed("the answer was too slow or too large")
    return json.loads(body)


def _emails(user: Any) -> ClerkEmails:
    """The primary and verified addresses of a Clerk user record; anything not shaped like one fails the lookup."""
    if not isinstance(user, dict) or not isinstance(user.get("email_addresses"), list):
        raise ClerkLookupFailed("not a user record")
    primary_id = user.get("primary_email_address_id")
    primary: str | None = None
    verified: set[str] = set()
    for e in user["email_addresses"]:
        address = e.get("email_address") if isinstance(e, dict) else None
        if not isinstance(address, str) or not address.isascii() or "@" not in address:
            continue  # only ASCII addresses count (a look-alike letter never lowercases into the operator's address)
        address = address.strip().lower()
        if isinstance(primary_id, str) and e.get("id") == primary_id:
            primary = address
        verification = e.get("verification")
        if isinstance(verification, dict) and verification.get("status") == "verified":
            verified.add(canonical_email(address))
    return ClerkEmails(primary, frozenset(verified))


def _lock(user_id: str) -> threading.Lock:
    with _clerk_locks_guard:
        if len(_clerk_locks) >= CLERK_CACHE_MAX:
            _clerk_locks.clear()
        return _clerk_locks.setdefault(user_id, threading.Lock())


def _cached(user_id: str) -> tuple[bool, ClerkEmails | None]:
    hit = _clerk_cache.get(user_id)
    if hit is not None and hit[0] > time.monotonic():
        return True, hit[1]
    return False, None


def clerk_emails(settings: Settings, user_id: str) -> ClerkEmails | None:
    """The account's primary and verified email addresses, from Clerk's Backend API; None when unavailable. A lookup
    is kept for a minute (a failure for 15 s), and one lookup at a time runs per account: the operator check runs on
    console requests, and a Clerk outage must not turn into one blocked call per request."""
    key = settings.clerk_secret_key.get_secret_value()
    if (
        settings.auth_provider != "clerk"
        or key in ("", EXAMPLE_CLERK_SECRET_KEY)
        or not CLERK_USER_ID.fullmatch(user_id)
    ):
        return None
    found, value = _cached(user_id)
    if found:
        return value
    with _lock(user_id):
        found, value = _cached(user_id)  # the lookup another request just finished
        if found:
            return value
        started = time.monotonic()
        try:
            value = _emails(_fetch_user(key, user_id))
            ttl = CLERK_LOOKUP_TTL_S
        except (httpx.HTTPError, ValueError, ClerkLookupFailed) as exc:  # ValueError covers a body that is not JSON
            log_event(
                log,
                "clerk_user_lookup_failed",
                level=30,
                error=type(exc).__name__,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            value, ttl = None, CLERK_FAILURE_TTL_S
        if len(_clerk_cache) >= CLERK_CACHE_MAX:
            _clerk_cache.clear()
        _clerk_cache[user_id] = (time.monotonic() + ttl, value)
        return value


def is_operator(settings: Settings, user_id: str | None) -> bool:
    """A user id in PLATFORM_OPERATOR_USER_IDS, or (Clerk) an account with a verified address in
    PLATFORM_OPERATOR_EMAILS, compared by canonical_email. Fails closed: when Clerk cannot be asked, the caller is not
    the operator."""
    if not user_id:
        return False
    if user_id in settings.operator_ids():
        return True
    emails = settings.operator_emails()
    if not emails:
        return False
    found = clerk_emails(settings, user_id)
    return found is not None and not found.verified.isdisjoint(emails)


OperatorStatus = Literal["operator", "company", "unknown"]


def operator_status(settings: Settings, user_id: str) -> OperatorStatus:
    """Whether a signed-in account is the platform operator, with a third answer when Clerk cannot be asked. Sign-in
    never creates a company on "unknown" (red team of plan v2: one failed lookup would have made the operator a
    company for good)."""
    if user_id in settings.operator_ids():
        return "operator"
    emails = settings.operator_emails()
    if not emails or settings.auth_provider != "clerk":
        return "company"
    found = clerk_emails(settings, user_id)
    if found is None:
        return "unknown"
    return "operator" if not found.verified.isdisjoint(emails) else "company"


def requester_domain(settings: Settings, user_id: str) -> str | None:
    """The domain of the requester's primary email, from Clerk; None when unavailable (a hint only, never required)."""
    found = clerk_emails(settings, user_id)
    if found is None or found.primary is None:
        return None
    return found.primary.rsplit("@", 1)[1][:253]


@dataclass(frozen=True)
class Requester:
    """Who asked, as Clerk knows them, kept on the request so the operator's window needs no Clerk call per row."""

    email: str
    verified: bool


def requester_of(settings: Settings, user_id: str) -> Requester | None:
    found = clerk_emails(settings, user_id)
    if found is None or found.primary is None:
        return None
    return Requester(found.primary, canonical_email(found.primary) in found.verified)


class NotOnboarded(ValueError):
    """The company has not named itself and chosen a plan yet."""


class OverPlan(PermissionError):
    """Free covers one product; asking for another needs Growth."""


def check_can_request(session: Session, ctx: FirmContext, settings: Settings) -> None:
    """Before a company asks for a recall (plan v3): it has finished onboarding, and on Free it has no product and no
    request waiting already (free covers one product, owner decision 2026-10-22)."""
    from recallops.billing.policy import FREE_PRODUCT_LIMIT, active_products, effective_plan
    from recallops.models import Firm

    firm = session.get(Firm, ctx.firm_id)
    if firm is None or firm.onboarded_at is None:
        raise NotOnboarded("finish setting up your company first: its name and plan")
    if effective_plan(session, firm) != "free":
        return
    waiting = session.scalar(
        select(func.count())
        .select_from(RecallAccess)
        .where(RecallAccess.firm_id == ctx.firm_id, RecallAccess.status == "requested")
    )
    if active_products(session, ctx.firm_id) + int(waiting or 0) >= settings.free_active_products:
        raise OverPlan(FREE_PRODUCT_LIMIT)


def is_test_address(email: str | None) -> bool:
    """Clerk's test mode verifies any +clerk_test address with a fixed code, so it proves nothing about a company."""
    return bool(email) and "+clerk_test" in str(email).lower()


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
    requester: Requester | None = None,
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
    if requester is not None:
        values |= {"requester_email": requester.email[:320], "requester_email_verified": requester.verified}
    if typed is not None:
        values |= {"typed_title": typed.title, "typed_company": typed.company, "typed_notice": typed.notice}
    try:
        with session.begin_nested():
            access_id = session.execute(insert(RecallAccess).values(**values).returning(RecallAccess.id)).scalar_one()
    except IntegrityError as exc:
        raise AccessAlreadyOpen("this company already has an open request for this recall") from exc
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
        trusted = r.get("requester_email_verified") is not False and not is_test_address(r.get("requester_email"))
        if recall is None:
            company = r["typed_company"]
        out.append(
            r
            | {
                "recall_title": recall.title if recall else r["typed_title"],
                "recall_number": recall.source_id if recall else None,
                "notice_company": company,
                "notice_sites": sites,
                "domain_matches": trusted and domain_matches(r["requester_domain"], company, sites),
                "requester_test_address": is_test_address(r.get("requester_email")),
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
            raise AccessHeldElsewhere("another company is already approved for this recall") from exc
        if code == "23514":  # check_violation, raised by the function for a move it does not allow
            raise AccessStateConflict("that decision is not allowed for this request's current status") from exc
        if code == "P0002":  # no_data_found
            raise LookupError("access request not found") from exc
        raise
    log_event(log, "access_decided", firm_id=str(firm_id), access_id=str(access_id), status=status)
    return UUID(str(firm_id))
