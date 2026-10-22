"""One public claim link per product (owner decision 2026-10-22; invariant 10 amended).

Anyone with the link sees the product's public facts and can start a claim; starting one creates a draft claim with
its own signed, expiring, single-claim link (invariant 10 still holds after intake), which the claimant keeps. A link
works only while its product takes new claims (open, approved, within the plan) and only at the product's current
version: replacing the link stops the old one. The limits on starting claims live in api/guards.py (per client and
per product); what a started claim may cost lives in the worker.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from recallops import programs
from recallops.auth.intake_tokens import IntakeTarget, build_intake_tokens
from recallops.auth.provider import AuthError, ClaimTokenVerifier
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, Firm, Recall, RecallProgram
from recallops.portal import required_photos
from recallops.rules.schemas import IdentificationRules, RemedyRules
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("intake")


class LinkInvalid(LookupError):
    """Not a link, a replaced link, or a product that takes no new claims: the API answers 404 either way."""


def link(settings: Settings, program: RecallProgram) -> str:
    token = build_intake_tokens(settings).issue(program.firm_id, program.id, program.intake_version)
    return f"{settings.intake_base()}/{token}"


def target(settings: Settings, token: str) -> IntakeTarget:
    """Check the signature before any database work; the firm inside it picks the tenant session."""
    try:
        return build_intake_tokens(settings).verify(token)
    except AuthError as exc:
        raise LinkInvalid() from exc


def resolve(session: Session, settings: Settings, found: IntakeTarget) -> RecallProgram:
    """session is a tenant session for found.firm_id."""
    program = session.get(RecallProgram, found.program_id)
    if program is None or program.firm_id != found.firm_id or program.intake_version != found.version:
        raise LinkInvalid()
    if not programs.takes_new_claims(session, program, settings):
        raise LinkInvalid()
    return program


def view(session: Session, program: RecallProgram) -> dict[str, Any]:
    """What a claimant sees before starting: the company, the product, and what the claim will ask for."""
    ident = IdentificationRules.model_validate(program.identification_rules or {})
    remedy = RemedyRules.model_validate(program.remedy_rules or {})
    recall = session.get(Recall, program.recall_id) if program.recall_id else None
    firm = session.get(Firm, program.firm_id)
    return {
        "company_name": firm.name if firm else "",
        "title": recall.title if recall else programs.program_source(session, program)[0],
        "recall_number": recall.source_id if recall else None,
        "models": ident.models,
        "model_label_location": ident.model_label_location,
        "sold_at": ident.sold_at,
        "remedy_type": remedy.remedy_type,
        "destruction_instruction": remedy.destruction_instruction,
        "required_photos": required_photos(program),
        "contact": remedy.contact,
    }


def start(
    session: Session, ctx: FirmContext, settings: Settings, verifier: ClaimTokenVerifier, program: RecallProgram
) -> tuple[UUID, str, str]:
    """A new draft claim and its private link. The claimant fills it in through the usual portal steps; a draft that
    is never submitted is deleted after DRAFT_TTL_HOURS with its photos (claims/sweeper.py)."""
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
    session.flush()
    log_event(log, "intake_claim_started", firm_id=str(ctx.firm_id), program_id=str(program.id), claim_id=str(claim_id))
    return claim_id, token, f"{settings.portal_base_url.rstrip('/')}/{token}"


def replace_link(session: Session, ctx: FirmContext, program_id: UUID) -> RecallProgram:
    """A new link for the product; the old one stops at once (posters with it need reprinting). Claims already started
    keep their own private links."""
    program = session.scalars(
        select(RecallProgram)
        .where(RecallProgram.id == program_id, RecallProgram.firm_id == ctx.firm_id)
        .with_for_update()
    ).first()
    if program is None:
        raise LookupError("program not found")
    if program.status == "closed":
        raise programs.Conflict("the program is closed: it has no claim link")
    before = program.intake_version
    program.intake_version = before + 1
    session.flush()
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action="intake_link_replaced",
            target_type="program",
            target_id=str(program.id),
            before={"version": before},
            after={"version": program.intake_version},
        )
    )
    log_event(log, "intake_link_replaced", firm_id=str(ctx.firm_id), program_id=str(program.id))
    return program
