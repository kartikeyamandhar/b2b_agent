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
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from recallops.auth.provider import ClaimTokenVerifier
from recallops.claims.tools.checks import destruction_required, receipt_required
from recallops.logs import get_logger, log_event
from recallops.models import AgentRun, AgentStep, AuditLog, Claim, ClaimPhoto, Recall, RecallProgram
from recallops.photos import InvalidPhoto, inspect
from recallops.rules.schemas import IdentificationRules, RemedyRules
from recallops.storage import Storage

log = get_logger("portal")

PHOTO_KINDS = ("label", "destruction", "receipt", "other")
CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
MEDIA_TYPES = {ext: media for media, ext in CONTENT_TYPES.items()}
EDITABLE = ("draft", "needs_info")
# Claim fields a reply to needs_info may correct, in the order the reply form shows them, with the words a reply's
# summary uses for each.
ANSWER_FIELDS = {
    "model_claimed": "model number",
    "serial_claimed": "serial number",
    "date_code_claimed": "date code",
    "retailer_claimed": "store",
    "purchase_date": "purchase date",
}
NOT_CONFIRMED = ("mismatch", "ambiguous", "not_provided", "unparsed", "out_of_range")


class LinkInvalid(LookupError):
    """Unknown token, wrong hash, or the program is closed: always a 404 (never reveal which)."""


class NotEditable(ValueError):
    pass


class EmptyAnswer(ValueError):
    """A reply with no text, no changed detail, and no new photo."""


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
        "status": claimant_status(claim.status),
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
        **_reply_form(session, claim, program),
        "decision_reasons": claimant_reasons(claim.status, decision, failed_rules(session, claim)),
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
# A person is deciding: the claimant never learns whether that is a fraud flag, a cap, or a money threshold
# (UI owner's note on PR 9: flagged and capped reached anyone holding the link).
HELD_FOR_PERSON = frozenset({"flagged", "capped", "under_review"})
DENY_CHECKS = ("check_model", "check_serial", "check_date_code", "check_purchase")


def claimant_status(status: str) -> str:
    return "under_review" if status in HELD_FOR_PERSON else status


def _latest_checks(session: Session, claim: Claim, tools: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    """The named tools' recorded results from the claim's latest run, in step order."""
    run_id = session.scalar(
        select(AgentRun.id)
        .where(AgentRun.claim_id == claim.id, AgentRun.firm_id == claim.firm_id)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    if run_id is None:
        return []
    rows = session.execute(
        select(AgentStep.tool, AgentStep.result)
        .where(AgentStep.run_id == run_id, AgentStep.tool.in_(tools))
        .order_by(AgentStep.idx)
    )
    return [(str(tool), result if isinstance(result, dict) else {}) for tool, result in rows]


def _reply_form(session: Session, claim: Claim, program: RecallProgram) -> dict[str, Any]:
    """While a claim waits on the claimant: the fields and photos the reply form shows, from the checks the latest run
    could not pass (a store not on the list shows the store, an unreadable label asks for the label photo). Each field
    carries the claim's current value. When the run left no usable checks, the form shows the model, store, and date
    and the program's required photos."""
    if claim.status != "needs_info":
        return {"answer_fields": [], "answer_photos": []}
    fields: set[str] = set()
    photos: set[str] = set()
    tools = ("read_label_photo", "check_model", "check_serial", "check_date_code", "check_purchase")
    destruction = ("check_destruction_photo", "check_destruction_presence")
    for tool, r in _latest_checks(session, claim, (*tools, *destruction)):
        if tool == "read_label_photo" and r.get("legible") is False:
            photos.add("label")
        elif tool == "check_model" and r.get("verdict") in NOT_CONFIRMED:
            fields.add("model_claimed")
            photos.add("label")
        elif tool == "check_serial" and r.get("verdict") in NOT_CONFIRMED:
            fields.add("serial_claimed")
        elif tool == "check_date_code" and r.get("verdict") in NOT_CONFIRMED:
            fields.add("date_code_claimed")
        elif tool == "check_purchase":
            if r.get("retailer") in ("not_listed", "unknown"):
                fields.add("retailer_claimed")
            if r.get("window") in ("before", "after", "unknown"):
                fields.add("purchase_date")
            if r.get("receipt") == "missing":
                photos.add("receipt")
        elif tool in destruction and (
            r.get("verdict") == "missing" or (r.get("verdict") == "checked" and not r.get("complies"))
        ):
            photos.add("destruction")
    if not fields and not photos:
        fields = {"model_claimed", "retailer_claimed", "purchase_date"}
        photos = set(required_photos(program))
    return {
        "answer_fields": [{"name": f, "value": _field_value(claim, f)} for f in ANSWER_FIELDS if f in fields],
        "answer_photos": [k for k in PHOTO_KINDS if k in photos],
    }


def _field_value(claim: Claim, name: str) -> str | None:
    value = getattr(claim, name)
    return None if value is None else value.isoformat() if name == "purchase_date" else str(value)


def failed_rules(session: Session, claim: Claim) -> set[str]:
    """The deny rules the claim's latest run actually failed, read from its recorded check results. The decision's
    cited_rules list every rule a reason touched, passing ones included, so they cannot word a denial alone."""
    failed: set[str] = set()
    for tool, r in _latest_checks(session, claim, DENY_CHECKS):
        if tool == "check_model" and r.get("verdict") == "mismatch":
            failed.add("R:models")
        elif tool == "check_serial" and r.get("verdict") == "out_of_range":
            failed.add("R:serials")
        elif tool == "check_date_code" and r.get("verdict") == "out_of_range":
            failed.add("R:date_codes")
        elif tool == "check_purchase" and r.get("window") == "before":
            failed.add("R:sold_window")
    return failed


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def claimant_reasons(status: str, decision: dict[str, Any], failed: set[str] | None = None) -> list[str]:
    """A fixed, claimant-safe wording from the outcome and the rules the run failed: never model prose, reviewer
    notes, or anything about other claims (red-team finding, decisions log 2026-10-18). When the decision cites rule
    ids (agent runs), a failed rule is worded only if the decision cited it too."""
    if status == "approved":
        return ["Your claim was approved."]
    if status == "needs_info":
        return ["More information is needed to finish your claim."]
    if status == "denied":
        if decision.get("review_action") == "mark_fraud":
            return ["Your claim could not be approved. Contact the firm for details."]
        cited = {c for c in decision.get("cited_rules", []) if c in CLAIMANT_REASON}
        shown = (failed or set()) & cited if cited else (failed or set())
        return sorted({CLAIMANT_REASON[c] for c in shown}) or [
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
    storage: Storage,
    kind: str,
    content_type: str | None,
    data: bytes,
    max_bytes: int,
    max_pixels: int,
) -> tuple[ClaimPhoto, list[str]]:
    """Returns the new photo and the storage keys it replaced; the caller deletes those only after the commit. The
    bytes are stored before the row is written, so a failed put (StorageError) leaves no row behind."""
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
    storage.put(relative, data, MEDIA_TYPES[ext])
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
    return photo, [p.storage_path for p in old]


def answer(session: Session, pc: PortalClaim, message: str, fields: dict[str, Any] | None = None) -> str:
    """A reply to needs_info. Corrected claim fields, or a photo uploaded since the decision, send the claim back for
    a fresh adjudication run ("rechecked"). A reply that only adds text goes to a person ("to_person", hold_reason
    claimant_reply): the agent reads claim fields and photos, never claimant text (that would be an instruction
    channel from the public), so a run on unchanged facts asks the same question again. That loop is what the first
    live claim met (2026-10-21)."""
    claim = pc.claim
    if claim.status != "needs_info":
        raise NotEditable("the claim is not waiting for an answer")
    changed = {
        k: v
        for k, v in (fields or {}).items()
        if k in ANSWER_FIELDS and v is not None and v != "" and v != getattr(claim, k)
    }
    new_photo = bool(
        claim.decided_at
        and session.scalar(
            select(func.count())
            .select_from(ClaimPhoto)
            .where(
                ClaimPhoto.claim_id == claim.id,
                ClaimPhoto.firm_id == claim.firm_id,
                ClaimPhoto.uploaded_at > claim.decided_at,
            )
        )
    )
    note = message.strip()[:2000]
    if not (note or changed or new_photo):
        raise EmptyAnswer("write an answer, correct a detail, or add a photo")
    summary = [f"Updated the {', '.join(ANSWER_FIELDS[k] for k in ANSWER_FIELDS if k in changed)}."] if changed else []
    if new_photo:
        summary.append("Added a photo.")
    entry: dict[str, Any] = {"from": "claimant", "text": note or " ".join(summary), "at": datetime.now(UTC).isoformat()}
    if changed:
        entry["changed"] = sorted(changed)
    messages = [*(claim.messages or []), entry]
    fence = (Claim.id == claim.id, Claim.firm_id == claim.firm_id, Claim.status == "needs_info")
    if changed or new_photo:
        after, outcome = "submitted", "rechecked"
        values: dict[str, Any] = {
            **changed,
            "messages": messages,
            "status": after,
            "submitted_at": datetime.now(UTC),
            "decision": None,
            "decided_at": None,
            "decided_by": None,
            "attempts": 0,
        }
    else:
        after, outcome = "under_review", "to_person"
        held = {**(claim.decision or {}), "routed_to_human": True, "hold_reason": "claimant_reply"}
        values = {"messages": messages, "status": after, "decision": held}
    done = session.execute(update(Claim).where(*fence).values(**values).returning(Claim.id)).first()
    if done is None:  # the claim left needs_info meanwhile (a reviewer, or a duplicate found later)
        raise NotEditable("the claim is no longer waiting for an answer")
    session.add(
        AuditLog(
            firm_id=claim.firm_id,
            actor="claimant",
            action="claim_answered",
            target_type="claim",
            target_id=str(claim.id),
            before={"status": "needs_info"},
            after={"status": after, "changed": sorted(changed), "new_photo": new_photo},
        )
    )
    log_event(log, "portal_answer", firm_id=str(claim.firm_id), claim_id=str(claim.id), outcome=outcome)
    return outcome


CHOOSABLE = ("pending", "onboarding", "failed", "pending_export")
SHARED_MAILING = "shared_mailing_address"


class PayoutConflict(ValueError):
    pass


def fingerprint(secret: str, value: str | None) -> str | None:
    """A keyed fingerprint for audit rows: equal addresses compare equal, the address itself is never stored."""
    import hashlib
    import hmac

    from recallops.claims.tools.checks import _norm_text

    norm = _norm_text(value)
    return hmac.new(secret.encode(), norm.encode(), hashlib.sha256).hexdigest()[:16] if norm else None


def _home(person: dict[str, Any]) -> str:
    return " ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip"))


def choose_check_payout(session: Session, pc: PortalClaim, mailing_address: str | None, secret: str) -> str:
    """The claimant chose a mailed check (the export batch pays it). One transaction under the program's money lock,
    claim row locked before the payout row (the money paths' lock order). The address can change only while no
    batch or transfer holds the payout.

    The check's destination (the mailing address, or the home address when there is none) is compared on every
    choice with every other non-draft claim's mailing and home address: a destination another claim uses is the
    redirect pattern the duplicate check cannot see, because it compares home addresses only. Every such claim that
    is approved with money not yet moved goes to a person (under_review) with its payout held. The program lock
    makes two concurrent choices of the same new address see each other. Returns the payout's status."""
    from recallops.claims.tools.checks import _norm_text
    from recallops.payouts.db_ledger import lock_program_money, transition
    from recallops.tenancy import FirmContext

    claim = pc.claim
    ctx = FirmContext(firm_id=claim.firm_id, user_id="claimant", role="admin")
    if mailing_address and not _norm_text(mailing_address):
        raise PayoutConflict("the mailing address needs letters or digits a US carrier can read")
    lock_program_money(session, claim.program_id)
    row = session.execute(
        text("SELECT status, claimant, program_id FROM claims WHERE id = :c AND firm_id = :f FOR UPDATE"),
        {"c": claim.id, "f": claim.firm_id},
    ).one()
    from recallops.models import Payout

    payout = session.scalars(
        select(Payout).where(Payout.firm_id == claim.firm_id, Payout.claim_id == claim.id).with_for_update()
    ).first()
    if row.status != "approved" or payout is None:
        raise PayoutConflict("there is no approved refund to pay out")
    if payout.status not in CHOOSABLE:
        raise PayoutConflict(f"the payout is already {payout.status}")
    claimant = dict(row.claimant or {})
    if mailing_address and _norm_text(mailing_address) != _norm_text(claimant.get("mailing_address")):
        session.add(
            AuditLog(
                firm_id=claim.firm_id,
                actor="claimant",
                action="payout_address_changed",
                target_type="claim",
                target_id=str(claim.id),
                before={"mailing_fp": fingerprint(secret, claimant.get("mailing_address"))},
                after={"mailing_fp": fingerprint(secret, mailing_address)},
            )
        )
        claimant["mailing_address"] = mailing_address[:300]
        session.execute(
            text("UPDATE claims SET claimant = CAST(:c AS jsonb) WHERE id = :id AND firm_id = :f"),
            {"c": json.dumps(claimant), "id": claim.id, "f": claim.firm_id},
        )
    if hold_shared_destination(session, ctx, claim.id, row.program_id, claimant):
        session.refresh(payout)
        return str(payout.status)
    if payout.status != "pending_export":
        transition(session, ctx, payout, "pending_export", method="check_export")
    return str(payout.status)


def hold_shared_destination(
    session: Session, ctx: Any, claim_id: UUID, program_id: UUID, claimant: dict[str, Any]
) -> bool:
    """The check's destination (the mailing address, or the home address when there is none) against every other
    non-draft claim's mailing and home address in the program. A destination another claim uses is the redirect
    pattern the duplicate check cannot see, because it compares home addresses only: every such claim that is
    approved with money not yet moved goes to a person (under_review) with its payout held. The caller holds the
    program's money lock (lock_program_money) and this claim's row lock. Returns whether anything was held. Used by
    the claimant's check choice and by the automatic fallback to a check after a confirmed Connect failure."""
    from recallops.claims.tools.checks import _norm_text
    from recallops.payouts.db_ledger import hold_for_claim

    target = _norm_text(claimant.get("mailing_address")) or _norm_text(_home(claimant))
    if not target:
        return False
    others = session.execute(
        text(
            "SELECT id, claimant FROM claims WHERE firm_id = :f AND program_id = :p AND id <> :c "
            "AND status <> 'draft' ORDER BY id"
        ),
        {"f": ctx.firm_id, "p": program_id, "c": claim_id},
    ).all()
    shared = [
        o.id
        for o in others
        if target
        in {
            v for v in (_norm_text((o.claimant or {}).get("mailing_address")), _norm_text(_home(o.claimant or {}))) if v
        }
    ]
    if not shared:
        return False
    for cid in sorted({claim_id, *shared}):
        done = session.execute(
            text(
                "UPDATE claims SET status = 'under_review', decision = coalesce(decision, '{}'::jsonb) || "
                "CAST(:d AS jsonb) WHERE id = :c AND firm_id = :f AND status = 'approved' RETURNING id"
            ),
            {"d": json.dumps({"routed_to_human": True, "hold_reason": SHARED_MAILING}), "c": cid, "f": ctx.firm_id},
        ).first()
        if done is None:
            continue
        hold_for_claim(session, ctx, cid, SHARED_MAILING)
        session.add(
            AuditLog(
                firm_id=ctx.firm_id,
                actor="system",
                action="claim_held_shared_mailing",
                target_type="claim",
                target_id=str(cid),
                before={"status": "approved"},
                after={"status": "under_review", "shared_with": len(shared)},
            )
        )
    log_event(log, "payout_shared_mailing", level=30, firm_id=str(ctx.firm_id), claim_id=str(claim_id))
    return True
