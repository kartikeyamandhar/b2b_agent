"""The 60-claim capability probe (evals/adjudication/PREREG.md Revision 2): an assistant-designed block built to
target the rules-only adjudicator's blind spots by construction. Never kill-criterion evidence.

python -m recallops.synth.probe       writes evals/adjudication/probe_claims.json, probe_labels/, and photos under
                                      data/generated/probe_photos/ (gitignored, reproducible from the seed)

Each claim starts as a clean synthetic claim (no planted violation) of one of six golden programs, under its own
program key (probe:<id>) so claimants and serials never collide with the 300-claim set, and then changes exactly
one thing for its category. A freeze check asserts that no claim has a duplicate hit in its own program's probe
population and that every base claim is clean under the rules-only adjudicator.
"""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from recallops.claims.rules_only import adjudicate
from recallops.claims.tools import ClaimFacts, PhotoRef, check_duplicates, check_model
from recallops.claims.tools.checks import destruction_required
from recallops.evals.golden import load_golden, load_program_rules
from recallops.rules.schemas import ExtractedRules
from recallops.settings import REPO_ROOT
from recallops.synth.generate import (
    SynthClaim,
    _label_lines,
    _photo,
    generate_program,
    load_yaml,
    sibling_model,
    stream,
)
from recallops.synth.render import draw_style, instruction_family, render_destruction, render_label
from recallops.tracing.writer import MemoryTrace

ADJ_DIR = REPO_ROOT / "evals" / "adjudication"
PHOTO_DIR = REPO_ROOT / "data" / "generated" / "probe_photos"
SEED = 4242
PROGRAMS = ("24118", "25206", "26007", "26011", "26046", "24082")
PER_CATEGORY = 10
CATEGORIES = (
    "label_shows_other_model",
    "destruction_photo_intact",
    "label_unreadable",
    "label_confirms_typed_typo",
    "label_confirms_missing_typed_model",
    "clean",
)
SWAPS = {"0": "O", "O": "0", "1": "I", "I": "1", "5": "S", "S": "5", "8": "B", "B": "8", "2": "Z", "Z": "2"}
# Unreadable render (stated, checkable criterion): the label is downsampled to one fourteenth, upsampled with
# nearest neighbour, blurred with radius 5, and saved at JPEG quality 40. At 768 by 576 that leaves blocks larger
# than the 24 px text, so no character survives (checked by eye on every program before freezing).
UNREADABLE = {"downsample": 14, "blur_radius": 5, "jpeg_quality": 40}


def typo(model: str, rules: ExtractedRules) -> str | None:
    """A one-character confusable swap that check_model reports as ambiguous (not a match, not a mismatch)."""
    for i, c in enumerate(model):
        if c.upper() in SWAPS:
            candidate = model[:i] + SWAPS[c.upper()] + model[i + 1 :]
            facts = ClaimFacts(claim_id="00000000-0000-4000-8000-000000000000", model_claimed=candidate)
            if check_model(facts, rules.identification).verdict == "ambiguous":
                return candidate
    return None


def applicable(category: str, rules: ExtractedRules) -> bool:
    if category == "destruction_photo_intact":
        return destruction_required(rules.remedy)
    if category == "label_confirms_typed_typo":
        return any(typo(m.rstrip("*"), rules) for m in rules.identification.models)
    return True


def unreadable(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        small = rgb.resize(
            (max(1, rgb.width // UNREADABLE["downsample"]), max(1, rgb.height // UNREADABLE["downsample"]))
        )
        big = small.resize(rgb.size, Image.Resampling.NEAREST).filter(
            ImageFilter.GaussianBlur(UNREADABLE["blur_radius"])
        )
        out = io.BytesIO()
        big.save(out, format="JPEG", quality=UNREADABLE["jpeg_quality"])
    return out.getvalue()


def _relabel(claim: SynthClaim, model_text: str | None, attempt: int, blur: bool = False) -> None:
    claim.subtypes["label_model"] = model_text or ""
    claim.subtypes["label_blur"] = "yes" if blur else "no"
    rng = stream(SEED, claim.program_key, claim.index, f"probe-label-{attempt}")
    shown = SynthClaim(**{**claim.__dict__, "model_claimed": model_text})
    data = render_label(rng, draw_style(rng), _label_lines(shown, rng))
    if blur:
        data = unreadable(data)
    claim.photos = [p for p in claim.photos if p.kind != "label"] + [_photo("label", data)]


def _facts(c: SynthClaim) -> ClaimFacts:
    person = c.claimant
    return ClaimFacts(
        claim_id=c.claim_id,
        model_claimed=c.model_claimed,
        serial_claimed=c.serial_claimed,
        date_code_claimed=c.date_code_claimed,
        retailer_claimed=c.retailer_claimed,
        purchase_date=c.purchase_date,
        refund_cents=c.refund_cents,
        email=person.get("email"),
        address=" ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip")),
        payout_destination=person.get("payout_destination"),
        photos=tuple(PhotoRef(kind=p.kind, sha256=p.sha256, phash=p.phash) for p in c.photos),
    )


def build(out_dir: Path = ADJ_DIR, photo_dir: Path = PHOTO_DIR) -> dict[str, Any]:
    frozen = load_program_rules(out_dir / "program_rules.json")
    dates = {
        n.source_id: n.recall_date for n in load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json")
    }
    planted = {**load_yaml(out_dir / "planted.yaml"), "clean_share": 1.0}
    policy = load_yaml(out_dir / "outcome_policy.yaml")
    plan: dict[str, list[str]] = {p: [] for p in PROGRAMS}
    for category in CATEGORIES:
        eligible = [p for p in PROGRAMS if applicable(category, frozen[p])]
        for k in range(PER_CATEGORY):
            plan[eligible[k % len(eligible)]].append(category)
    records: list[dict[str, Any]] = []
    labels: dict[str, dict[str, Any]] = {}
    for program in PROGRAMS:
        rules = frozen[program]
        key = f"probe:{program}"
        claims = generate_program(rules, key, len(plan[program]), SEED, planted, policy, dates[program])
        for claim, category in zip(claims, plan[program], strict=True):
            base = adjudicate(_facts(claim), rules, [], 10_000_000, MemoryTrace("rules_only"))
            if base.outcome != "approve":
                raise SystemExit(f"{claim.claim_id}: base claim is not clean ({base.reasons})")
            claim.subtypes = {"probe": category, "label_model": claim.model_claimed or "", "label_blur": "no"}
            if category == "label_shows_other_model":
                _relabel(claim, sibling_model(rules.identification.models, stream(SEED, key, claim.index, "sib")), 0)
            elif category == "destruction_photo_intact":
                rng = stream(SEED, key, claim.index, "probe-destruction")
                family = instruction_family(rules.remedy.destruction_instruction)
                intact = render_destruction(rng, draw_style(rng), family, False)
                claim.photos = [p for p in claim.photos if p.kind != "destruction"] + [_photo("destruction", intact)]
            elif category == "label_unreadable":
                _relabel(claim, claim.model_claimed, 0, blur=True)
            elif category == "label_confirms_typed_typo":
                correct = claim.model_claimed or ""
                bad = typo(correct, rules)
                if bad is None:
                    correct = next(m.rstrip("*") for m in rules.identification.models if typo(m.rstrip("*"), rules))
                    bad = typo(correct, rules)
                _relabel(claim, correct, 0)
                claim.model_claimed = bad
            elif category == "label_confirms_missing_typed_model":
                _relabel(claim, claim.model_claimed, 0)
                claim.model_claimed = None
            claim.expected_outcome = (
                str(policy["photo_rules"][category]) if category != "clean" else str(policy["clean"])
            )
        _separate(claims)
        for claim in claims:
            facts = _facts(claim)
            hits = check_duplicates(facts, [_facts(o) for o in claims])
            if hits.hits:
                raise SystemExit(f"{claim.claim_id}: unplanted duplicate hit {hits.hits[0]}")
            for p in claim.photos:
                path = photo_dir / key.replace(":", "_") / f"{claim.claim_id}_{p.kind}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(p.data)
            record = {
                "claim_id": str(claim.claim_id),
                "program": program,
                "population": key,
                "model_claimed": claim.model_claimed,
                "serial_claimed": claim.serial_claimed,
                "date_code_claimed": claim.date_code_claimed,
                "retailer_claimed": claim.retailer_claimed,
                "purchase_date": claim.purchase_date.isoformat() if claim.purchase_date else None,
                "refund_cents": claim.refund_cents,
                "remedy": claim.remedy,
                "claimant": claim.claimant,
                "photos": [
                    {
                        "kind": p.kind,
                        "sha256": p.sha256,
                        "phash": p.phash,
                        "path": f"data/generated/probe_photos/{key.replace(':', '_')}/{claim.claim_id}_{p.kind}.jpg",
                    }
                    for p in claim.photos
                ],
            }
            records.append(record)
            labels[str(claim.claim_id)] = {
                "claim_id": str(claim.claim_id),
                "program": program,
                "category": claim.subtypes["probe"],
                "expected_outcome": claim.expected_outcome,
                "violations": [] if claim.subtypes["probe"] == "clean" else [claim.subtypes["probe"]],
                "label_model_printed": claim.subtypes["label_model"],
                "label_unreadable": claim.subtypes["label_blur"] == "yes",
                "subtypes": {},
            }
    labels_dir = out_dir / "probe_labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    for stale in labels_dir.glob("*.json"):
        stale.unlink()
    for cid, label in labels.items():
        (labels_dir / f"{cid}.json").write_text(json.dumps(label, indent=1) + "\n", encoding="utf-8")
    payload = {
        "seed": SEED,
        "block": "probe60",
        "unreadable_render": UNREADABLE,
        "programs": list(PROGRAMS),
        "claims": records,
    }
    (out_dir / "probe_claims.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return {
        "claims": len(records),
        "categories": dict(Counter(lab["category"] for lab in labels.values())),
        "per_program": {p: len(plan[p]) for p in PROGRAMS},
        "expected": dict(Counter(lab["expected_outcome"] for lab in labels.values())),
    }


def _separate(claims: list[SynthClaim]) -> None:
    """Re-render a re-labelled photo that lands within the duplicate threshold of another claim's label."""
    from recallops.fraud.phash import hamming

    for attempt in range(1, 6):
        moved = False
        for i, a in enumerate(claims):
            la = next(p for p in a.photos if p.kind == "label")
            for j, b in enumerate(claims):
                if j <= i:
                    continue
                lb = next(p for p in b.photos if p.kind == "label")
                if hamming(la.phash, lb.phash) <= 10:
                    _relabel(b, b.subtypes["label_model"] or None, attempt, blur=b.subtypes["label_blur"] == "yes")
                    moved = True
        if not moved:
            return


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(build(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
