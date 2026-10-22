"""Programs: open one from a recall (or a manual notice), edit it, and issue claim links.

Rule extraction follows the extraction verdict (decisions log 2026-10-18): the regex baseline is primary; the
model-based extractor runs only as a fallback when the baseline finds no identifiers (no models, serial ranges, or
date codes), and only when a model client can be built. Every write runs in the caller's tenant session (RLS);
every change writes audit_log (04-login.md section 6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from recallops import access
from recallops.auth.provider import ClaimTokenVerifier
from recallops.billing.interface import BillingGate
from recallops.db.engine import app_engine
from recallops.llm.client import LiveCallsDisabled, ModelCallFailed, ReplayMiss, build_client
from recallops.llm.meter import BudgetExhausted
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, Firm, Recall, RecallProgram
from recallops.retrieval.documents import program_documents
from recallops.retrieval.embed import build_embedder
from recallops.retrieval.search import index_program
from recallops.rules import baseline, graph
from recallops.rules.schemas import ExtractedRules, IdentificationRules, RemedyRules
from recallops.settings import Settings
from recallops.tenancy import FirmContext
from recallops.text import clean
from recallops.tracing.writer import DbTrace

log = get_logger("programs")


class Conflict(ValueError):
    pass


class NotEntitled(PermissionError):
    def __init__(self, action: str, reason: str | None) -> None:
        super().__init__(action)
        self.action, self.reason = action, reason


@dataclass(frozen=True)
class Extraction:
    rules: ExtractedRules
    extractor: str


def has_identifiers(rules: ExtractedRules) -> bool:
    i = rules.identification
    return bool(i.models or i.serial_ranges or i.date_codes)


def extract_rules(source_text: str, settings: Settings, ctx: FirmContext) -> Extraction:
    rules = baseline.extract(source_text)
    if has_identifiers(rules):
        return Extraction(rules, f"baseline {baseline.EXTRACTOR_VERSION}")
    try:
        llm = build_client(settings, ledger_engine=app_engine())
    except LiveCallsDisabled:
        return Extraction(rules, f"baseline {baseline.EXTRACTOR_VERSION} (fallback unavailable)")
    trace = DbTrace(app_engine(), "extract_rules", firm_id=ctx.firm_id, model_id=llm.model_id, tenant=True)
    try:
        result = graph.extract(source_text, llm, trace)
    except (ReplayMiss, ModelCallFailed, BudgetExhausted) as exc:
        log_event(log, "extraction_fallback_failed", firm_id=str(ctx.firm_id), error=str(exc)[:200])
        return Extraction(rules, f"baseline {baseline.EXTRACTOR_VERSION} (fallback failed)")
    if result.rules is not None and has_identifiers(result.rules):
        return Extraction(result.rules, f"graph {graph.EXTRACTOR_VERSION} (fallback)")
    return Extraction(rules, f"baseline {baseline.EXTRACTOR_VERSION}")


def manual_source_text(title: str, description: str) -> str:
    return f"TITLE:\n{clean(title)}\n\nDESCRIPTION:\n{clean(description)}"


def _audit(
    session: Session,
    ctx: FirmContext,
    action: str,
    target: str,
    before: Any,
    after: Any,
    target_type: str = "program",
) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type=target_type,
            target_id=target,
            before=before,
            after=after,
        )
    )


def reindex(
    session: Session, ctx: FirmContext, program: RecallProgram, title: str, source_text: str, settings: Settings
) -> int:
    rules = ExtractedRules(
        identification=IdentificationRules.model_validate(program.identification_rules or {}),
        remedy=RemedyRules.model_validate(program.remedy_rules or {}),
    )
    docs = program_documents(title, source_text, rules, program.value_threshold_cents)
    embedder = build_embedder(settings.embedding_model_id, settings.embedding_dim)
    return index_program(session, ctx.firm_id, program.id, docs, embedder)


def create_program(
    session: Session,
    ctx: FirmContext,
    settings: Settings,
    billing: BillingGate,
    recall_id: UUID | None,
    manual: dict[str, Any] | None,
    value_threshold_cents: int | None,
    access_id: UUID | None = None,
    baseline_only: bool = False,
) -> tuple[RecallProgram, str, str]:
    """A CPSC recall needs this firm's approved request for it; a typed notice needs the approved typed request it
    names (access_id), used by one program only (company verification, decisions log 2026-10-22). A company runs
    one live product per recall, and on Free one product in all (plan v3). `baseline_only` reads the rules with the
    regex extractor alone: the operator's approval never waits on, or pays for, a model call."""
    if recall_id is not None:
        recall = session.get(Recall, recall_id)
        if recall is None:
            raise LookupError("recall not found")
        title, source_text = recall.title, recall.source_text
        access_id = None  # a CPSC program is matched to its approval by recall id (access.program_approved)
    elif manual is not None:
        title = clean(manual["title"])
        source_text = manual_source_text(manual["title"], manual["description"])
    else:
        raise ValueError("give recall_id or manual")
    if access.required(settings):
        probe = RecallProgram(firm_id=ctx.firm_id, recall_id=recall_id, access_id=access_id)
        if not access.program_approved(session, probe, settings):
            raise access.AccessDenied("request access to this recall and wait for the operator's approval")
    if access_id is not None and session.scalar(select(RecallProgram.id).where(RecallProgram.access_id == access_id)):
        raise Conflict("a program already runs this typed notice")
    if recall_id is not None and live_program_for(session, ctx, recall_id) is not None:
        raise Conflict("this recall already has a product")
    session.execute(select(Firm.id).where(Firm.id == ctx.firm_id).with_for_update())
    entitlement = billing.check_entitlement(ctx, "create_program")
    if not entitlement.allowed:
        raise NotEntitled("create_program", entitlement.reason)
    if baseline_only:
        extraction = Extraction(baseline.extract(source_text), f"baseline {baseline.EXTRACTOR_VERSION}")
    else:
        extraction = extract_rules(source_text, settings, ctx)
    rules_json = json.loads(extraction.rules.model_dump_json())
    program = RecallProgram(
        id=uuid4(),
        firm_id=ctx.firm_id,
        recall_id=recall_id,
        access_id=access_id,
        status="draft",
        identification_rules=rules_json["identification"],
        remedy_rules=rules_json["remedy"],
        value_threshold_cents=value_threshold_cents
        if value_threshold_cents is not None
        else settings.default_value_threshold_cents,
    )
    session.add(program)
    session.flush()
    chunks = reindex(session, ctx, program, title, source_text, settings)
    _audit(
        session,
        ctx,
        "program_created",
        str(program.id),
        None,
        {"recall_id": str(recall_id) if recall_id else None, "extractor": extraction.extractor, "chunks": chunks},
    )
    log_event(
        log, "program_created", firm_id=str(ctx.firm_id), program_id=str(program.id), extractor=extraction.extractor
    )
    return program, title, source_text


def program_source(session: Session, program: RecallProgram) -> tuple[str, str]:
    if program.recall_id:
        recall = session.get(Recall, program.recall_id)
        if recall is not None:
            return recall.title, recall.source_text
    from recallops.models import Document

    notice = session.scalars(
        select(Document).where(Document.program_id == program.id, Document.kind == "notice")
    ).first()
    return (notice.title, notice.text) if notice else ("Manual recall", "")


def patch_program(
    session: Session,
    ctx: FirmContext,
    settings: Settings,
    billing: BillingGate,
    program: RecallProgram,
    changes: dict[str, Any],
) -> RecallProgram:
    before = {
        "status": program.status,
        "value_threshold_cents": program.value_threshold_cents,
        "rules_sha256": _rules_hash(program),
    }
    rules_changed = False
    if "identification_rules" in changes and changes["identification_rules"] is not None:
        program.identification_rules = changes["identification_rules"]
        rules_changed = True
    if "remedy_rules" in changes and changes["remedy_rules"] is not None:
        program.remedy_rules = changes["remedy_rules"]
        rules_changed = True
    if changes.get("value_threshold_cents") is not None:
        program.value_threshold_cents = int(changes["value_threshold_cents"])
        rules_changed = True
    status = changes.get("status")
    if status and status != program.status:
        if program.status == "closed":
            raise Conflict("a closed program cannot be reopened; open a new program")
        if status == "open":
            access.require_program(session, program, settings)
            if needs_refund_amount(program):
                raise Conflict(MISSING_REFUND_AMOUNT)
            session.execute(select(Firm.id).where(Firm.id == ctx.firm_id).with_for_update())
            entitlement = billing.check_entitlement(ctx, "open_program")
            if not entitlement.allowed:
                raise NotEntitled("open_program", entitlement.reason)
            program.opened_at = datetime.now(UTC)
        if status == "closed":
            program.closed_at = datetime.now(UTC)
        program.status = status
    if before["status"] != program.status:
        from recallops.billing.service import mark_quantity_dirty

        mark_quantity_dirty(session, ctx)
    session.flush()
    if rules_changed:
        title, source_text = program_source(session, program)
        reindex(session, ctx, program, title, source_text, settings)
    after = {
        "status": program.status,
        "value_threshold_cents": program.value_threshold_cents,
        "rules_sha256": _rules_hash(program),
    }
    action = "program_status_changed" if before["status"] != after["status"] else "program_rules_edited"
    _audit(session, ctx, action, str(program.id), before, after)
    return program


def _rules_hash(program: RecallProgram) -> str:
    body = json.dumps([program.identification_rules, program.remedy_rules], sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


REISSUE_REFUSED = ("paid", "closed")


def reissue_link(
    session: Session, ctx: FirmContext, settings: Settings, verifier: ClaimTokenVerifier, claim_id: UUID
) -> str:
    """A fresh link for one existing claim; the old link stops working at once (claims.token_hash rotates).

    Signed links expire after CLAIM_TOKEN_TTL_DAYS and are not stored, so this is how a firm reaches a claimant whose
    link is lost, expired, or leaked (it doubles as revocation), and how a campaign sender gets the link for a
    draft's {claim_link}. Refused on a closed program and on a paid or closed claim (backend review of PR 8).
    A signed link is a function of the firm, the claim, and its expiry second, so a reissue within the second of
    the last issue, or any reissue under the dev verifier, returns the same link; the audit row says which."""
    claim = session.scalars(
        select(Claim).where(Claim.firm_id == ctx.firm_id, Claim.id == claim_id).with_for_update()
    ).first()
    if claim is None:
        raise LookupError("claim not found")
    program = session.get(RecallProgram, claim.program_id)
    if program is None or program.status == "closed":
        raise Conflict("the program is closed: its claim links stay dead")
    access.require_program(session, program, settings)
    if claim.status in REISSUE_REFUSED:
        raise Conflict(f"the claim is {claim.status}: there is nothing left for the claimant to do")
    token = verifier.issue(ctx.firm_id, claim.id)
    new_hash = verifier.token_hash(token)
    rotated = new_hash != claim.token_hash
    claim.token_hash = new_hash
    session.flush()
    # Filed under the claim, so the claim's own audit trail shows the reissue (UI owner's note on PR 9).
    after = {"status": claim.status, "rotated": rotated}
    _audit(session, ctx, "claim_link_reissued", str(claim.id), None, after, target_type="claim")
    log_event(log, "claim_link_reissued", firm_id=str(ctx.firm_id), claim_id=str(claim.id), rotated=rotated)
    return f"{settings.portal_base_url.rstrip('/')}/{token}"


MISSING_REFUND_AMOUNT = "set the refund amount (the highest price) in Rules and settings before opening"


def needs_refund_amount(program: RecallProgram) -> bool:
    """True when the product pays refunds but its rules carry no price, so an approved claim would have no amount to
    pay (portal.submit takes the refund from the highest price)."""
    ident = IdentificationRules.model_validate(program.identification_rules or {})
    remedy = RemedyRules.model_validate(program.remedy_rules or {})
    kind = remedy.options[0].type if remedy.options else remedy.remedy_type
    refund = kind == "refund" or kind == "multiple"
    return refund and ident.price_range_cents is None


def live_program_for(session: Session, ctx: FirmContext, recall_id: UUID) -> RecallProgram | None:
    return session.scalars(
        select(RecallProgram)
        .where(
            RecallProgram.firm_id == ctx.firm_id,
            RecallProgram.recall_id == recall_id,
            RecallProgram.status != "closed",
        )
        .with_for_update()
    ).first()


def _program_for_access(session: Session, ctx: FirmContext, row: Any) -> RecallProgram | None:
    if row.recall_id is not None:
        return live_program_for(session, ctx, row.recall_id)
    return session.scalars(
        select(RecallProgram)
        .where(
            RecallProgram.firm_id == ctx.firm_id, RecallProgram.access_id == row.id, RecallProgram.status != "closed"
        )
        .with_for_update()
    ).first()


def start_product(
    session: Session, ctx: FirmContext, settings: Settings, billing: BillingGate, access_id: UUID
) -> dict[str, Any]:
    """After the operator approves a request (owner decision 2026-10-22): the company's product is created and opened
    at once. Runs in the decision's own transaction (under the company's firm context), so it sees the approval; the
    caller wraps it in a savepoint, so a product that cannot be made never undoes the decision. Reuses the live
    product for the recall when one exists (a revoke, then a new request). Returns the outcome for the operator."""
    from recallops.models import RecallAccess

    row = session.get(RecallAccess, access_id)
    if row is None or row.firm_id != ctx.firm_id or row.status != "approved":
        return {"id": None, "status": None, "error": "not_approved"}
    program = _program_for_access(session, ctx, row)
    if program is None:
        manual = (
            None
            if row.recall_id is not None
            else {"title": row.typed_title or "Typed notice", "description": row.typed_notice or row.typed_title or ""}
        )
        try:
            program, _, _ = create_program(
                session, ctx, settings, billing, row.recall_id, manual, None, access_id=row.id, baseline_only=True
            )
        except NotEntitled:
            log_event(log, "product_not_created", firm_id=str(ctx.firm_id), reason="over_plan")
            return {"id": None, "status": None, "error": "over_plan"}
    elif program.status == "open":  # approved again after a revoke: the same product and link take claims again
        from recallops.billing.service import mark_quantity_dirty

        mark_quantity_dirty(session, ctx)
    if program.status == "draft":
        try:
            program = patch_program(session, ctx, settings, billing, program, {"status": "open"})
        except NotEntitled:
            return {"id": program.id, "status": "draft", "error": "over_plan"}
        except Conflict as exc:
            error = "missing_refund_amount" if str(exc) == MISSING_REFUND_AMOUNT else "conflict"
            return {"id": program.id, "status": "draft", "error": error}
    log_event(log, "product_started", firm_id=str(ctx.firm_id), program_id=str(program.id), status=program.status)
    return {"id": program.id, "status": program.status, "error": None}


def stop_product(session: Session, ctx: FirmContext, settings: Settings, billing: BillingGate, access_id: UUID) -> None:
    """The operator revoked a company's access. The product is left as it is: every consumer checks the approval (its
    claim link and claimants' links answer 404, the worker and the payment jobs leave it alone, and it no longer
    counts toward the plan or the bill), so only the billed quantity changes here. Closing it would be final, and a
    re-approval would then make a second product with a new link and strand the first one's claimants (plan v3
    review); instead start_product finds it again and its link works as before."""
    from recallops.billing.service import mark_quantity_dirty
    from recallops.models import RecallAccess

    row = session.get(RecallAccess, access_id)
    if row is None or row.firm_id != ctx.firm_id:
        return
    program = _program_for_access(session, ctx, row)
    if program is not None:
        mark_quantity_dirty(session, ctx)
        log_event(log, "product_paused", firm_id=str(ctx.firm_id), program_id=str(program.id))


def takes_new_claims(session: Session, program: RecallProgram, settings: Settings) -> bool:
    """Whether the product's public claim link accepts new claims: open, approved by the operator, and within the
    company's plan. A company that fell back to Free keeps only its oldest open product taking new claims; the others
    pause (claims already filed carry on)."""
    if program.status != "open" or not access.program_approved(session, program, settings):
        return False
    firm = session.get(Firm, program.firm_id)
    if firm is None:
        return False
    from recallops.billing.policy import effective_plan

    if effective_plan(session, firm) != "free":
        return True
    oldest = session.scalars(
        select(RecallProgram.id)
        .where(RecallProgram.firm_id == program.firm_id, RecallProgram.status == "open")
        .order_by(RecallProgram.opened_at.asc().nulls_last(), RecallProgram.created_at.asc())
        .limit(settings.free_active_products)
    ).all()
    return program.id in oldest


def issue_links(
    session: Session, ctx: FirmContext, settings: Settings, verifier: ClaimTokenVerifier, program: RecallProgram, n: int
) -> list[tuple[UUID, str]]:
    if program.status != "open":
        raise Conflict("open the program before issuing claim links")
    access.require_program(session, program, settings)
    links: list[tuple[UUID, str]] = []
    for _ in range(n):
        claim_id = uuid4()
        token = verifier.issue(ctx.firm_id, claim_id)
        session.add(
            Claim(
                id=claim_id,
                firm_id=ctx.firm_id,
                program_id=program.id,
                token_hash=verifier.token_hash(token),
                status="draft",
                claimant={},
            )
        )
        links.append((claim_id, f"{settings.portal_base_url.rstrip('/')}/{token}"))
    session.flush()
    _audit(session, ctx, "token_batch_issued", str(program.id), None, {"n": n})
    log_event(log, "claim_links_issued", firm_id=str(ctx.firm_id), program_id=str(program.id), n=n)
    return links
