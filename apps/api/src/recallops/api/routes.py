"""Every route in 01-backend.md section 9. Read routes are implemented over the database in M1 so the UI owner
can build against `make seed`; write routes keep their final contract and return 501 until their milestone."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from recallops.api import schemas as s
from recallops.api import views
from recallops.api.deps import PublicDb, TenantDb
from recallops.api.problems import NotYetImplemented
from recallops.auth.deps import AdminDep, ClaimTokenDep, FirmDep, build_claim_verifier
from recallops.db.engine import internal_engine
from recallops.db.repos import ClaimsRepo, ProgramsRepo, RunsRepo, UsageRepo
from recallops.db.session import tenant_session
from recallops.models import Claim, Recall, RecallProgram
from recallops.rules.schemas import RemedyRules
from recallops.settings import get_settings
from recallops.tenancy import FirmContext

Responses = dict[int | str, dict[str, Any]]
NOT_FOUND: Responses = {404: {"model": s.Problem, "description": "not found, or belongs to another firm"}}
AUTH: Responses = {401: {"model": s.Problem}, 403: {"model": s.Problem}}
LATER: Responses = {501: {"model": s.Problem, "description": "contract final, implementation in a later milestone"}}
IMAGE: Responses = {200: {"content": {"image/jpeg": {}}}}
PDF: Responses = {200: {"content": {"application/pdf": {}}}}

firm = APIRouter(prefix="/v1", tags=["firm"], responses=AUTH)
billing = APIRouter(prefix="/v1/billing", tags=["billing (component 3)"], responses={**AUTH, **LATER})
portal = APIRouter(prefix="/portal", tags=["portal"])
internal = APIRouter(prefix="/internal", tags=["internal"])
health = APIRouter(tags=["health"])


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
def create_program(body: s.ProgramCreate, ctx: AdminDep) -> s.Program:
    raise NotYetImplemented("POST /v1/programs", "M2")


@firm.patch(
    "/programs/{program_id}",
    response_model=s.Program,
    responses={**NOT_FOUND, **LATER},
    summary="Edit rules, threshold, or status",
)
def patch_program(program_id: UUID, body: s.ProgramPatch, ctx: AdminDep) -> s.Program:
    raise NotYetImplemented("PATCH /v1/programs/{id}", "M2")


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
def issue_links(program_id: UUID, body: s.IssueRequest, ctx: AdminDep) -> s.IssueResponse:
    raise NotYetImplemented("POST /v1/programs/{id}/claims/issue", "M2", "backend owner, tokens by login owner")


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
    return FileResponse(path, media_type="image/jpeg")


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
    return s.Trace(run=views.run_summary(run), caps=caps, steps=[views.trace_step(t) for t in runs.steps(run.id)])


@firm.post(
    "/claims/{claim_id}/actions",
    response_model=s.ClaimDetail,
    responses={**NOT_FOUND, **LATER},
    summary="Reviewer action: resume_override, request_info, mark_fraud (writes audit_log)",
)
def claim_action(claim_id: UUID, body: s.ClaimAction, ctx: FirmDep) -> s.ClaimDetail:
    raise NotYetImplemented("POST /v1/claims/{id}/actions", "M3")


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


def _portal_claim(token_ctx: Any) -> tuple[FirmContext, Claim]:
    verifier = build_claim_verifier()
    with Session(internal_engine()) as session:
        row = session.execute(select(Claim.firm_id).where(Claim.id == token_ctx.claim_id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found or expired")
    ctx = FirmContext(firm_id=row.firm_id, user_id="portal", role="reviewer")
    with tenant_session(ctx) as session:
        claim = ClaimsRepo(session, ctx).get(token_ctx.claim_id)
        if claim is None or claim.token_hash != verifier.token_hash(f"dev.{token_ctx.claim_id}"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found or expired")
        session.expunge(claim)
    return ctx, claim


@portal.get(
    "/{token}",
    response_model=s.PortalView,
    responses=NOT_FOUND,
    summary="Program public info, required steps, status, pending question",
)
def portal_view(token: str, token_ctx: ClaimTokenDep) -> s.PortalView:
    ctx, claim = _portal_claim(token_ctx)
    with tenant_session(ctx) as session:
        p = ProgramsRepo(session, ctx).get(claim.program_id)
        assert p is not None
        recall = session.get(Recall, p.recall_id) if p.recall_id else None
        remedy = RemedyRules.model_validate(p.remedy_rules or {})
        ident = p.identification_rules or {}
        required: list[Any] = ["label"]
        if remedy.destruction_instruction or any("destruction_photo" in o.proof_required for o in remedy.options):
            required.append("destruction")
        if any("receipt" in o.proof_required for o in remedy.options):
            required.append("receipt")
        decision = claim.decision or {}
        return s.PortalView(
            claim_id=claim.id,
            status=claim.status,
            program=s.PortalProgram(
                title=recall.title if recall else "Recall",
                models=ident.get("models", []),
                model_label_location=ident.get("model_label_location"),
                remedy_type=remedy.remedy_type,
                destruction_instruction=remedy.destruction_instruction,
                required_photos=required,
            ),
            pending_question=decision.get("question_for_claimant"),
            decision_reasons=decision.get("reasons", [])
            if claim.status in ("approved", "denied", "needs_info")
            else [],
        )


@portal.post(
    "/{token}/submit", response_model=s.PortalView, responses={**NOT_FOUND, **LATER}, summary="Submit claim fields"
)
def portal_submit(token: str, body: s.PortalSubmit, token_ctx: ClaimTokenDep) -> s.PortalView:
    raise NotYetImplemented("POST /portal/{token}/submit", "M2")


@portal.post(
    "/{token}/photos",
    response_model=s.Photo,
    responses={**NOT_FOUND, **LATER},
    summary="Upload a photo (multipart); sha256 and pHash computed server-side",
)
def portal_photo(
    token: str, token_ctx: ClaimTokenDep, kind: Annotated[str, Form()], file: Annotated[UploadFile, File()]
) -> s.Photo:
    raise NotYetImplemented("POST /portal/{token}/photos", "M2")


@portal.post(
    "/{token}/answer", response_model=s.PortalView, responses={**NOT_FOUND, **LATER}, summary="Reply to needs_info"
)
def portal_answer(token: str, body: s.PortalAnswer, token_ctx: ClaimTokenDep) -> s.PortalView:
    raise NotYetImplemented("POST /portal/{token}/answer", "M2")


@portal.post(
    "/{token}/payout",
    response_model=s.PortalView,
    responses={**NOT_FOUND, **LATER},
    summary="Payout destination (Connect onboarding handoff is component 3)",
)
def portal_payout(token: str, body: s.PortalPayout, token_ctx: ClaimTokenDep) -> s.PortalView:
    raise NotYetImplemented("POST /portal/{token}/payout", "M4", "backend owner with payments owner")


# Internal


@internal.post("/feeds/pull", status_code=202, responses=LATER, summary="Nightly feed pull (shared secret header)")
def feeds_pull() -> dict[str, str]:
    raise NotYetImplemented("POST /internal/feeds/pull", "M2")


@internal.post("/stripe/webhook", responses=LATER, summary="Stripe webhook (component 3 replaces this stub)")
def stripe_webhook() -> dict[str, str]:
    raise NotYetImplemented("POST /internal/stripe/webhook", "M3", "payments owner")


# Health


@health.get("/healthz", response_model=s.Health)
def healthz() -> s.Health:
    return s.Health(status="ok", database=True, worker_heartbeat_age_s=None)


@health.get("/readyz", response_model=s.Health, responses={503: {"model": s.Health}})
def readyz() -> s.Health:
    try:
        with internal_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        ok = True
    except Exception:  # noqa: BLE001 - readiness reports, never raises
        ok = False
    if not ok:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unreachable")
    return s.Health(status="ok", database=True, worker_heartbeat_age_s=None)
