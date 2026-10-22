"""Tenant-scoped firm APIs, single-claim portal routes, and signed integration webhooks."""

from __future__ import annotations

import base64
import re
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from recallops import access as access_service
from recallops import company as company_service
from recallops import intake as intake_service
from recallops import portal as portal_service
from recallops import programs as program_service
from recallops.api import schemas as s
from recallops.api import views
from recallops.api.deps import PublicDb, TenantDb
from recallops.api.guards import limited
from recallops.api.problems import PaymentRequired
from recallops.auth.deps import AdminDep, ClaimTokenDep, FirmDep, build_claim_verifier, build_provider, current_user
from recallops.auth.provider import AuthUnavailable, NoFirmError, OperatorHasNoFirm, Principal
from recallops.billing import service as billing_service
from recallops.billing.client import PaymentError
from recallops.billing.interface import BillingGate
from recallops.billing.policy import PlanBilling, active_products
from recallops.billing.service import billing_summary
from recallops.claims.review import ReviewConflict, submit_action
from recallops.db.engine import app_engine
from recallops.db.repos import ClaimsRepo, ProgramsRepo, RunsRepo, UsageRepo
from recallops.db.session import set_firm, tenant_session
from recallops.fraud.view import FraudGraphView
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, Recall, RecallAccess, RecallProgram, WorkerHeartbeat
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.settings import get_settings
from recallops.storage import InvalidKey, Storage, StorageError, build_storage
from recallops.tenancy import FirmContext

log = get_logger("api.routes")

Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": s.Problem, "description": "not found, or belongs to another firm"}}
AUTH: Responses = {401: {"model": s.Problem}, 403: {"model": s.Problem}}
CONFLICT: Responses = {409: {"model": s.Problem, "description": "the resource is not in a state that allows this"}}
PAYMENT: Responses = {402: {"model": s.Problem, "description": "over the plan's limit (component 3 entitlement)"}}
IMAGE: Responses = {200: {"content": {"image/jpeg": {}, "image/png": {}}}}
PDF: Responses = {200: {"content": {"application/pdf": {}}}}
MPR_PERIOD = r"^\d{4}-(0[1-9]|1[0-2])$"
PHOTO_STORAGE_DOWN = "photo storage unavailable"


def require_approved_firm(ctx: FirmDep, db: TenantDb) -> None:
    """Company verification (decisions log 2026-10-22): until the operator approves a recall for this company, the
    only routes it reaches are the account, access, and billing routes. The operator has no company (plan v3)."""
    if not access_service.firm_approved(db, ctx, get_settings()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, access_service.PENDING_DETAIL)


def require_operator(principal: Annotated[Principal, Depends(current_user)]) -> str:
    """The platform operator: the verified token's user id in PLATFORM_OPERATOR_USER_IDS, or that account's verified
    email in PLATFORM_OPERATOR_EMAILS. No organization needed."""
    if not access_service.is_operator(get_settings(), principal.user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the platform operator may do this")
    return str(principal.user_id)


OperatorDep = Annotated[str, Depends(require_operator)]


def caller_is_operator(principal: Annotated[Principal, Depends(current_user)]) -> bool:
    """Declared before TenantDb in a route's parameters, so a slow Clerk lookup runs before the tenant session opens
    and never holds a database connection."""
    return access_service.is_operator(get_settings(), principal.user_id)


CallerIsOperator = Annotated[bool, Depends(caller_is_operator)]
APPROVED = [Depends(require_approved_firm)]
firm = APIRouter(prefix="/v1", tags=["firm"], responses=AUTH, dependencies=APPROVED)
# Outside the approval gate: who is signed in, onboarding, the plan, and paying for it work before the operator
# approves the company (plan v3: plans are chosen at sign-up).
account = APIRouter(prefix="/v1", tags=["account"], responses=AUTH)
public = APIRouter(prefix="/v1", tags=["public"])
access = APIRouter(prefix="/v1", tags=["access"], responses=AUTH)
operator = APIRouter(prefix="/v1/operator", tags=["operator"], responses=AUTH)
billing = APIRouter(
    prefix="/v1/billing",
    tags=["billing"],
    responses={**AUTH, **NOT_FOUND, **CONFLICT, 503: {"model": s.Problem}},
)
portal = APIRouter(prefix="/portal", tags=["portal"])
internal = APIRouter(prefix="/internal", tags=["internal"])
health = APIRouter(tags=["health"])


def billing_gate(session: Session | None = None) -> BillingGate:
    return PlanBilling(app_engine(), get_settings(), session)


# Public archive


RECALL_NUMBER = re.compile(r"^\d{2}-?\d{3,4}$")


@access.get("/recalls", response_model=s.RecallPage, summary="Search the public recall archive (full text)")
def search_recalls(
    _: FirmDep, db: PublicDb, q: str | None = None, since: date | None = None, limit: int = Query(25, ge=1, le=100)
) -> s.RecallPage:
    stmt = select(Recall)
    # A recall number ("27024", or "23-756" as the monthly report sheet writes it) is not in the search text: match
    # it directly, ahead of any text match (the search box offers it; plan v3 review).
    number = re.sub(r"\D", "", q) if q and RECALL_NUMBER.match(q.strip()) else None
    if q:
        query = func.plainto_tsquery("simple", q)
        matches = text("search @@ plainto_tsquery('simple', :q)").bindparams(q=q)
        stmt = stmt.where(or_(matches, Recall.source_id == number) if number else matches).order_by(
            (Recall.source_id == number).desc() if number else Recall.recall_date.desc(),
            func.ts_rank(text("search"), query).desc(),
            Recall.recall_date.desc(),
        )
    else:
        stmt = stmt.order_by(Recall.recall_date.desc())
    if since:
        stmt = stmt.where(Recall.recall_date >= since)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.limit(limit)).all()
    return s.RecallPage(items=[views.recall_summary(r) for r in rows], total=total)


# The signed-in account (plan v3: one account is one company; the operator has none)


@account.get(
    "/me",
    response_model=s.Me,
    responses={503: {"model": s.Problem}},
    summary="Who is signed in: the operator, or a company with its plan and setup state",
)
def me(principal: Annotated[Principal, Depends(current_user)]) -> s.Me:
    """Resolving the company first means an existing company never waits on Clerk (a Clerk outage only delays new
    accounts); only a new account is checked against the operator, and an unknown answer creates nothing (503)."""
    settings = get_settings()
    found = access_service.clerk_emails(settings, principal.user_id)
    email = found.primary if found else None
    if principal.user_id in settings.operator_ids():
        return s.Me(operator=True, email=email, firm=None)
    try:
        ctx = build_provider().firm_context(principal)
    except OperatorHasNoFirm:
        return s.Me(operator=True, email=email, firm=None)
    except NoFirmError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except AuthUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "sign-in verification unavailable") from exc
    with tenant_session(ctx) as session:
        return s.Me(operator=False, email=email, firm=s.MeFirm(**company_service.summary(session, ctx, settings)))


@account.post(
    "/onboarding",
    response_model=s.OnboardingResult,
    responses={**CONFLICT, 503: {"model": s.Problem}},
    summary="Name the company and choose a plan (Growth returns a Stripe Checkout link)",
)
def onboarding(body: s.OnboardingRequest, ctx: AdminDep) -> s.OnboardingResult:
    settings = get_settings()
    try:
        with tenant_session(ctx) as session:
            company_service.onboard(session, ctx, body.name, body.plan)
    except company_service.AlreadyOnboarded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    checkout_url = None
    if body.plan == "growth":  # once per company (AlreadyOnboarded), so no billing rate limit here
        try:
            checkout_url = billing_service.checkout(app_engine(), settings, ctx)
        except PaymentError as exc:  # the company is set up on Free; Billing offers the upgrade again
            log_event(log, "onboarding_checkout_failed", firm_id=str(ctx.firm_id), error=type(exc).__name__)
    with tenant_session(ctx) as session:
        firm_view = s.MeFirm(**company_service.summary(session, ctx, settings))
    return s.OnboardingResult(firm=firm_view, checkout_url=checkout_url)


@account.patch(
    "/firm",
    response_model=s.MeFirm,
    responses={**CONFLICT},
    summary="Rename the company (before the operator approves it; after that, the operator renames it)",
)
def rename_firm(body: s.FirmPatch, ctx: AdminDep, db: TenantDb) -> s.MeFirm:
    try:
        company_service.rename(db, ctx, get_settings(), body.name)
    except company_service.RenameLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return s.MeFirm(**company_service.summary(db, ctx, get_settings()))


@public.get("/plans", response_model=list[s.PlanInfo], summary="What each plan allows and costs (public)")
def plans() -> list[s.PlanInfo]:
    return [s.PlanInfo(**p) for p in company_service.plans(get_settings())]


# Company verification (migration 0008; decisions log 2026-10-22)


def _access_view(db: Session, row: RecallAccess) -> s.AccessRequest:
    recall = db.get(Recall, row.recall_id) if row.recall_id else None
    if recall is not None:
        product = db.scalars(
            select(RecallProgram)
            .where(
                RecallProgram.firm_id == row.firm_id,
                RecallProgram.recall_id == recall.id,
                RecallProgram.status != "closed",
            )
            .order_by(RecallProgram.created_at.desc())
        ).first()
    else:
        product = db.scalars(select(RecallProgram).where(RecallProgram.access_id == row.id)).first()
    return s.AccessRequest(
        id=row.id,
        recall_id=row.recall_id,
        title=recall.title if recall else (row.typed_title or "Typed notice"),
        typed=row.recall_id is None,
        typed_company=row.typed_company,
        typed_notice=row.typed_notice,
        program_id=product.id if product else None,
        program_status=product.status if product else None,
        status=row.status,
        note=row.note,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


@access.get("/access", response_model=s.AccessStatus, summary="This company's access: approved or not, and why")
def access_status(ctx: FirmDep, operator: CallerIsOperator, db: TenantDb) -> s.AccessStatus:
    settings = get_settings()
    return s.AccessStatus(
        required=access_service.required(settings),
        approved=access_service.firm_approved(db, ctx, settings),
        operator=operator,
        requests=[_access_view(db, r) for r in access_service.own_requests(db, ctx)],
    )


@access.post(
    "/access",
    response_model=s.AccessRequest,
    status_code=201,
    responses={**NOT_FOUND, **CONFLICT, **PAYMENT},
    summary="Ask the platform operator for access to a recall, or to a typed notice not in the archive",
)
def request_access(body: s.AccessRequestCreate, ctx: AdminDep, db: TenantDb) -> s.AccessRequest:
    if (body.recall_id is None) == (body.typed is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give exactly one of recall_id or typed")
    settings = get_settings()
    typed = access_service.Typed(body.typed.title, body.typed.company, body.typed.notice) if body.typed else None
    domain = access_service.requester_domain(settings, ctx.user_id)
    requester = access_service.requester_of(settings, ctx.user_id)
    try:
        access_service.check_can_request(db, ctx, settings)
        access_id = access_service.request_access(db, ctx, settings, body.recall_id, typed, domain, requester)
    except access_service.NotOnboarded as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except access_service.OverPlan as exc:
        raise PaymentRequired("create_program", str(exc)) from exc
    except access_service.AccessConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recall not found") from exc
    row = db.get(RecallAccess, access_id)
    assert row is not None
    return _access_view(db, row)


@operator.get("/access", response_model=list[s.OperatorAccessRequest], summary="Every company's requests")
def operator_requests(_: OperatorDep, db: PublicDb) -> list[s.OperatorAccessRequest]:
    return [s.OperatorAccessRequest(**_operator_fields(r)) for r in access_service.operator_list(db)]


@operator.post(
    "/access/{access_id}",
    response_model=s.OperatorAccessRequest,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Approve (the product opens), reject, or revoke (the product closes) a company's request",
)
def operator_decide(access_id: UUID, body: s.AccessDecision, op: OperatorDep, db: PublicDb) -> s.OperatorAccessRequest:
    """One transaction (plan v3 red team): the decision, the audit row, and the company's product. The product step
    runs under the company's firm context in a savepoint, so it sees the approval and a product that cannot be made
    never undoes the decision; the outcome is returned for the operator to see."""
    settings = get_settings()
    try:
        firm_id = access_service.decide(db, op, access_id, body.decision, body.note)
    except access_service.AccessConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "access request not found") from exc
    set_firm(db, firm_id)
    ctx = FirmContext(firm_id=firm_id, user_id=op, role="admin")
    db.add(
        AuditLog(
            firm_id=firm_id,
            actor=op,
            action="access_decided",
            target_type="recall_access",
            target_id=str(access_id),
            before={},
            after={"status": body.decision, "note": body.note},
        )
    )
    product: dict[str, Any] | None = None
    if body.decision in ("approved", "revoked"):
        try:
            with db.begin_nested():
                if body.decision == "approved":
                    product = program_service.start_product(db, ctx, settings, billing_gate(db), access_id)
                else:
                    program_service.stop_product(db, ctx, settings, billing_gate(db), access_id)
        except (program_service.Conflict, program_service.NotEntitled, access_service.AccessDenied) as exc:
            log_event(log, "product_step_failed", firm_id=str(firm_id), error=type(exc).__name__, level=30)
            product = {"id": None, "status": None, "error": "failed"}
    row = next(r for r in access_service.operator_list(db) if r["id"] == access_id)
    return s.OperatorAccessRequest(**_operator_fields(row), product=s.ProductOutcome(**product) if product else None)


def _operator_fields(r: dict[str, Any]) -> dict[str, Any]:
    keys = set(s.OperatorAccessRequest.model_fields) - {"title", "typed", "product"}
    return {k: r.get(k) for k in keys} | {
        "title": r.get("recall_title") or "Typed notice",
        "typed": r["recall_id"] is None,
    }


# Programs


def _program_view(db: Session, ctx: FirmContext, p: RecallProgram) -> s.Program:
    recall = db.get(Recall, p.recall_id) if p.recall_id else None
    counts = ProgramsRepo(db, ctx).claim_status_counts(p.id)
    # A typed notice keeps its title in the program's notice document (it read "Manual recall" everywhere).
    title = recall.title if recall else program_service.program_source(db, p)[0]
    view = views.program(p, title, recall.units_affected if recall else None, counts)
    settings = get_settings()
    live = program_service.takes_new_claims(db, p, settings)
    return view.model_copy(
        update={
            "intake_url": intake_service.link(settings, p) if live else None,
            "takes_new_claims": live,
            "needs_refund_amount": program_service.needs_refund_amount(p),
        }
    )


@firm.get("/programs", response_model=s.ProgramList, summary="List the company's products")
def list_programs(ctx: FirmDep, db: TenantDb) -> s.ProgramList:
    return s.ProgramList(items=[_program_view(db, ctx, p) for p in ProgramsRepo(db, ctx).list()])


@firm.get("/programs/{program_id}", response_model=s.Program, responses=NOT_FOUND, summary="Program with rules")
def get_program(program_id: UUID, ctx: FirmDep, db: TenantDb) -> s.Program:
    p = ProgramsRepo(db, ctx).get(program_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    return _program_view(db, ctx, p)


@firm.post(
    "/programs",
    response_model=s.Program,
    status_code=201,
    responses={**NOT_FOUND, **CONFLICT, **PAYMENT},
    summary="Create the product for an approved recall (a draft; approval normally opens it automatically)",
)
def create_program(body: s.ProgramCreate, ctx: AdminDep, db: TenantDb) -> s.Program:
    if (body.recall_id is None) == (body.manual is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give exactly one of recall_id or manual")
    try:
        program, _, _ = program_service.create_program(
            db,
            ctx,
            get_settings(),
            billing_gate(db),
            body.recall_id,
            body.manual.model_dump() if body.manual else None,
            body.value_threshold_cents,
            body.access_id,
        )
    except program_service.NotEntitled as exc:
        raise PaymentRequired(exc.action, exc.reason) from exc
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recall not found") from exc
    return _program_view(db, ctx, program)


@firm.patch(
    "/programs/{program_id}",
    response_model=s.Program,
    responses={**NOT_FOUND, **PAYMENT, **CONFLICT},
    summary="Edit rules, threshold, or status",
)
def patch_program(program_id: UUID, body: s.ProgramPatch, ctx: AdminDep, db: TenantDb) -> s.Program:
    p = ProgramsRepo(db, ctx).get(program_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    changes = body.model_dump(mode="json", exclude_unset=True)
    try:
        p = program_service.patch_program(db, ctx, get_settings(), billing_gate(db), p, changes)
    except program_service.NotEntitled as exc:
        raise PaymentRequired(exc.action, exc.reason) from exc
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _program_view(db, ctx, p)


def _decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        created, claim_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created), UUID(claim_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


def _encode_cursor(c: Claim) -> str:
    return base64.urlsafe_b64encode(f"{c.created_at.isoformat()}|{c.id}".encode()).decode()


@firm.get(
    "/programs/{program_id}/claims",
    response_model=s.ClaimPage,
    responses={**NOT_FOUND, 400: {"model": s.Problem, "description": "the cursor is not one this API issued"}},
    summary="Claims queue, oldest first, with a status filter and keyset cursor (drafts only with status=draft)",
)
def list_claims(
    program_id: UUID,
    ctx: FirmDep,
    db: TenantDb,
    claim_status: Annotated[s.ClaimStatus | None, Query(alias="status")] = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> s.ClaimPage:
    if ProgramsRepo(db, ctx).get(program_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    rows = ClaimsRepo(db, ctx).list_for_program(program_id, claim_status, _decode_cursor(cursor), limit + 1)
    page, more = rows[:limit], len(rows) > limit
    return s.ClaimPage(
        items=[views.claim_summary(c) for c in page], next_cursor=_encode_cursor(page[-1]) if more and page else None
    )


@firm.post(
    "/programs/{program_id}/claims/issue",
    response_model=s.IssueResponse,
    include_in_schema=False,
)
def issue_links(program_id: UUID, body: s.IssueRequest, ctx: AdminDep, db: TenantDb) -> s.IssueResponse:
    """Development and tests only (plan v3: every claimant uses the product's one link). Not in the contract, and
    refused in prod."""
    if get_settings().app_env == "prod":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    p = ProgramsRepo(db, ctx).get(program_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    try:
        links = program_service.issue_links(db, ctx, get_settings(), build_claim_verifier(), p, body.n)
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return s.IssueResponse(links=[s.IssuedLink(claim_id=c, url=u) for c, u in links])


@firm.post(
    "/programs/{program_id}/intake/replace",
    response_model=s.Program,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Replace the product's claim link (the old link stops at once)",
)
def replace_intake_link(program_id: UUID, ctx: AdminDep, db: TenantDb) -> s.Program:
    try:
        program = intake_service.replace_link(db, ctx, program_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found") from exc
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _program_view(db, ctx, program)


@firm.post(
    "/claims/{claim_id}/link",
    response_model=s.IssuedLink,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Reissue one claim's portal link; the previous link stops working (admin)",
)
def reissue_link(claim_id: UUID, ctx: AdminDep, db: TenantDb) -> s.IssuedLink:
    try:
        url = program_service.reissue_link(db, ctx, get_settings(), build_claim_verifier(), claim_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found") from exc
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return s.IssuedLink(claim_id=claim_id, url=url)


# Claims


@firm.get(
    "/claims/{claim_id}",
    response_model=s.ClaimDetail,
    responses=NOT_FOUND,
    summary="Claim with photos, decision, latest run, clusters, payout, audit trail",
)
def get_claim(claim_id: UUID, ctx: FirmDep, db: TenantDb) -> s.ClaimDetail:
    repo = ClaimsRepo(db, ctx)
    c = repo.get(claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    run = RunsRepo(db, ctx).latest_for_claim(claim_id)
    pay = repo.payout(claim_id)
    return s.ClaimDetail(
        id=c.id,
        program_id=c.program_id,
        status=c.status,
        claimant=views.claimant(c.claimant),
        model_claimed=c.model_claimed,
        serial_claimed=c.serial_claimed,
        date_code_claimed=c.date_code_claimed,
        retailer_claimed=c.retailer_claimed,
        purchase_date=c.purchase_date,
        quantity=c.quantity,
        refund_cents=c.refund_cents,
        remedy=c.remedy,
        photos=[views.photo(p) for p in repo.photos(claim_id)],
        decision=views.decision(c),
        run=views.run_summary(run) if run else None,
        clusters=[views.cluster(f) for f in repo.clusters(claim_id)],
        payout=views.payout(pay) if pay else None,
        audit=[views.audit(a) for a in repo.audit(claim_id)],
        messages=[views.message(m) for m in c.messages or []],
        created_at=c.created_at,
        submitted_at=c.submitted_at,
        decided_at=c.decided_at,
    )


@firm.get(
    "/claims/{claim_id}/photos/{photo_id}",
    responses={**NOT_FOUND, **IMAGE, 503: {"model": s.Problem, "description": "photo storage unavailable"}},
    response_class=Response,
    summary="Photo bytes, read from the private store after the firm check (no storage URL reaches the browser)",
)
def get_photo(claim_id: UUID, photo_id: UUID, ctx: FirmDep, db: TenantDb) -> Response:
    # The bytes are proxied from storage after the RLS lookup, so the bucket stays private and no signed storage URL
    # reaches a browser. A storage failure answers a fixed 503 detail, never the storage error's text. (A comment,
    # not a docstring: a docstring would become the route's description in docs/contracts/openapi.json.)
    photo = next((p for p in ClaimsRepo(db, ctx).photos(claim_id) if p.id == photo_id), None)
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    try:
        data = photo_storage().get(photo.storage_path)
    except InvalidKey:
        data = None
    except StorageError as exc:
        log_event(
            log, "photo_read_failed", level=40, firm_id=str(ctx.firm_id), claim_id=str(claim_id), error=str(exc)[:300]
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PHOTO_STORAGE_DOWN) from exc
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo file missing from storage")
    media = "image/png" if photo.storage_path.lower().endswith(".png") else "image/jpeg"
    return Response(content=data, media_type=media, headers={"X-Content-Type-Options": "nosniff"})


@firm.get(
    "/claims/{claim_id}/trace",
    response_model=s.Trace,
    responses=NOT_FOUND,
    summary="agent_steps of the claim's latest run (docs/contracts/trace.md)",
)
def get_trace(claim_id: UUID, ctx: FirmDep, db: TenantDb) -> s.Trace:
    runs = RunsRepo(db, ctx)
    run = runs.latest_for_claim(claim_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no run for this claim")
    settings = get_settings()
    caps = s.Caps(
        max_steps=settings.run_cap_steps, max_usd=str(settings.run_cap_usd), max_seconds=settings.run_cap_seconds
    )
    steps = runs.steps(run.id)
    used = s.Used(
        steps=sum(1 for t in steps if t.counted),
        cost_usd=f"{sum((t.nominal_cost_usd for t in steps), start=0):.6f}",
        seconds=round(sum(t.latency_ms for t in steps) / 1000, 3),
    )
    return s.Trace(run=views.run_summary(run), caps=caps, used=used, steps=[views.trace_step(t) for t in steps])


@firm.post(
    "/claims/{claim_id}/actions",
    response_model=s.ClaimDetail,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Reviewer action: resume_override, request_info, mark_fraud, mark_delivered (writes audit_log)",
)
def claim_action(claim_id: UUID, body: s.ClaimAction, ctx: FirmDep, db: TenantDb) -> s.ClaimDetail:
    c = ClaimsRepo(db, ctx).get(claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim not found")
    try:
        submit_action(
            db,
            app_engine(),
            ctx,
            c,
            body.action,
            body.outcome,
            body.message,
            DbPayoutLedger(app_engine()),
            billing_gate(),
            get_settings().phash_duplicate_bits,
        )
    except ReviewConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    db.expire_all()
    return get_claim(claim_id, ctx, db)


# MPR (M4)


def _mpr_view(r: Any) -> s.MprReport:
    return s.MprReport(
        id=r.id,
        program_id=r.program_id,
        period_start=r.period_start,
        period_end=r.period_end,
        payload=r.payload,
        reconciled=r.reconciled,
        generated_at=r.generated_at,
    )


@firm.get(
    "/programs/{program_id}/mpr",
    response_model=s.MprReport,
    responses=NOT_FOUND,
    summary="Latest MPR generated for a period (YYYY-MM)",
)
def get_mpr(program_id: UUID, ctx: FirmDep, db: TenantDb, period: str = Query(pattern=MPR_PERIOD)) -> s.MprReport:
    from recallops.models import MprReport
    from recallops.mpr.generate import Period

    p = Period.parse(period)
    row = db.scalars(
        select(MprReport)
        .where(MprReport.firm_id == ctx.firm_id, MprReport.program_id == program_id, MprReport.period_start == p.start)
        .order_by(MprReport.generated_at.desc())
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no report for that period; generate one first")
    return _mpr_view(row)


@firm.post(
    "/programs/{program_id}/mpr/generate",
    response_model=s.MprReport,
    responses=NOT_FOUND,
    summary="Generate the MPR for a period; reconciled is true when the independent recount agrees",
)
def generate_mpr(
    program_id: UUID,
    ctx: AdminDep,
    period: str = Query(pattern=MPR_PERIOD),
    manual: Annotated[
        s.MprManual | None, Body(description="boxes the system cannot know; omitted boxes use their defaults")
    ] = None,
) -> s.MprReport:
    """One REPEATABLE READ snapshot: the SQL report and the recount's row exports see the same data, so a payout
    delivered while the report is generated cannot produce a false discrepancy."""
    from recallops.models import AuditLog, MprReport
    from recallops.mpr.generate import Period, build_payload
    from recallops.mpr.recount import export, reconcile, recount

    entered = manual.model_dump(exclude_none=True) if manual else {}
    with tenant_session(ctx, isolation="REPEATABLE READ") as db:
        if ProgramsRepo(db, ctx).get(program_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
        p = Period.parse(period)
        payload = build_payload(db, ctx.firm_id, program_id, p, entered)
        diffs = reconcile(payload["computed"], recount(export(db, ctx.firm_id, program_id), p.label, entered))
        payload["reconciliation"] = {"discrepancies": diffs}
        row = MprReport(
            firm_id=ctx.firm_id,
            program_id=program_id,
            period_start=p.start,
            period_end=p.end,
            payload=payload,
            reconciled=not diffs,
            generated_by=ctx.user_id,
        )
        db.add(row)
        db.flush()
        db.add(
            AuditLog(
                firm_id=ctx.firm_id,
                actor=ctx.user_id,
                action="mpr_generated",
                target_type="program",
                target_id=str(program_id),
                before=None,
                after={"period": period, "reconciled": not diffs, "manual_boxes": sorted(entered)},
            )
        )
        db.refresh(row)
        if diffs:
            log_event(log, "mpr_unreconciled", level=40, firm_id=str(ctx.firm_id), program_id=str(program_id))
        return _mpr_view(row)


@firm.get("/programs/{program_id}/mpr/{report_id}.pdf", responses={**NOT_FOUND, **PDF}, summary="MPR as PDF")
def mpr_pdf(program_id: UUID, report_id: UUID, ctx: FirmDep, db: TenantDb) -> Response:
    from recallops.models import MprReport
    from recallops.mpr.pdf import render_pdf

    row = db.scalars(
        select(MprReport).where(
            MprReport.firm_id == ctx.firm_id, MprReport.program_id == program_id, MprReport.id == report_id
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    program = ProgramsRepo(db, ctx).get(program_id)
    recall = db.get(Recall, program.recall_id) if program and program.recall_id else None
    body = render_pdf(
        recall.title if recall else "Recall program", row.payload["period"], row.payload["boxes"], row.payload["notes"]
    )
    return Response(body, media_type="application/pdf")


# Fraud graph (M5; Neo4j projection, decisions log 2026-10-18)

GRAPH_DOWN = "fraud graph unavailable"


@firm.get(
    "/programs/{program_id}/fraud-graph",
    response_model=FraudGraphView,
    responses={**NOT_FOUND, 503: {"model": s.Problem, "description": "the graph is disabled or unreachable"}},
    summary="Fraud rings across every duplicate kind: claims, links, rings (statuses from Postgres)",
)
def fraud_graph_view(
    program_id: UUID, ctx: FirmDep, db: TenantDb, limit: int = Query(500, ge=1, le=2000)
) -> FraudGraphView:
    """Program first (404 for another firm's program), then a read-only graph query whose every claim id is checked
    against this firm's claims under RLS. A graph error answers a fixed 503 detail (never the driver's message)."""
    from recallops.fraud.graph import GraphError
    from recallops.fraud.view import fraud_graph

    _open_program(db, ctx, program_id)
    store = graph_store()
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_DOWN)
    try:
        return fraud_graph(db, store, ctx, program_id, node_cap=limit)
    except GraphError as exc:
        log_event(log, "fraud_graph_unavailable", level=30, firm_id=str(ctx.firm_id), error=type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_DOWN) from exc


# Notification campaigns (M5): registrations, matching, drafts, simulated send


def _open_program(db: Session, ctx: FirmContext, program_id: UUID) -> RecallProgram:
    program = ProgramsRepo(db, ctx).get(program_id)
    if program is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    return program


@firm.post(
    "/programs/{program_id}/registrations",
    response_model=s.RegistrationUpload,
    responses={**NOT_FOUND, 413: {"model": s.Problem}},
    summary="Upload product-owner registrations (CSV, synthetic rows only); upserts on external_ref",
)
async def upload_registrations(
    program_id: UUID, ctx: AdminDep, file: Annotated[UploadFile, File(description="CSV with external_ref")]
) -> s.RegistrationUpload:
    from recallops import campaigns

    settings = get_settings()
    raw = await file.read(settings.registrations_max_bytes + 1)
    if len(raw) > settings.registrations_max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "registrations CSV too large")
    try:
        parsed = campaigns.parse_registrations(raw, settings.registrations_max_rows)
    except campaigns.UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    with tenant_session(ctx) as db:
        _open_program(db, ctx, program_id)
        accepted = campaigns.upsert_registrations(db, ctx, program_id, parsed.rows)
    return s.RegistrationUpload(accepted=accepted, rejected=parsed.rejected[:50], rejected_total=len(parsed.rejected))


@firm.post(
    "/programs/{program_id}/campaign/match",
    response_model=s.CampaignCounts,
    responses=NOT_FOUND,
    summary="Match registrations against the program's identification rules",
)
def campaign_match(program_id: UUID, ctx: AdminDep, db: TenantDb) -> s.CampaignCounts:
    from recallops import campaigns

    return s.CampaignCounts(counts=campaigns.match(db, ctx, _open_program(db, ctx, program_id)))


@firm.post(
    "/programs/{program_id}/campaign/drafts",
    response_model=s.CampaignCounts,
    responses={**NOT_FOUND, **CONFLICT},
    summary="Draft notices for matched owners (quotes the notice verbatim; one claim link per owner)",
)
def campaign_drafts(program_id: UUID, body: s.CampaignDraftRequest, ctx: AdminDep, db: TenantDb) -> s.CampaignCounts:
    from recallops import campaigns

    program = _open_program(db, ctx, program_id)
    return s.CampaignCounts(counts=campaigns.draft(db, ctx, program, list(dict.fromkeys(body.channels))))


@firm.post(
    "/programs/{program_id}/campaign/send",
    response_model=s.CampaignCounts,
    responses=NOT_FOUND,
    summary="Mark drafts sent (simulated: no message leaves the system); feeds the MPR notified_* boxes",
)
def campaign_send(program_id: UUID, body: s.CampaignSendRequest, ctx: AdminDep, db: TenantDb) -> s.CampaignCounts:
    from recallops import campaigns

    access_service.require_program(db, _open_program(db, ctx, program_id), get_settings())
    return s.CampaignCounts(counts=campaigns.send(db, ctx, program_id, body.channel))


@firm.get(
    "/programs/{program_id}/campaign",
    response_model=s.CampaignSummary,
    responses=NOT_FOUND,
    summary="Campaign status: registrations, matches, notices by channel and status, three sample drafts",
)
def campaign_summary(program_id: UUID, ctx: FirmDep, db: TenantDb) -> s.CampaignSummary:
    from recallops import campaigns

    program = _open_program(db, ctx, program_id)
    settings = get_settings()
    live = program_service.takes_new_claims(db, program, settings)
    return s.CampaignSummary(
        **campaigns.summary(db, ctx, program_id, intake_service.link(settings, program) if live else None)
    )


# Payout export batches (the check/ACH fallback, invariant 9)

CSV: Responses = {200: {"content": {"text/csv": {}}}}


def _batch_view(b: Any) -> s.ExportBatchSummary:
    return s.ExportBatchSummary(
        id=b.id,
        created_at=b.created_at,
        created_by=b.created_by,
        row_count=b.row_count,
        total_cents=b.total_cents,
        sha256=b.sha256,
    )


@firm.post(
    "/payouts/exports",
    response_model=s.ExportBatchSummary | None,
    summary="Write one export batch of this firm's approved payouts waiting for a check (null when none)",
)
def create_export(ctx: AdminDep) -> s.ExportBatchSummary | None:
    from recallops.models import ExportBatch
    from recallops.payouts.export import export_firm

    result = export_firm(app_engine(), ctx.firm_id, actor=ctx.user_id, tenant=True)
    if result is None:
        return None
    with tenant_session(ctx) as session:
        return _batch_view(session.get(ExportBatch, UUID(result["batch"])))


@firm.get("/payouts/exports", response_model=s.ExportBatchList, summary="Export batches, newest first")
def list_exports(ctx: AdminDep, db: TenantDb, limit: int = Query(50, ge=1, le=200)) -> s.ExportBatchList:
    from recallops.models import ExportBatch

    rows = db.scalars(
        select(ExportBatch)
        .where(ExportBatch.firm_id == ctx.firm_id)
        .order_by(ExportBatch.created_at.desc())
        .limit(limit)
    ).all()
    return s.ExportBatchList(items=[_batch_view(b) for b in rows])


@firm.get("/payouts/exports/{batch_id}.csv", responses={**NOT_FOUND, **CSV}, summary="Download an export batch (admin)")
def download_export(batch_id: UUID, ctx: AdminDep, db: TenantDb) -> Response:
    from recallops.models import ExportBatch

    batch = db.scalars(
        select(ExportBatch).where(ExportBatch.firm_id == ctx.firm_id, ExportBatch.id == batch_id)
    ).first()
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch not found")
    return Response(
        bytes(batch.csv),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{batch.id}.csv"', "Cache-Control": "no-store"},
    )


# Usage


@account.get(
    "/usage", response_model=s.Usage, responses=NOT_FOUND, summary="Current period meters (read by the billing UI)"
)
def get_usage(ctx: FirmDep, db: TenantDb) -> s.Usage:
    period = datetime.now(UTC).date().replace(day=1)
    meter = UsageRepo(db, ctx).for_period(period)
    open_programs = sum(1 for p in ProgramsRepo(db, ctx).list() if p.status == "open")
    return s.Usage(
        period_start=period,
        claims_adjudicated=meter.claims_adjudicated if meter else 0,
        model_cost_usd=f"{meter.model_cost_usd:.6f}" if meter else "0.000000",
        open_programs=open_programs,
        active_products=active_products(db, ctx.firm_id),
        **billing_summary(db, ctx, get_settings()),
    )


# Billing


def _billing_rate(ctx: FirmContext) -> None:
    """Billing is open before approval (plan v3), so one account could start Stripe sessions in a loop on the key that
    also pays refunds: each company gets BILLING_RATE_PER_FIRM_HOUR sessions an hour."""
    retry = limited("billing", ctx.firm_id, get_settings().billing_rate_per_firm_hour)
    if retry is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many billing requests; try again later", {"Retry-After": str(retry)}
        )


@billing.post(
    "/checkout",
    response_model=s.CheckoutSession,
    responses={429: {"model": s.Problem}},
    summary="Stripe Checkout session for the growth plan",
)
def checkout(ctx: AdminDep) -> s.CheckoutSession:
    from recallops.billing.service import checkout as create_checkout

    _billing_rate(ctx)
    try:
        return s.CheckoutSession(url=create_checkout(app_engine(), get_settings(), ctx))
    except SQLAlchemyError as exc:
        raise HTTPException(503, "payment persistence unavailable; reconcile the operation before retrying") from exc


@billing.post(
    "/portal",
    response_model=s.CheckoutSession,
    responses={429: {"model": s.Problem}},
    summary="Stripe Customer Portal session",
)
def customer_portal(ctx: AdminDep) -> s.CheckoutSession:
    from recallops.billing.service import portal as create_portal

    _billing_rate(ctx)
    return s.CheckoutSession(url=create_portal(app_engine(), get_settings(), ctx))


# Portal (token in path)


def _portal(token: str, token_ctx: Any) -> tuple[FirmContext, UUID]:
    """The verified token names the firm, so the portal opens that firm's tenant session directly (RLS); a token
    whose firm does not hold the claim finds nothing and answers 404."""
    return FirmContext(firm_id=token_ctx.firm_id, user_id="portal", role="reviewer"), token_ctx.claim_id


def _portal_claim_in(session: Session, token: str, claim_id: UUID) -> portal_service.PortalClaim:
    try:
        return portal_service.resolve(session, build_claim_verifier(), token, claim_id)
    except portal_service.LinkInvalid as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found or expired") from exc


def _portal_view(session: Session, pc: portal_service.PortalClaim) -> s.PortalView:
    return s.PortalView.model_validate(portal_service.view(session, pc))


@portal.get(
    "/intake/{intake_token}",
    response_model=s.IntakeView,
    responses=NOT_FOUND,
    summary="A product's public claim link: the company, the product, and what a claim asks for",
)
def intake_view(intake_token: str) -> s.IntakeView:
    settings = get_settings()
    try:
        found = intake_service.target(settings, intake_token)
        with tenant_session(FirmContext(firm_id=found.firm_id, user_id="intake", role="reviewer")) as session:
            program = intake_service.resolve(session, settings, found)
            return s.IntakeView(**intake_service.view(session, program))
    except intake_service.LinkInvalid as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found") from exc


@portal.post(
    "/intake/{intake_token}",
    response_model=s.IntakeStarted,
    status_code=201,
    responses={**NOT_FOUND, 415: {"model": s.Problem}, 429: {"model": s.Problem}},
    summary="Start a claim from a product's link; returns the claimant's private claim link",
)
def intake_start(intake_token: str, request: Request) -> s.IntakeStarted:
    # A JSON body forces a CORS preflight the API never grants, so another site cannot start claims from its visitors'
    # browsers (plan v3 red team); the website's own proxy sends JSON from its server.
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "send application/json")
    settings = get_settings()
    try:
        found = intake_service.target(settings, intake_token)
        ctx = FirmContext(firm_id=found.firm_id, user_id="intake", role="reviewer")
        with tenant_session(ctx) as session:
            program = intake_service.resolve(session, settings, found)
            # Counted here, after every check, so only real starts spend the link's hourly budget (plan v3 review).
            retry = limited("intake-link", program.id, settings.intake_rate_per_program_hour)
            if retry is not None:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "too many claims were started from this link in the last hour; try again later",
                    {"Retry-After": str(retry)},
                )
            claim_id, token, url = intake_service.start(session, ctx, settings, build_claim_verifier(), program)
    except intake_service.LinkInvalid as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found") from exc
    return s.IntakeStarted(claim_id=claim_id, claim_token=token, claim_url=url)


@portal.get(
    "/{token}",
    response_model=s.PortalView,
    responses=NOT_FOUND,
    summary="Program public info, required steps, status, pending question",
)
def portal_view(token: str, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim_id = _portal(token, token_ctx)
    with tenant_session(ctx) as session:
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


@portal.post(
    "/{token}/submit",
    response_model=s.PortalView,
    responses={**NOT_FOUND, 409: {"model": s.Problem}},
    summary="Submit claim fields (idempotent: a second submission changes nothing)",
)
def portal_submit(token: str, body: s.PortalSubmit, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim_id = _portal(token, token_ctx)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            portal_service.submit(session, pc, body.model_dump())
        except portal_service.LabelPhotoMissing as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        session.expire_all()
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


@portal.post(
    "/{token}/photos",
    response_model=s.Photo,
    responses={
        **NOT_FOUND,
        409: {"model": s.Problem},
        422: {"model": s.Problem},
        503: {"model": s.Problem, "description": "photo storage unavailable; nothing was saved"},
    },
    summary="Upload a photo (multipart, JPEG or PNG); sha256 and pHash computed server-side",
)
def portal_photo(
    token: str, token_ctx: ClaimTokenDep, kind: Annotated[str, Form()], file: Annotated[UploadFile, File()]
) -> s.Photo:
    ctx, claim_id = _portal(token, token_ctx)
    settings = get_settings()
    storage = photo_storage()
    data = file.file.read(settings.max_photo_bytes + 1)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            photo, replaced = portal_service.add_photo(
                session,
                pc,
                storage,
                kind,
                file.content_type,
                data,
                settings.max_photo_bytes,
                settings.max_photo_pixels,
                settings.intake_photo_max_pixels,
                settings.intake_photo_max_bytes,
            )
        except portal_service.NotEditable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except portal_service.PhotoRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except StorageError as exc:  # nothing was stored, and the transaction rolls back: no row without its bytes
            log_event(log, "portal_photo_store_failed", level=40, claim_id=str(claim_id), error=str(exc)[:300])
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PHOTO_STORAGE_DOWN) from exc
        view = views.photo(photo)
    for key in replaced:  # only after the commit: a failed commit keeps the old rows and their objects
        try:
            storage.delete(key)
        except StorageError as exc:  # the upload is committed; a leftover object is unreferenced, not an error
            log_event(log, "portal_photo_delete_failed", level=30, claim_id=str(claim_id), error=str(exc)[:300])
    return view


@portal.post(
    "/{token}/answer",
    response_model=s.PortalView,
    responses={**NOT_FOUND, 409: {"model": s.Problem}, 422: {"model": s.Problem}},
    summary="Reply to needs_info: corrected details or a new photo are checked again; a note alone goes to a person",
)
def portal_answer(token: str, body: s.PortalAnswer, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim_id = _portal(token, token_ctx)
    fields = body.model_dump(include=set(portal_service.ANSWER_FIELDS), exclude_none=True)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            portal_service.answer(session, pc, body.message, fields)
        except portal_service.NotEditable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except portal_service.EmptyAnswer as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        session.expire_all()
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


@portal.post(
    "/{token}/payout",
    response_model=s.PortalView,
    responses={
        **NOT_FOUND,
        **PAYMENT,
        **CONFLICT,
        400: {"model": s.Problem, "description": "Stripe onboarding needs the claimant's email"},
        503: {"model": s.Problem},
    },
    summary="Payout destination (Connect onboarding handoff is component 3)",
)
def portal_payout(token: str, body: s.PortalPayout, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim_id = _portal(token, token_ctx)
    if body.method == "stripe_connect":
        from recallops.payouts.stripe_connect import onboarding

        try:
            with tenant_session(ctx) as session:
                _portal_claim_in(session, token, claim_id)
            url = onboarding(app_engine(), get_settings(), ctx, claim_id)
            with tenant_session(ctx) as session:
                view = _portal_view(session, _portal_claim_in(session, token, claim_id))
        except SQLAlchemyError as exc:
            raise HTTPException(
                503, "onboarding persistence unavailable; reconcile the operation before retrying"
            ) from exc
        return view.model_copy(update={"connect_onboarding_url": url})
    settings = get_settings()
    secret = settings.graph_key_secret
    raw = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            portal_service.choose_check_payout(session, pc, body.mailing_address, raw)
        except portal_service.PayoutConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    with tenant_session(ctx) as session:
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


# Internal


@internal.post(
    "/feeds/pull",
    status_code=202,
    responses={401: {"model": s.Problem}},
    summary="Nightly feed pull (X-Internal-Secret header); pulls the CPSC windows since the last run",
)
def feeds_pull(x_internal_secret: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    import hmac

    from recallops.ingest.runner import pull_since_last

    settings = get_settings()
    if not x_internal_secret or not hmac.compare_digest(x_internal_secret, settings.internal_shared_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or wrong X-Internal-Secret")
    if settings.app_env == "prod" and settings.internal_shared_secret == "internal-dev-only":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "the example internal secret is refused in prod")
    return pull_since_last(app_engine(), settings)  # recalls and feed_runs are public tables (migration 0003)


@internal.post(
    "/stripe/webhook",
    responses={400: {"model": s.Problem}, 413: {"model": s.Problem}, 503: {"model": s.Problem}},
    summary="Signed Stripe test-mode webhook",
)
async def stripe_webhook(request: Request) -> dict[str, str]:
    from starlette.concurrency import run_in_threadpool

    from recallops.billing.webhooks import receive

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 1_000_000:
            raise HTTPException(413, "Stripe webhook body is too large")
    try:
        return await run_in_threadpool(
            receive, app_engine(), get_settings(), bytes(payload), request.headers.get("stripe-signature")
        )
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Stripe event persistence unavailable; retry this event") from exc


# Health


@health.get("/healthz", response_model=s.Health)
def healthz() -> s.Health:
    return s.Health(status="ok", database=True, worker_heartbeat_age_s=None)


@health.get("/readyz", response_model=s.Health, responses={503: {"model": s.Problem}})
def readyz() -> s.Health:
    """Database reachable and a worker heartbeat younger than WORKER_HEARTBEAT_MAX_AGE_S (01-backend.md section 9)."""
    try:
        with Session(app_engine()) as session:
            seen = session.scalar(select(func.max(WorkerHeartbeat.seen_at)))
            age = session.scalar(select(func.extract("epoch", func.now() - seen))) if seen else None
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unreachable") from exc
    age_s = round(float(age), 1) if age is not None else None
    if age_s is None or age_s > get_settings().worker_heartbeat_max_age_s:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "no worker heartbeat" if age_s is None else f"worker heartbeat is {age_s} s old",
        )
    return s.Health(status="ok", database=True, worker_heartbeat_age_s=age_s, graph=_graph_status())


_STORAGE: dict[str, Storage] = {}


def photo_storage() -> Storage:
    """One claim-photo storage per process (the Supabase client pools connections), built from settings."""
    if "storage" not in _STORAGE:
        _STORAGE["storage"] = build_storage(get_settings())
    return _STORAGE["storage"]


_GRAPH: dict[str, Any] = {}


def graph_store() -> Any:
    """One store per process (the driver pools connections); None when NEO4J_URI is empty."""
    from recallops.fraud.graph import build_graph_store

    if "store" not in _GRAPH:
        _GRAPH["store"] = build_graph_store(get_settings())
    return _GRAPH["store"]


def _graph_status() -> Any:
    from recallops.fraud.graph import graph_status

    return graph_status(graph_store())
