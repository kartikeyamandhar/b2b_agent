"""Synthetic claims CLI.

python -m recallops.synth --golden
    Builds the 300-claim adjudication golden set from the 20 synth-source golden notices (15 claims each,
    evals/adjudication/planted.yaml). Writes golden_claims.json (what an adjudicator may see) and one label
    sidecar per claim under evals/adjudication/labels/ (what only the scorer reads). Photos go to
    data/generated/golden_photos/ (gitignored, reproducible from the seed).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", action="store_true", required=True)
    parser.parse_args(argv)
    print(json.dumps(build_golden(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
