"""The consumer claim portal (01-backend.md section 9; invariant 10): one claim per link, no accounts.

The token names a claim; the claim's firm is resolved once, then every read and write runs in a tenant session for
that firm (RLS). A link stops working when its program is closed. Submitting twice changes nothing the second time.
Uploads are size- and type-checked, decoded with Pillow before they are stored, and stored under a path built
only from ids (never from a client-supplied name), with sha256 and pHash computed on the stored bytes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from recallops.auth.provider import ClaimTokenVerifier
from recallops.claims.tools.checks import destruction_required, receipt_required
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Claim, ClaimPhoto, Recall, RecallProgram
from recallops.photos import InvalidPhoto, inspect
from recallops.rules.schemas import IdentificationRules, RemedyRules

log = get_logger("portal")

PHOTO_KINDS = ("label", "destruction", "receipt", "other")
CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
EDITABLE = ("draft", "needs_info")


class LinkInvalid(LookupError):
    """Unknown token, wrong hash, or the program is closed: always a 404 (never reveal which)."""


class NotEditable(ValueError):
    pass


class PhotoRejected(ValueError):
    pass


@dataclass(frozen=True)
class PortalClaim:
    claim: Claim
    program: RecallProgram
    title: str


def resolve(session: Session, verifier: ClaimTokenVerifier, token: str, claim_id: UUID) -> PortalClaim:
    """session is already a tenant session for the claim's firm."""
    claim = session.get(Claim, claim_id)
    if claim is None or claim.token_hash != verifier.token_hash(token):
        raise LinkInvalid()
    program = session.scalars(
        select(RecallProgram).where(RecallProgram.id == claim.program_id, RecallProgram.firm_id == claim.firm_id)
    ).one()
    if program.status == "closed":
        raise LinkInvalid()
    recall = session.get(Recall, program.recall_id) if program.recall_id else None
    return PortalClaim(claim, program, recall.title if recall else "Recall")


def required_photos(program: RecallProgram) -> list[str]:
    remedy = RemedyRules.model_validate(program.remedy_rules or {})
    out = ["label"]
    if destruction_required(remedy):
        out.append("destruction")
    if receipt_required(remedy):
        out.append("receipt")
    return out


def view(session: Session, pc: PortalClaim) -> dict[str, Any]:
    claim, program = pc.claim, pc.program
    remedy = RemedyRules.model_validate(program.remedy_rules or {})
    ident = IdentificationRules.model_validate(program.identification_rules or {})
    decision = claim.decision or {}
    kinds = sorted(
        set(
            session.scalars(
                select(ClaimPhoto.kind).where(ClaimPhoto.claim_id == claim.id, ClaimPhoto.firm_id == claim.firm_id)
            )
        )
    )
    messages = [
        {"sender": m.get("from", "firm"), "text": m.get("text", ""), "at": m.get("at")} for m in (claim.messages or [])
    ]
    from recallops.models import Payout

    payout_status = session.scalar(
        select(Payout.status).where(Payout.claim_id == claim.id, Payout.firm_id == claim.firm_id)
    )
    return {
        "claim_id": claim.id,
        "status": claim.status,
        "program": {
            "title": pc.title,
            "models": ident.models,
            "model_label_location": ident.model_label_location,
            "remedy_type": remedy.remedy_type,
            "destruction_instruction": remedy.destruction_instruction,
            "required_photos": required_photos(program),
        },
        "pending_question": _safe_question(decision.get("question_for_claimant"))
        if claim.status == "needs_info"
        else None,
        "decision_reasons": claimant_reasons(claim.status, decision),
        "photos_uploaded": kinds,
        "messages": messages,
        "payout_status": payout_status,
    }


CLAIMANT_REASON = {
    "R:models": "The model number shown is not part of this recall.",
    "R:serials": "The serial number is outside the recalled range.",
    "R:date_codes": "The date code is outside the recalled range.",
    "R:sold_window": "The purchase date is before the recalled products were sold.",
}
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def claimant_reasons(status: str, decision: dict[str, Any]) -> list[str]:
    """A fixed, claimant-safe wording from the outcome and the cited rule ids: never model prose, reviewer notes,
    or anything about other claims (red-team finding, decisions log 2026-10-18)."""
    if status == "approved":
        return ["Your claim was approved."]
    if status == "needs_info":
        return ["More information is needed to finish your claim."]
    if status == "denied":
        if decision.get("review_action") == "mark_fraud":
            return ["Your claim could not be approved. Contact the firm for details."]
        mapped = [CLAIMANT_REASON[c] for c in decision.get("cited_rules", []) if c in CLAIMANT_REASON]
        return sorted(set(mapped)) or [
            "Your claim does not meet the recall's requirements. Contact the firm for details."
        ]
    return []


def _safe_question(question: Any) -> str | None:
    if not question:
        return None
    return _UUID.sub("another claim", str(question))[:500]


def submit(session: Session, pc: PortalClaim, fields: dict[str, Any]) -> bool:
    """True when this call submitted the claim; False when it was already submitted (idempotent replay)."""
    claim = pc.claim
    if claim.status != "draft":
        return False
    ident = IdentificationRules.model_validate(pc.program.identification_rules or {})
    remedy = RemedyRules.model_validate(pc.program.remedy_rules or {})
    kind = (
        remedy.options[0].type
        if remedy.options
        else (remedy.remedy_type if remedy.remedy_type != "multiple" else "refund")
    )
    refund = ident.price_range_cents.max_cents if kind == "refund" and ident.price_range_cents else None
    claimant = {
        k: fields.get(k)
        for k in ("name", "email", "phone", "address_line1", "city", "state", "zip")
        if fields.get(k) is not None
    }
    done = session.execute(
        update(Claim)
        .where(Claim.id == claim.id, Claim.firm_id == claim.firm_id, Claim.status == "draft")
        .values(
            status="submitted",
            submitted_at=datetime.now(UTC),
            claimant=claimant,
            model_claimed=fields.get("model_claimed"),
            serial_claimed=fields.get("serial_claimed"),
            date_code_claimed=fields.get("date_code_claimed"),
            retailer_claimed=fields.get("retailer_claimed"),
            purchase_date=fields.get("purchase_date"),
            quantity=fields.get("quantity", 1),
            remedy=kind if kind in ("refund", "repair", "replacement") else None,
            refund_cents=refund,
        )
        .returning(Claim.id)
    ).first()
    if done is None:
        return False
    session.add(
        AuditLog(
            firm_id=claim.firm_id,
            actor="claimant",
            action="claim_submitted",
            target_type="claim",
            target_id=str(claim.id),
            before={"status": "draft"},
            after={"status": "submitted"},
        )
    )
    log_event(log, "portal_claim_submitted", firm_id=str(claim.firm_id), claim_id=str(claim.id))
    return True


def add_photo(
    session: Session,
    pc: PortalClaim,
    storage_dir: Path,
    kind: str,
    content_type: str | None,
    data: bytes,
    max_bytes: int,
    max_pixels: int,
) -> tuple[ClaimPhoto, list[Path]]:
    """Returns the new photo and the files it replaced; the caller deletes those only after the commit."""
    claim = pc.claim
    if claim.status not in EDITABLE:
        raise NotEditable(f"photos can be added while the claim is {' or '.join(EDITABLE)}")
    if kind not in PHOTO_KINDS:
        raise PhotoRejected(f"kind must be one of {', '.join(PHOTO_KINDS)}")
    if len(data) > max_bytes:
        raise PhotoRejected(f"photo is larger than {max_bytes // 1_000_000} MB")
    ext = CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        raise PhotoRejected("photo must be JPEG or PNG")
    try:
        facts = inspect(data, max_pixels)
    except InvalidPhoto as exc:
        raise PhotoRejected(str(exc)) from exc
    if facts.exif.get("format") not in ("JPEG", "PNG"):
        raise PhotoRejected("photo must be JPEG or PNG")
    relative = f"{claim.firm_id}/{claim.id}/{kind}-{uuid4().hex}.{ext}"
    target = storage_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    old = list(
        session.scalars(
            select(ClaimPhoto).where(
                ClaimPhoto.claim_id == claim.id, ClaimPhoto.firm_id == claim.firm_id, ClaimPhoto.kind == kind
            )
        )
    )
    session.execute(delete(ClaimPhoto).where(ClaimPhoto.id.in_([p.id for p in old])))
    photo = ClaimPhoto(
        firm_id=claim.firm_id,
        claim_id=claim.id,
        kind=kind,
        storage_path=relative,
        sha256=facts.sha256,
        phash=facts.phash,
        exif=facts.exif,
    )
    session.add(photo)
    session.flush()
    log_event(
        log, "portal_photo_stored", firm_id=str(claim.firm_id), claim_id=str(claim.id), kind=kind, bytes=len(data)
    )
    return photo, [storage_dir / p.storage_path for p in old]


def answer(session: Session, pc: PortalClaim, message: str) -> None:
    """A reply to needs_info sends the claim back for a fresh adjudication run."""
    claim = pc.claim
    if claim.status != "needs_info":
        raise NotEditable("the claim is not waiting for an answer")
    entry = {"from": "claimant", "text": message[:2000], "at": datetime.now(UTC).isoformat()}
    session.execute(
        text(
            "UPDATE claims SET messages = messages || CAST(:m AS jsonb), status = 'submitted', "
            "submitted_at = now(), decision = NULL, decided_at = NULL, decided_by = NULL "
            "WHERE id = :c AND firm_id = :f AND status = 'needs_info'"
        ),
        {"m": json.dumps([entry]), "c": claim.id, "f": claim.firm_id},
    )
    session.add(
        AuditLog(
            firm_id=claim.firm_id,
            actor="claimant",
            action="claim_answered",
            target_type="claim",
            target_id=str(claim.id),
            before={"status": "needs_info"},
            after={"status": "submitted"},
        )
    )
