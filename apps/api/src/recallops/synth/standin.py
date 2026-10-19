"""The 40-claim stand-in for the classmate set (PREREG Revision 2): rendered from specs written by one sub-agent that
read only the four notice texts and outcome_policy.yaml (never the generator, the tools, or any label). The specs
are committed exactly as authored (evals/adjudication/standin_specs.json); expected outcomes are the author's.

python -m recallops.synth.standin    writes standin_claims.json, standin_labels/, photos under
                                     data/generated/standin_photos/ (gitignored, reproducible)

Photos use the generator's renderer (so the block is not held out on the photo side; the write-up says so). Every
photo is seeded by its spec id; a spec that reuses another spec's label photo gets those exact bytes. A freeze
check asserts that the only duplicate hits inside each notice's population are the ones the author planted.
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from recallops.claims.tools import ClaimFacts, PhotoRef, check_duplicates
from recallops.evals.golden import load_program_rules
from recallops.settings import REPO_ROOT
from recallops.synth.generate import _photo
from recallops.synth.probe import unreadable
from recallops.synth.render import draw_style, instruction_family, render_destruction, render_label, render_receipt

ADJ_DIR = REPO_ROOT / "evals" / "adjudication"
PHOTO_DIR = REPO_ROOT / "data" / "generated" / "standin_photos"
NS = uuid.UUID("5ad1a000-0000-4000-8000-000000000040")


def _rng(spec_id: str, what: str, attempt: int = 0) -> random.Random:
    return random.Random(f"standin:{spec_id}:{what}:{attempt}")


def build(out_dir: Path = ADJ_DIR, photo_dir: Path = PHOTO_DIR) -> dict[str, Any]:
    specs = json.loads((out_dir / "standin_specs.json").read_text(encoding="utf-8"))["specs"]
    rules = load_program_rules(out_dir / "program_rules.json")
    by_id = {s["spec_id"]: s for s in specs}
    labels_bytes: dict[str, bytes] = {}
    claims: dict[str, dict[str, Any]] = {}
    photos: dict[str, list[Any]] = {}
    for spec in specs:
        sid = spec["spec_id"]
        r = rules[spec["notice"]]
        shots = []
        lines = spec["label_photo"]["lines"]
        if spec.get("same_label_photo_as"):
            pass  # filled after the referenced spec is rendered
        elif lines:
            rng = _rng(sid, "label")
            data = render_label(rng, draw_style(rng), lines)
            if spec["label_photo"]["quality"] == "unreadable":
                data = unreadable(data)
            labels_bytes[sid] = data
            shots.append(_photo("label", data))
        if spec["destruction_photo"] in ("intact", "destroyed"):
            rng = _rng(sid, "destruction")
            family = instruction_family(r.remedy.destruction_instruction)
            shots.append(
                _photo(
                    "destruction",
                    render_destruction(rng, draw_style(rng), family, spec["destruction_photo"] == "destroyed"),
                )
            )
        if spec["receipt_photo"]:
            rng = _rng(sid, "receipt")
            typed = spec["typed"]
            cents = round((typed.get("refund_dollars") or 0) * 100)
            shots.append(
                _photo(
                    "receipt",
                    render_receipt(
                        rng, draw_style(rng), typed.get("retailer") or "", typed.get("purchase_date") or "", cents
                    ),
                )
            )
        photos[sid] = shots
    for spec in specs:
        source = spec.get("same_label_photo_as")
        if source:
            photos[spec["spec_id"]].insert(0, _photo("label", labels_bytes[source]))
    for spec in specs:
        sid = spec["spec_id"]
        typed = spec["typed"]
        claim_id = uuid.uuid5(NS, sid)
        person = {**spec["claimant"], "synthetic": True, "payout_destination": f"synth_dest_standin_{sid.lower()}"}
        claims[sid] = {
            "claim_id": str(claim_id),
            "spec_id": sid,
            "program": spec["notice"],
            "population": f"standin:{spec['notice']}",
            "model_claimed": typed.get("model"),
            "serial_claimed": typed.get("serial"),
            "date_code_claimed": typed.get("date_code"),
            "retailer_claimed": typed.get("retailer"),
            "purchase_date": typed.get("purchase_date"),
            "refund_cents": round(typed["refund_dollars"] * 100) if typed.get("refund_dollars") is not None else None,
            "remedy": rules[spec["notice"]].remedy.remedy_type,
            "claimant": person,
            "photos": [
                {
                    "kind": p.kind,
                    "sha256": p.sha256,
                    "phash": p.phash,
                    "path": f"data/generated/standin_photos/{spec['notice']}/{claim_id}_{p.kind}.jpg",
                }
                for p in photos[sid]
            ],
        }
    _freeze_check(specs, claims, by_id)
    for spec in specs:
        sid = spec["spec_id"]
        for p in photos[sid]:
            path = photo_dir / spec["notice"] / f"{claims[sid]['claim_id']}_{p.kind}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(p.data)
    labels_dir = out_dir / "standin_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    for stale in labels_dir.glob("*.json"):
        stale.unlink()
    for spec in specs:
        c = claims[spec["spec_id"]]
        label = {
            "claim_id": c["claim_id"],
            "spec_id": spec["spec_id"],
            "program": spec["notice"],
            "category": f"expect_{spec['expected_outcome']}",
            "expected_outcome": spec["expected_outcome"],
            "rationale": spec["rationale"],
            "violations": [],
            "subtypes": {},
        }
        (labels_dir / f"{c['claim_id']}.json").write_text(json.dumps(label, indent=1) + "\n", encoding="utf-8")
    payload = {
        "block": "standin40",
        "author": "one sub-agent, blind to the generator, tools, and labels",
        "claims": [claims[s["spec_id"]] for s in specs],
    }
    (out_dir / "standin_claims.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return {"claims": len(specs), "expected": dict(Counter(s["expected_outcome"] for s in specs))}


def _facts(c: dict[str, Any]) -> ClaimFacts:
    person = c["claimant"]
    return ClaimFacts(
        claim_id=uuid.UUID(c["claim_id"]),
        model_claimed=c["model_claimed"],
        serial_claimed=c["serial_claimed"],
        date_code_claimed=c["date_code_claimed"],
        retailer_claimed=c["retailer_claimed"],
        purchase_date=date.fromisoformat(c["purchase_date"]) if c["purchase_date"] else None,
        refund_cents=c["refund_cents"],
        email=person.get("email"),
        address=" ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip")),
        payout_destination=person.get("payout_destination"),
        photos=tuple(PhotoRef(kind=p["kind"], sha256=p["sha256"], phash=p["phash"]) for p in c["photos"]),
    )


def _freeze_check(specs: list[dict[str, Any]], claims: dict[str, dict[str, Any]], by_id: dict[str, Any]) -> None:
    planted = {
        frozenset({s["spec_id"], s[k]}) for s in specs for k in ("same_label_photo_as", "same_address_as") if s.get(k)
    }
    facts = {sid: _facts(c) for sid, c in claims.items()}
    for sid, c in claims.items():
        population = [facts[o] for o, oc in claims.items() if oc["population"] == c["population"] and o != sid]
        for hit in check_duplicates(facts[sid], population).hits:
            other = next(o for o, f in facts.items() if f.claim_id == hit.other_claim_id)
            if frozenset({sid, other}) not in planted:
                raise SystemExit(f"{sid}: unplanted duplicate hit with {other} ({hit.kind}, {hit.detail})")


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(build(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
