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

from recallops.auth.provider import ClaimTokenVerifier
from recallops.billing.interface import BillingGate
from recallops.db.engine import app_engine
from recallops.llm.client import LiveCallsDisabled, ModelCallFailed, ReplayMiss, build_client
from recallops.llm.meter import BudgetExhausted
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, Recall, RecallProgram
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


def _audit(session: Session, ctx: FirmContext, action: str, target: str, before: Any, after: Any) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="program",
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
) -> tuple[RecallProgram, str, str]:
    entitlement = billing.check_entitlement(ctx, "open_program")
    if not entitlement.allowed:
        raise NotEntitled("open_program", entitlement.reason)
    if recall_id is not None:
        recall = session.get(Recall, recall_id)
        if recall is None:
            raise LookupError("recall not found")
        title, source_text = recall.title, recall.source_text
    elif manual is not None:
        title = clean(manual["title"])
        source_text = manual_source_text(manual["title"], manual["description"])
    else:
        raise ValueError("give recall_id or manual")
    extraction = extract_rules(source_text, settings, ctx)
    rules_json = json.loads(extraction.rules.model_dump_json())
    program = RecallProgram(
        id=uuid4(),
        firm_id=ctx.firm_id,
        recall_id=recall_id,
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
            entitlement = billing.check_entitlement(ctx, "open_program")
            if not entitlement.allowed:
                raise NotEntitled("open_program", entitlement.reason)
            program.opened_at = datetime.now(UTC)
        if status == "closed":
            program.closed_at = datetime.now(UTC)
        program.status = status
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


def issue_links(
    session: Session, ctx: FirmContext, settings: Settings, verifier: ClaimTokenVerifier, program: RecallProgram, n: int
) -> list[tuple[UUID, str]]:
    if program.status != "open":
        raise Conflict("open the program before issuing claim links")
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
