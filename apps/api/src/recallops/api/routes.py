"""Tenant-scoped firm APIs, single-claim portal routes, and signed integration webhooks."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from recallops import portal as portal_service
from recallops import programs as program_service
from recallops.api import schemas as s
from recallops.api import views
from recallops.api.deps import PublicDb, TenantDb
from recallops.api.problems import PaymentRequired
from recallops.auth.deps import AdminDep, ClaimTokenDep, FirmDep, build_claim_verifier
from recallops.billing.interface import BillingGate
from recallops.billing.policy import PlanBilling
from recallops.billing.service import billing_summary
from recallops.claims.review import ReviewConflict, submit_action
from recallops.db.engine import app_engine
from recallops.db.repos import ClaimsRepo, ProgramsRepo, RunsRepo, UsageRepo
from recallops.db.session import tenant_session
from recallops.fraud.view import FraudGraphView
from recallops.logs import get_logger, log_event
from recallops.models import Claim, Recall, RecallProgram, WorkerHeartbeat
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

firm = APIRouter(prefix="/v1", tags=["firm"], responses=AUTH)
billing = APIRouter(
    prefix="/v1/billing", tags=["billing (component 3)"], responses={**AUTH, **CONFLICT, 503: {"model": s.Problem}}
)
portal = APIRouter(prefix="/portal", tags=["portal"])
internal = APIRouter(prefix="/internal", tags=["internal"])
health = APIRouter(tags=["health"])


def billing_gate(session: Session | None = None) -> BillingGate:
    return PlanBilling(app_engine(), get_settings(), session)


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
    responses={**NOT_FOUND, **PAYMENT},
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
            billing_gate(db),
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
    responses={**NOT_FOUND, **CONFLICT},
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

    def issue(n: int) -> list[tuple[UUID, str]]:
        return program_service.issue_links(db, ctx, get_settings(), build_claim_verifier(), program, n)

    try:
        return s.CampaignCounts(counts=campaigns.draft(db, ctx, program, list(dict.fromkeys(body.channels)), issue))
    except program_service.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@firm.post(
    "/programs/{program_id}/campaign/send",
    response_model=s.CampaignCounts,
    responses=NOT_FOUND,
    summary="Mark drafts sent (simulated: no message leaves the system); feeds the MPR notified_* boxes",
)
def campaign_send(program_id: UUID, body: s.CampaignSendRequest, ctx: AdminDep, db: TenantDb) -> s.CampaignCounts:
    from recallops import campaigns

    _open_program(db, ctx, program_id)
    return s.CampaignCounts(counts=campaigns.send(db, ctx, program_id, body.channel))


@firm.get(
    "/programs/{program_id}/campaign",
    response_model=s.CampaignSummary,
    responses=NOT_FOUND,
    summary="Campaign status: registrations, matches, notices by channel and status, three sample drafts",
)
def campaign_summary(program_id: UUID, ctx: FirmDep, db: TenantDb) -> s.CampaignSummary:
    from recallops import campaigns

    _open_program(db, ctx, program_id)
    return s.CampaignSummary(**campaigns.summary(db, ctx, program_id))


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


@firm.get("/usage", response_model=s.Usage, summary="Current period meters (read by the billing UI)")
def get_usage(ctx: FirmDep, db: TenantDb) -> s.Usage:
    period = datetime.now(UTC).date().replace(day=1)
    meter = UsageRepo(db, ctx).for_period(period)
    open_programs = sum(1 for p in ProgramsRepo(db, ctx).list() if p.status == "open")
    return s.Usage(
        period_start=period,
        claims_adjudicated=meter.claims_adjudicated if meter else 0,
        model_cost_usd=f"{meter.model_cost_usd:.6f}" if meter else "0.000000",
        open_programs=open_programs,
        **billing_summary(db, ctx, get_settings()),
    )


# Billing


@billing.post("/checkout", response_model=s.CheckoutSession, summary="Stripe Checkout session for the growth plan")
def checkout(ctx: AdminDep) -> s.CheckoutSession:
    from recallops.billing.service import checkout as create_checkout

    try:
        return s.CheckoutSession(url=create_checkout(app_engine(), get_settings(), ctx))
    except SQLAlchemyError as exc:
        raise HTTPException(503, "payment persistence unavailable; reconcile the operation before retrying") from exc


@billing.post("/portal", response_model=s.CheckoutSession, summary="Stripe Customer Portal session")
def customer_portal(ctx: AdminDep) -> s.CheckoutSession:
    from recallops.billing.service import portal as create_portal

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
    responses={**NOT_FOUND, **PAYMENT, **CONFLICT, 503: {"model": s.Problem}},
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
    responses={400: {"model": s.Problem}, 503: {"model": s.Problem}},
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
