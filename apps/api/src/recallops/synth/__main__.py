"""Synthetic claims CLI.

python -m recallops.synth --golden
    Builds the 300-claim adjudication golden set from the 20 synth-source golden notices (15 claims each,
    evals/adjudication/planted.yaml). Writes golden_claims.json (what an adjudicator may see) and one label
    sidecar per claim under evals/adjudication/labels/ (what only the scorer reads). Photos go to
    data/generated/golden_photos/ (gitignored, reproducible from the seed).

python -m recallops.synth --registrations owners.csv --program <uuid> --n 200 --seed 7
    Synthetic product-owner registrations for the M5 campaign demo: about 70 percent own a recalled model (serial
    inside any stated range), the rest own another model or a unit outside the range. Upload with
    POST /v1/programs/{id}/registrations.

python -m recallops.synth --program <uuid> --n 500 --seed 42 [--violations evals/adjudication/planted.yaml]
    The M2 demo (01-backend.md section 7): generates n synthetic claims from an open program's rules, stores their
    photos like portal uploads, and inserts them as submitted, so `make worker` adjudicates them. Labels are not
    written anywhere the worker can read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from recallops.evals.golden import load_golden, load_program_rules
from recallops.settings import REPO_ROOT
from recallops.synth.generate import SynthClaim, generate_program, load_yaml

ADJ_DIR = REPO_ROOT / "evals" / "adjudication"
PHOTO_DIR = REPO_ROOT / "data" / "generated" / "golden_photos"


def public_record(c: SynthClaim, photo_root: str) -> dict[str, Any]:
    return {
        "claim_id": str(c.claim_id),
        "program": c.program_key,
        "model_claimed": c.model_claimed,
        "serial_claimed": c.serial_claimed,
        "date_code_claimed": c.date_code_claimed,
        "retailer_claimed": c.retailer_claimed,
        "purchase_date": c.purchase_date.isoformat() if c.purchase_date else None,
        "refund_cents": c.refund_cents,
        "remedy": c.remedy,
        "claimant": c.claimant,
        "photos": [
            {
                "kind": p.kind,
                "sha256": p.sha256,
                "phash": p.phash,
                "path": f"{photo_root}/{c.program_key}/{c.claim_id}_{p.kind}.jpg",
            }
            for p in c.photos
        ],
    }


def label_record(c: SynthClaim) -> dict[str, Any]:
    return {
        "claim_id": str(c.claim_id),
        "program": c.program_key,
        "violations": c.violations,
        "subtypes": c.subtypes,
        "cluster": c.cluster,
        "tier": c.tier,
        "expected_outcome": c.expected_outcome,
    }


def build_golden(out_dir: Path = ADJ_DIR, photo_dir: Path = PHOTO_DIR, limit: int | None = None) -> dict[str, Any]:
    planted = load_yaml(out_dir / "planted.yaml")
    policy = load_yaml(out_dir / "outcome_policy.yaml")
    # Rules come from the frozen snapshot, not the (since audited) extraction labels, so the set regenerates exactly.
    frozen = load_program_rules(out_dir / "program_rules.json")
    notices = [n for n in load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json") if n.synth_source]
    notices = notices[:limit] if limit else notices
    claims: list[SynthClaim] = []
    for notice in notices:
        claims += generate_program(
            frozen[notice.source_id],
            notice.source_id,
            planted["claims_per_program"],
            planted["seed"],
            planted,
            policy,
            notice.recall_date,
        )
    labels_dir = out_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    for stale in labels_dir.glob("*.json"):
        stale.unlink()
    for c in claims:
        for p in c.photos:
            path = photo_dir / c.program_key / f"{c.claim_id}_{p.kind}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(p.data)
        (labels_dir / f"{c.claim_id}.json").write_text(json.dumps(label_record(c), indent=1) + "\n", encoding="utf-8")
    payload = {
        "seed": planted["seed"],
        "programs": [n.source_id for n in notices],
        "claims": [public_record(c, "data/generated/golden_photos") for c in claims],
    }
    (out_dir / "golden_claims.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return {
        "claims": len(claims),
        "programs": len(notices),
        "expected": dict(Counter(c.expected_outcome for c in claims)),
        "violations": dict(Counter(v for c in claims for v in c.violations)),
        "tiers": dict(Counter(c.tier for c in claims)),
        "multi_violation": sum(1 for c in claims if len(c.violations) > 1),
    }


def load_program(program_id: UUID) -> dict[str, Any]:
    """Internal job: generate claims for a program into the database (recallops_internal, firm from the row)."""
    from sqlalchemy.orm import Session

    from recallops.db.engine import internal_engine
    from recallops.models import Recall, RecallProgram
    from recallops.rules.schemas import ExtractedRules, IdentificationRules, RemedyRules

    engine = internal_engine()
    with Session(engine) as session:
        program = session.get(RecallProgram, program_id)
        if program is None or program.status != "open":
            raise SystemExit(f"program {program_id} not found or not open")
        recall = session.get(Recall, program.recall_id) if program.recall_id else None
        rules = ExtractedRules(
            identification=IdentificationRules.model_validate(program.identification_rules or {}),
            remedy=RemedyRules.model_validate(program.remedy_rules or {}),
        )
        firm_id, recall_date = program.firm_id, recall.recall_date if recall else None
    return {"engine": engine, "firm_id": firm_id, "rules": rules, "recall_date": recall_date}


def write_registrations(path: Path, program_id: UUID, n: int, seed: int) -> dict[str, Any]:
    import csv
    import random

    from recallops.synth.claimants import person
    from recallops.synth.generate import date_code_inside, serial_inside, serial_outside

    loaded = load_program(program_id)
    rules = loaded["rules"]
    models = rules.identification.models or ["UNLISTED-1"]
    rng = random.Random(seed)
    kinds: dict[str, int] = {}
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["external_ref", "name", "email", "phone", "address", "model", "serial", "date_code", "purchase_date"]
        )
        for i in range(n):
            who = person(rng, f"reg{seed}", i)
            roll = rng.random()
            model, serial = rng.choice(models), serial_inside(rules, rng)
            kind = "recalled"
            if roll > 0.85:
                model, kind = f"{models[0]}-X{rng.randint(1, 9)}", "other_model"
            elif roll > 0.70 and rules.identification.serial_ranges:
                serial, kind = serial_outside(rules, rng), "serial_outside"
            kinds[kind] = kinds.get(kind, 0) + 1
            address = f"{who['address_line1']}, {who['city']} {who['state']} {who['zip']}"
            writer.writerow(
                [
                    f"R{seed}-{i:05d}",
                    who["name"],
                    who["email"],
                    who["phone"],
                    address,
                    model,
                    serial or "",
                    date_code_inside(rules, rng) or "",
                    "",
                ]
            )
    return {"path": str(path), "rows": n, "kinds": kinds}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--golden", action="store_true")
    mode.add_argument("--registrations", type=Path, help="write a synthetic registrations CSV here (needs --program)")
    parser.add_argument("--program", type=UUID, help="claims mode (alone) or the registrations' program")
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--violations", type=Path, default=ADJ_DIR / "planted.yaml")
    args = parser.parse_args(argv)
    if args.golden:
        print(json.dumps(build_golden(), indent=1))
        return 0
    if args.program is None:
        parser.error("give --golden, or --program (claims), or --registrations PATH --program")
    if args.registrations:
        print(json.dumps(write_registrations(args.registrations, args.program, args.n, args.seed), indent=1))
        return 0
    from recallops.seed import insert_claims

    loaded = load_program(args.program)
    planted = load_yaml(args.violations)
    policy = load_yaml(ADJ_DIR / "outcome_policy.yaml")
    claims = generate_program(
        loaded["rules"], f"{args.program}:{args.seed}", args.n, args.seed, planted, policy, loaded["recall_date"]
    )
    insert_claims(loaded["engine"], loaded["firm_id"], args.program, claims)
    print(json.dumps({"program_id": str(args.program), "submitted": len(claims)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
