"""Every route in 01-backend.md section 9. Read routes are implemented over the database in M1 so the UI owner
can build against `make seed`; write routes keep their final contract and return 501 until their milestone."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from recallops import portal as portal_service
from recallops import programs as program_service
from recallops.api import schemas as s
from recallops.api import views
from recallops.api.deps import PublicDb, TenantDb
from recallops.api.problems import NotYetImplemented, PaymentRequired
from recallops.auth.deps import AdminDep, ClaimTokenDep, FirmDep, build_claim_verifier
from recallops.billing.fake import AllowAllBilling
from recallops.billing.interface import BillingGate
from recallops.claims.review import ReviewConflict, submit_action
from recallops.db.engine import app_engine
from recallops.db.repos import ClaimsRepo, ProgramsRepo, RunsRepo, UsageRepo
from recallops.db.session import tenant_session
from recallops.models import Claim, Recall, RecallProgram, WorkerHeartbeat
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.settings import get_settings
from recallops.tenancy import FirmContext

Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": s.Problem, "description": "not found, or belongs to another firm"}}
AUTH: Responses = {401: {"model": s.Problem}, 403: {"model": s.Problem}}
LATER: Responses = {501: {"model": s.Problem, "description": "contract final, implementation in a later milestone"}}
IMAGE: Responses = {200: {"content": {"image/jpeg": {}, "image/png": {}}}}
PDF: Responses = {200: {"content": {"application/pdf": {}}}}

firm = APIRouter(prefix="/v1", tags=["firm"], responses=AUTH)
billing = APIRouter(prefix="/v1/billing", tags=["billing (component 3)"], responses={**AUTH, **LATER})
portal = APIRouter(prefix="/portal", tags=["portal"])
internal = APIRouter(prefix="/internal", tags=["internal"])
health = APIRouter(tags=["health"])


def billing_gate() -> BillingGate:
    """The payments owner swaps in the plan-aware gate here (component 3)."""
    return AllowAllBilling()


# Public archive


@firm.get("/recalls", response_model=s.RecallPage, summary="Search the public recall archive (full text)")
def search_recalls(
    _: FirmDep, db: PublicDb, q: str | None = None, since: date | None = None, limit: int = Query(25, ge=1, le=100)
) -> s.RecallPage:
    stmt = select(Recall)
    if q:
        query = func.plainto_tsquery("simple", q)
        stmt = stmt.where(text("search @@ plainto_tsquery('simple', :q)").bindparams(q=q)).order_by(
            func.ts_rank(text("search"), query).desc(), Recall.recall_date.desc()
        )
    else:
        stmt = stmt.order_by(Recall.recall_date.desc())
    if since:
        stmt = stmt.where(Recall.recall_date >= since)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.limit(limit)).all()
    return s.RecallPage(items=[views.recall_summary(r) for r in rows], total=total)


# Programs


def _program_view(db: Session, ctx: FirmContext, p: RecallProgram) -> s.Program:
    recall = db.get(Recall, p.recall_id) if p.recall_id else None
    counts = ProgramsRepo(db, ctx).claim_status_counts(p.id)
    return views.program(
        p, recall.title if recall else "Manual recall", recall.units_affected if recall else None, counts
    )


@firm.get("/programs", response_model=s.ProgramList, summary="List the firm's recall programs")
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
    responses=LATER,
    summary="Open a program from a recall; runs rule extraction and returns rules for review",
)
def create_program(body: s.ProgramCreate, ctx: AdminDep, db: TenantDb) -> s.Program:
    if (body.recall_id is None) == (body.manual is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give exactly one of recall_id or manual")
    try:
        program, _, _ = program_service.create_program(
            db,
            ctx,
            get_settings(),
            billing_gate(),
            body.recall_id,
            body.manual.model_dump() if body.manual else None,
            body.value_threshold_cents,
        )
    except program_service.NotEntitled as exc:
        raise PaymentRequired(exc.action, exc.reason) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recall not found") from exc
    return _program_view(db, ctx, program)


@firm.patch(
    "/programs/{program_id}",
    response_model=s.Program,
    responses={**NOT_FOUND, **LATER},
    summary="Edit rules, threshold, or status",
)
def patch_program(program_id: UUID, body: s.ProgramPatch, ctx: AdminDep, db: TenantDb) -> s.Program:
    p = ProgramsRepo(db, ctx).get(program_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    changes = body.model_dump(mode="json", exclude_unset=True)
    try:
        p = program_service.patch_program(db, ctx, get_settings(), billing_gate(), p, changes)
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
    responses=NOT_FOUND,
    summary="Claims queue, oldest first, with a status filter and keyset cursor",
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
    responses={**NOT_FOUND, **LATER},
    summary="Issue n single-claim portal links for distribution",
)
def issue_links(program_id: UUID, body: s.IssueRequest, ctx: AdminDep, db: TenantDb) -> s.IssueResponse:
    p = ProgramsRepo(db, ctx).get(program_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "program not found")
    try:
        links = program_service.issue_links(db, ctx, get_settings(), build_claim_verifier(), p, body.n)
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return s.IssueResponse(links=[s.IssuedLink(claim_id=c, url=u) for c, u in links])


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
        created_at=c.created_at,
        submitted_at=c.submitted_at,
        decided_at=c.decided_at,
    )


@firm.get(
    "/claims/{claim_id}/photos/{photo_id}",
    responses={**NOT_FOUND, **IMAGE},
    response_class=FileResponse,
    summary="Photo bytes (dev stand-in for a signed storage URL)",
)
def get_photo(claim_id: UUID, photo_id: UUID, ctx: FirmDep, db: TenantDb) -> FileResponse:
    photo = next((p for p in ClaimsRepo(db, ctx).photos(claim_id) if p.id == photo_id), None)
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo not found")
    path = get_settings().storage_dir / photo.storage_path
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "photo file missing from storage")
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff"})


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
    responses={**NOT_FOUND, **LATER},
    summary="Reviewer action: resume_override, request_info, mark_fraud (writes audit_log)",
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
        )
    except ReviewConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    db.flush()
    db.expire_all()
    return get_claim(claim_id, ctx, db)


# MPR (M4)


@firm.get(
    "/programs/{program_id}/mpr",
    response_model=s.MprReport,
    responses={**NOT_FOUND, **LATER},
    summary="MPR for a period (YYYY-MM)",
)
def get_mpr(program_id: UUID, ctx: FirmDep, period: str = Query(pattern=r"^\d{4}-\d{2}$")) -> s.MprReport:
    raise NotYetImplemented("GET /v1/programs/{id}/mpr", "M4")


@firm.post(
    "/programs/{program_id}/mpr/generate",
    response_model=s.MprReport,
    responses={**NOT_FOUND, **LATER},
    summary="Generate the MPR for a period",
)
def generate_mpr(program_id: UUID, ctx: AdminDep, period: str = Query(pattern=r"^\d{4}-\d{2}$")) -> s.MprReport:
    raise NotYetImplemented("POST /v1/programs/{id}/mpr/generate", "M4")


@firm.get("/programs/{program_id}/mpr/{report_id}.pdf", responses={**NOT_FOUND, **LATER, **PDF}, summary="MPR as PDF")
def mpr_pdf(program_id: UUID, report_id: UUID, ctx: FirmDep) -> None:
    raise NotYetImplemented("GET /v1/programs/{id}/mpr/{report_id}.pdf", "M4")


# Usage


@firm.get("/usage", response_model=s.Usage, summary="Current period meters (read by the billing UI)")
def get_usage(ctx: FirmDep, db: TenantDb) -> s.Usage:
    period = date.today().replace(day=1)
    meter = UsageRepo(db, ctx).for_period(period)
    open_programs = sum(1 for p in ProgramsRepo(db, ctx).list() if p.status == "open")
    return s.Usage(
        period_start=period,
        claims_adjudicated=meter.claims_adjudicated if meter else 0,
        model_cost_usd=f"{meter.model_cost_usd:.6f}" if meter else "0.000000",
        open_programs=open_programs,
    )


# Billing (component 3 replaces these stubs)


@billing.post("/checkout", response_model=s.CheckoutSession, summary="Stripe Checkout session for the growth plan")
def checkout(ctx: AdminDep) -> s.CheckoutSession:
    raise NotYetImplemented("POST /v1/billing/checkout", "M3", "payments owner")


@billing.post("/portal", response_model=s.CheckoutSession, summary="Stripe Customer Portal session")
def customer_portal(ctx: AdminDep) -> s.CheckoutSession:
    raise NotYetImplemented("POST /v1/billing/portal", "M3", "payments owner")


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
        portal_service.submit(session, pc, body.model_dump())
        session.expire_all()
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


@portal.post(
    "/{token}/photos",
    response_model=s.Photo,
    responses={**NOT_FOUND, 409: {"model": s.Problem}, 422: {"model": s.Problem}},
    summary="Upload a photo (multipart, JPEG or PNG); sha256 and pHash computed server-side",
)
def portal_photo(
    token: str, token_ctx: ClaimTokenDep, kind: Annotated[str, Form()], file: Annotated[UploadFile, File()]
) -> s.Photo:
    ctx, claim_id = _portal(token, token_ctx)
    settings = get_settings()
    data = file.file.read(settings.max_photo_bytes + 1)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            photo, replaced = portal_service.add_photo(
                session,
                pc,
                settings.storage_dir,
                kind,
                file.content_type,
                data,
                settings.max_photo_bytes,
                settings.max_photo_pixels,
            )
        except portal_service.NotEditable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except portal_service.PhotoRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        view = views.photo(photo)
    for path in replaced:  # only after the commit: a failed commit keeps the old rows and their files
        path.unlink(missing_ok=True)
    return view


@portal.post(
    "/{token}/answer",
    response_model=s.PortalView,
    responses={**NOT_FOUND, 409: {"model": s.Problem}},
    summary="Reply to needs_info; the claim is adjudicated again",
)
def portal_answer(token: str, body: s.PortalAnswer, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim_id = _portal(token, token_ctx)
    with tenant_session(ctx) as session:
        pc = _portal_claim_in(session, token, claim_id)
        try:
            portal_service.answer(session, pc, body.message)
        except portal_service.NotEditable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        session.expire_all()
        return _portal_view(session, _portal_claim_in(session, token, claim_id))


@portal.post(
    "/{token}/payout",
    response_model=s.PortalView,
    responses={**NOT_FOUND, **LATER},
    summary="Payout destination (Connect onboarding handoff is component 3)",
)
def portal_payout(token: str, body: s.PortalPayout, token_ctx: ClaimTokenDep) -> s.PortalView:
    raise NotYetImplemented("POST /portal/{token}/payout", "M4", "backend owner with payments owner")


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


@internal.post("/stripe/webhook", responses=LATER, summary="Stripe webhook (component 3 replaces this stub)")
def stripe_webhook() -> dict[str, str]:
    raise NotYetImplemented("POST /internal/stripe/webhook", "M3", "payments owner")


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
    return s.Health(status="ok", database=True, worker_heartbeat_age_s=age_s)
