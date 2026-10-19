"""make seed: a local database the UI owner can build every screen against.

Loads the committed CPSC fixture (data/samples/cpsc_sample.json), opens a program for the Scotch thermal laminator
recall (27024) with baseline-extracted rules, generates 500 synthetic claims with planted violations and rendered
photos, adjudicates them with the rules-only adjudicator (traces in agent_runs and agent_steps), moves the approved
refunds' payouts through the real ledger (transferred, failed, onboarding, pending, and one export batch), and adds a
second firm so cross-tenant behavior can be tried from the UI. Ids are deterministic, so README curl examples stay
valid across reseeds. Idempotent: rows of the two seed firms are deleted and rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, text, update
from sqlalchemy.orm import Session

from recallops.claims.rules_only import adjudicate
from recallops.claims.tools import ClaimFacts, PhotoRef
from recallops.db.engine import internal_engine
from recallops.db.session import internal_session
from recallops.ingest.runner import run as ingest_run
from recallops.logs import configure, get_logger, log_event
from recallops.models import (
    AuditLog,
    Claim,
    ClaimPhoto,
    Firm,
    FirmMember,
    FraudCluster,
    Recall,
    RecallProgram,
    UsageMeter,
)
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.export import export_firm
from recallops.photos import store
from recallops.rules import baseline
from recallops.settings import REPO_ROOT, get_settings
from recallops.synth.generate import SynthClaim, generate_program, load_yaml
from recallops.tenancy import FirmContext
from recallops.tracing.writer import DbTrace

log = get_logger("seed")

NAMESPACE = uuid.UUID("5eed0000-0000-4000-8000-000000000000")
DEMO_FIRM_ID = uuid.UUID("00000000-0000-4000-8000-00000000d001")
OTHER_FIRM_ID = uuid.UUID("00000000-0000-4000-8000-00000000d002")
DEMO_RECALL = "27024"
OTHER_RECALL = "27025"
FIXTURE = REPO_ROOT / "data" / "samples" / "cpsc_sample.json"
STATUS = {"approve": "approved", "deny": "denied", "needs_info": "needs_info", "flag": "flagged"}
TENANT_DELETE_ORDER = (
    "document_chunks",
    "agent_steps",
    "agent_runs",
    "payouts",
    "export_batches",
    "claim_photos",
    "fraud_clusters",
    "mpr_reports",
    "documents",
    "audit_log",
    "claims",
    "recall_programs",
    "usage_meters",
    "firm_members",
)


def program_id_for(firm_id: uuid.UUID, recall: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"program:{firm_id}:{recall}")


def _wipe(session: Session, firm_ids: list[uuid.UUID]) -> None:
    """Reset tooling only: audit_log is append-only (migration 0003) unless app.audit_purge is on."""
    session.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
    for table in TENANT_DELETE_ORDER:
        session.execute(text(f"DELETE FROM {table} WHERE firm_id = ANY(:ids)"), {"ids": firm_ids})
    session.execute(text("DELETE FROM firms WHERE id = ANY(:ids)"), {"ids": firm_ids})


def _facts(c: SynthClaim) -> ClaimFacts:
    person = c.claimant
    address = " ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip"))
    return ClaimFacts(
        claim_id=c.claim_id,
        model_claimed=c.model_claimed,
        serial_claimed=c.serial_claimed,
        date_code_claimed=c.date_code_claimed,
        retailer_claimed=c.retailer_claimed,
        purchase_date=c.purchase_date,
        refund_cents=c.refund_cents,
        email=person.get("email"),
        address=address,
        payout_destination=person.get("payout_destination"),
        photos=tuple(PhotoRef(kind=p.kind, sha256=p.sha256, phash=p.phash) for p in c.photos),
    )


def _question(reasons: list[str]) -> str:
    return "To finish your claim, please provide: " + "; ".join(reasons) + "."


def insert_claims(engine: Engine, firm_id: uuid.UUID, program_id: uuid.UUID, claims: list[SynthClaim]) -> None:
    """Submitted claims with their rendered photos stored like portal uploads (dev tokens dev.<firm>.<claim>)."""
    settings = get_settings()
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        for c in claims:
            token = f"dev.{firm_id}.{c.claim_id}"
            session.add(
                Claim(
                    id=c.claim_id,
                    firm_id=firm_id,
                    program_id=program_id,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    status="submitted",
                    claimant=c.claimant,
                    model_claimed=c.model_claimed,
                    serial_claimed=c.serial_claimed,
                    date_code_claimed=c.date_code_claimed,
                    retailer_claimed=c.retailer_claimed,
                    purchase_date=c.purchase_date,
                    quantity=1,
                    remedy=c.remedy,
                    refund_cents=c.refund_cents,
                    submitted_at=now,
                )
            )
        session.flush()
        for c in claims:
            for p in c.photos:
                relative = f"{firm_id}/{c.claim_id}/{p.kind}.jpg"
                stored = store(settings.storage_dir, relative, p.data)
                session.add(
                    ClaimPhoto(
                        firm_id=firm_id,
                        claim_id=c.claim_id,
                        kind=p.kind,
                        storage_path=relative,
                        sha256=stored.sha256,
                        phash=stored.phash,
                        exif=stored.exif,
                    )
                )


# Deterministic payout states for the demo, by position among approved refunds (index mod 10).
PAYOUT_PLAN = (
    "export",
    "export",
    "export",
    "transfer",
    "transfer",
    "transfer",
    "pending",
    "pending",
    "onboard",
    "fail",
)


def seed_payouts(engine: Engine, ctx: FirmContext, approved: list[tuple[uuid.UUID, int]], plan: str) -> dict[str, Any]:
    """Approved refunds through the real ledger, so the demo has every payout state, an export batch, and MPR
    corrections. A free-plan firm has no Connect payouts: its rows wait for the export batch (03-payments.md)."""
    ledger = DbPayoutLedger(engine, session_for=lambda c, e: internal_session(e))
    method = "stripe_connect" if plan != "free" else "check_export"
    for i, (claim_id, cents) in enumerate(sorted(approved)):
        ref = ledger.create_pending(ctx, claim_id, cents, method)
        if method != "stripe_connect":
            continue
        step = PAYOUT_PLAN[i % len(PAYOUT_PLAN)]
        if step == "export":
            ledger.to_export(ctx, ref.payout_id)
        elif step == "transfer":
            ledger.claim_for_transfer(ctx, ref.payout_id)
            ledger.mark_transferred(ctx, ref.payout_id, f"tr_seed_{claim_id.hex[:12]}")
        elif step == "onboard":
            ledger.mark_onboarding(ctx, ref.payout_id, f"acct_seed_{claim_id.hex[:12]}")
        elif step == "fail":
            ledger.claim_for_transfer(ctx, ref.payout_id)
            ledger.mark_failed(ctx, ref.payout_id, "seeded transfer failure (test mode)")
    batch = export_firm(engine, ctx.firm_id, actor="seed") if method == "stripe_connect" else None
    with engine.connect() as conn:
        statuses: dict[str, int] = dict(
            conn.execute(
                text("SELECT status, count(*) FROM payouts WHERE firm_id = :f GROUP BY status ORDER BY status"),
                {"f": ctx.firm_id},
            ).all()
        )
    return {
        "payouts": {str(k): int(v) for k, v in statuses.items()},
        "export_batch_rows": batch["rows"] if batch else 0,
    }


def seed_program(engine: Engine, firm_id: uuid.UUID, recall_source_id: str, n: int, seed: int) -> dict[str, Any]:
    settings = get_settings()
    planted = load_yaml(REPO_ROOT / "evals" / "adjudication" / "planted.yaml")
    policy = load_yaml(REPO_ROOT / "evals" / "adjudication" / "outcome_policy.yaml")
    ctx = FirmContext(firm_id=firm_id, user_id="seed", role="admin")
    with Session(engine) as session, session.begin():
        recall = session.query(Recall).filter(Recall.source == "cpsc", Recall.source_id == recall_source_id).one()
        rules = baseline.extract(recall.source_text)
        rules_json = json.loads(rules.model_dump_json())
        session.execute(
            update(Recall)
            .where(Recall.id == recall.id)
            .values(
                extracted_rules=rules_json,
                extracted_at=datetime.now(UTC),
                extractor=baseline.EXTRACTOR,
                extractor_version=baseline.EXTRACTOR_VERSION,
            )
        )
        program_id = program_id_for(firm_id, recall_source_id)
        session.add(
            RecallProgram(
                id=program_id,
                firm_id=firm_id,
                recall_id=recall.id,
                status="open",
                identification_rules=rules_json["identification"],
                remedy_rules=rules_json["remedy"],
                value_threshold_cents=settings.default_value_threshold_cents,
                opened_at=datetime.now(UTC),
            )
        )
        recall_date = recall.recall_date
    claims = generate_program(rules, str(program_id), n, seed, planted, policy, recall_date)
    insert_claims(engine, firm_id, program_id, claims)
    facts_all = [_facts(c) for c in claims]
    approved: list[tuple[uuid.UUID, int]] = []
    outcomes: Counter[str] = Counter()
    clusters: dict[tuple[str, tuple[str, ...]], None] = {}
    t0 = time.monotonic()
    for c, facts in zip(claims, facts_all, strict=True):
        trace = DbTrace(engine, "rules_only", firm_id=firm_id, claim_id=c.claim_id, program_id=program_id)
        result = adjudicate(
            facts, rules, facts_all, settings.default_value_threshold_cents, trace, settings.phash_duplicate_bits
        )
        status = "under_review" if result.outcome == "approve" and result.routed_to_human else STATUS[result.outcome]
        outcomes[status] += 1
        decision = {
            "outcome": result.outcome,
            "reasons": result.reasons,
            "cited_rules": result.cited_rules,
            "routed_to_human": result.routed_to_human,
            "confidence": None,
            "question_for_claimant": _question(result.reasons) if result.outcome == "needs_info" else None,
        }
        with Session(engine) as session, session.begin():
            session.execute(
                update(Claim)
                .where(Claim.id == c.claim_id)
                .values(status=status, decision=decision, decided_at=datetime.now(UTC), decided_by="rules_only")
            )
            session.add(
                AuditLog(
                    firm_id=firm_id,
                    actor="system",
                    action="claim_adjudicated",
                    target_type="claim",
                    target_id=str(c.claim_id),
                    before={"status": "submitted"},
                    after={"status": status, "outcome": result.outcome},
                )
            )
        for kind, other in zip(result.duplicate_kinds, result.duplicate_with, strict=False):
            clusters[(kind, tuple(sorted({str(c.claim_id), other})))] = None
        if result.outcome == "approve" and c.refund_cents and not result.routed_to_human:
            approved.append((c.claim_id, c.refund_cents))
    elapsed = time.monotonic() - t0
    with Session(engine) as session:
        plan = str(session.get(Firm, firm_id).plan)  # type: ignore[union-attr]
    payouts = seed_payouts(engine, ctx, approved, plan)
    with Session(engine) as session, session.begin():
        for kind, members in clusters:
            session.add(
                FraudCluster(
                    firm_id=firm_id, program_id=program_id, kind=kind, member_claim_ids=[uuid.UUID(m) for m in members]
                )
            )
        session.add(
            UsageMeter(firm_id=firm_id, period_start=date.today().replace(day=1), claims_adjudicated=len(claims))
        )
    summary = {
        "firm_id": str(firm_id),
        "program_id": str(program_id),
        "recall": recall_source_id,
        "claims": len(claims),
        "statuses": dict(outcomes),
        "clusters": len(clusters),
        **payouts,
        "adjudication_seconds": round(elapsed, 2),
        "first_claim_id": str(claims[0].claim_id),
    }
    log_event(log, "seed_program_done", firm_id=str(firm_id), duration_ms=round(elapsed * 1000), claims=len(claims))
    return summary


def run(engine: Engine | None = None, n: int = 500, seed: int = 42) -> dict[str, Any]:
    engine = engine or internal_engine()
    t0 = time.monotonic()
    ingest_run([("cpsc_sample", json.loads(FIXTURE.read_text(encoding="utf-8")))], engine)
    with Session(engine) as session, session.begin():
        _wipe(session, [DEMO_FIRM_ID, OTHER_FIRM_ID])
        session.add_all(
            [
                Firm(id=DEMO_FIRM_ID, name="Demo Recall Co", plan="growth"),
                Firm(id=OTHER_FIRM_ID, name="Other Firm Inc", plan="free"),
            ]
        )
        session.flush()
        session.add_all(
            [
                FirmMember(firm_id=DEMO_FIRM_ID, clerk_user_id="dev-admin", role="admin"),
                FirmMember(firm_id=DEMO_FIRM_ID, clerk_user_id="dev-reviewer", role="reviewer"),
                FirmMember(firm_id=OTHER_FIRM_ID, clerk_user_id="dev-other", role="admin"),
            ]
        )
    demo = seed_program(engine, DEMO_FIRM_ID, DEMO_RECALL, n, seed)
    other = seed_program(engine, OTHER_FIRM_ID, OTHER_RECALL, 5, seed)
    return {"demo": demo, "other": other, "seconds": round(time.monotonic() - t0, 2)}


def main() -> int:
    configure(get_settings().log_level)
    print(json.dumps(run(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
