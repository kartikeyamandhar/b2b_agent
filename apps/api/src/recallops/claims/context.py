"""Load a claim, its program, its photos, and the program's other claims from the database for one run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from recallops.claims.tools import ClaimFacts, PhotoRef
from recallops.logs import get_logger, log_event
from recallops.models import Claim, ClaimPhoto, RecallProgram
from recallops.rules.schemas import ExtractedRules, IdentificationRules, RemedyRules
from recallops.storage import InvalidKey, Storage

log = get_logger("claims.context")


@dataclass(frozen=True)
class LoadedClaim:
    claim_id: UUID
    firm_id: UUID
    program_id: UUID
    facts: ClaimFacts
    rules: ExtractedRules
    value_threshold_cents: int
    refund_cents: int | None
    photo_paths: dict[str, str]  # kind -> storage path (latest upload per kind)


def facts_from_rows(c: Claim, photos: list[ClaimPhoto]) -> ClaimFacts:
    person = c.claimant or {}
    address = " ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip")).strip() or None
    latest: dict[str, ClaimPhoto] = {}
    for p in sorted(photos, key=lambda p: (p.kind, p.uploaded_at)):
        latest[p.kind] = p
    return ClaimFacts(
        claim_id=c.id,
        model_claimed=c.model_claimed,
        serial_claimed=c.serial_claimed,
        date_code_claimed=c.date_code_claimed,
        retailer_claimed=c.retailer_claimed,
        purchase_date=c.purchase_date,
        refund_cents=c.refund_cents,
        email=person.get("email"),
        address=address,
        payout_destination=person.get("payout_destination"),
        photos=tuple(PhotoRef(kind=p.kind, sha256=p.sha256, phash=p.phash) for p in latest.values()),
    )


def load_claim(session: Session, claim_id: UUID) -> LoadedClaim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise LookupError(f"claim {claim_id} not found")
    program = session.scalars(
        select(RecallProgram).where(RecallProgram.id == claim.program_id, RecallProgram.firm_id == claim.firm_id)
    ).one()
    photos = list(
        session.scalars(select(ClaimPhoto).where(ClaimPhoto.claim_id == claim_id, ClaimPhoto.firm_id == claim.firm_id))
    )
    facts = facts_from_rows(claim, photos)
    rules = ExtractedRules(
        identification=IdentificationRules.model_validate(program.identification_rules or {}),
        remedy=RemedyRules.model_validate(program.remedy_rules or {}),
    )
    paths: dict[str, str] = {}
    for p in sorted(photos, key=lambda p: (p.kind, p.uploaded_at)):
        paths[p.kind] = p.storage_path
    return LoadedClaim(
        claim_id=claim.id,
        firm_id=claim.firm_id,
        program_id=claim.program_id,
        facts=facts,
        rules=rules,
        value_threshold_cents=program.value_threshold_cents,
        refund_cents=claim.refund_cents,
        photo_paths=paths,
    )


def program_claims(session: Session, firm_id: UUID, program_id: UUID, exclude: UUID) -> list[ClaimFacts]:
    """Every other non-draft claim of the program, with its photos (the duplicate check's population)."""
    claims = list(
        session.scalars(
            select(Claim)
            .where(
                Claim.firm_id == firm_id, Claim.program_id == program_id, Claim.status != "draft", Claim.id != exclude
            )
            .order_by(Claim.id)
        )
    )
    photos: dict[UUID, list[ClaimPhoto]] = {}
    if claims:
        for p in session.scalars(
            select(ClaimPhoto).where(ClaimPhoto.firm_id == firm_id, ClaimPhoto.claim_id.in_([c.id for c in claims]))
        ):
            photos.setdefault(p.claim_id, []).append(p)
    return [facts_from_rows(c, photos.get(c.id, [])) for c in claims]


def photo_reader(storage: Storage, paths: dict[str, str]) -> Callable[[str], bytes | None]:
    """Bytes by photo kind. None when the claim has no photo of that kind, the object is absent, or the stored key is
    unsafe (refused by the storage guard, never read). Any other StorageError propagates: the worker logs the failed
    attempt and retries later, so a storage outage never reads as a claim without photos."""

    def read(kind: str) -> bytes | None:
        rel = paths.get(kind)
        if rel is None:
            return None
        try:
            return storage.get(rel)
        except InvalidKey:
            log_event(log, "photo_key_refused", level=30, kind=kind)
            return None

    return read
