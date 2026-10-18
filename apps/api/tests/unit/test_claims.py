"""Pure-code tools, the rules-only decision table, pHash, and the synthetic generator."""

from __future__ import annotations

import io
import json
import random
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

from recallops.claims.rules_only import adjudicate
from recallops.claims.tools import (
    ClaimFacts,
    PhotoRef,
    check_date_code,
    check_destruction_presence,
    check_duplicates,
    check_model,
    check_purchase,
    check_serial,
)
from recallops.claims.tools.checks import date_key, serial_in_range
from recallops.evals.golden import load_golden, load_program_rules
from recallops.fraud.phash import hamming, phash_bytes
from recallops.rules.schemas import (
    DateCodeRule,
    ExtractedRules,
    IdentificationRules,
    RemedyOption,
    RemedyRules,
    SerialRange,
)
from recallops.settings import REPO_ROOT
from recallops.synth.generate import expected_outcome, generate_program, load_yaml, serial_outside
from recallops.synth.render import draw_style, render_label
from recallops.tracing.writer import MemoryTrace

ADJ = REPO_ROOT / "evals" / "adjudication"


def facts(**kwargs: object) -> ClaimFacts:
    base: dict[str, object] = {"claim_id": uuid.uuid4()}
    base.update(kwargs)
    return ClaimFacts.model_validate(base)


def ident(**kwargs: object) -> IdentificationRules:
    return IdentificationRules.model_validate(kwargs)


# models


@pytest.mark.parametrize(
    ("claimed", "verdict"),
    [
        ("TL1302Z", "match"),
        ("tl-1302 z", "match"),
        ("TL13O2Z", "ambiguous"),
        ("TL1302Y", "mismatch"),
        (None, "not_provided"),
        ("FEFBZ90GC12", "match"),
    ],
)
def test_check_model(claimed: str | None, verdict: str) -> None:
    rules = ident(models=["TL1302Z", "FEFBZ90GC*"])
    assert check_model(facts(model_claimed=claimed), rules).verdict == verdict


def test_check_model_skipped_without_rules() -> None:
    assert check_model(facts(model_claimed="X1"), ident()).verdict == "skipped"


# serials


@pytest.mark.parametrize(
    ("serial", "start", "end", "inside"),
    [
        ("VF34618200", "VF34618193", "VF34618322", True),
        ("VF34618400", "VF34618193", "VF34618322", False),
        ("VF3461820", "VF34618193", "VF34618322", False),  # wrong length
        ("700KM1234567", "607KMXXXXXXX", "807KMXXXXXXX", True),
        ("102KM1234567", "607KMXXXXXXX", "206KMXXXXXXX", True),  # wrap: year-digit serial
        ("450KM1234567", "607KMXXXXXXX", "206KMXXXXXXX", False),
        ("S8000", "S73202", "S8442", True),  # lexicographic, as printed by the notice
        ("C50000", "C3753", "C77735", True),
    ],
)
def test_serial_in_range(serial: str, start: str, end: str, inside: bool) -> None:
    assert serial_in_range(serial, start, end) is inside


def test_check_serial_prefix_and_verdicts() -> None:
    rules = ident(serial_ranges=[SerialRange(prefix="ET22", raw="begin with ET22")])
    assert check_serial(facts(serial_claimed="et22-0001"), rules).verdict == "in_range"
    assert check_serial(facts(serial_claimed="XX220001"), rules).verdict == "out_of_range"
    assert check_serial(facts(serial_claimed=None), rules).verdict == "not_provided"
    assert check_serial(facts(serial_claimed="A1"), ident()).verdict == "skipped"


def test_generator_builds_serials_outside_every_range() -> None:
    rules = load_program_rules(ADJ / "program_rules.json")["25126"]  # 20 ranges, several wrapping
    for seed in range(20):
        serial = serial_outside(rules, random.Random(seed))
        assert serial is not None
        assert check_serial(facts(serial_claimed=serial), rules.identification).verdict == "out_of_range"


# date codes


def test_date_codes() -> None:
    mmyy = ident(date_codes=[DateCodeRule(format="MM/YY", start="07/22", end="08/22", raw="07/22 or 08/22")])
    assert check_date_code(facts(date_code_claimed="08/22"), mmyy).verdict == "in_range"
    assert check_date_code(facts(date_code_claimed="09/22"), mmyy).verdict == "out_of_range"
    prior = ident(
        date_codes=[DateCodeRule(format="Month YYYY", end="July 2025", raw="manufactured prior to July 2025")]
    )
    assert check_date_code(facts(date_code_claimed="June 2025"), prior).verdict == "in_range"
    assert check_date_code(facts(date_code_claimed="July 2025"), prior).verdict == "out_of_range"
    opaque = ident(date_codes=[DateCodeRule(format="date code", start="2126", end="2319", raw="2126 to 2319")])
    assert check_date_code(facts(date_code_claimed="2200"), opaque).verdict == "in_range"
    assert check_date_code(facts(date_code_claimed="2400"), opaque).verdict == "out_of_range"
    assert check_date_code(facts(date_code_claimed="soon"), mmyy).verdict == "unparsed"
    assert check_date_code(facts(), mmyy).verdict == "not_provided"
    assert date_key("2024", "model year") == (2024, 0)


# purchase and destruction


def _rules(**remedy: object) -> ExtractedRules:
    return ExtractedRules(
        identification=ident(
            sold_at=["Amazon.com", "The Home Depot"], sold_from=date(2024, 1, 1), sold_to=date(2024, 12, 31)
        ),
        remedy=RemedyRules.model_validate(remedy),
    )


def test_check_purchase() -> None:
    rules = _rules(options=[RemedyOption(type="refund", proof_required=["receipt"])])
    ok = check_purchase(
        facts(
            retailer_claimed="amazon",
            purchase_date=date(2024, 6, 1),
            photos=(PhotoRef(kind="receipt", sha256="a", phash="0" * 16),),
        ),
        rules,
    )
    assert (ok.retailer, ok.window, ok.receipt) == ("listed", "inside", "present")
    bad = check_purchase(facts(retailer_claimed="Corner Hardware Co", purchase_date=date(2023, 6, 1)), rules)
    assert (bad.retailer, bad.window, bad.receipt) == ("not_listed", "before", "missing")
    assert check_purchase(facts(purchase_date=date(2025, 2, 1)), _rules()).window == "after"


def test_destruction_presence() -> None:
    remedy = RemedyRules(destruction_instruction="cut the cord")
    label = PhotoRef(kind="label", sha256="l", phash="00000000000000ff")
    assert check_destruction_presence(facts(photos=(label,)), remedy).verdict == "missing"
    reupload = PhotoRef(kind="destruction", sha256="l", phash="00000000000000ff")
    assert check_destruction_presence(facts(photos=(label, reupload)), remedy).verdict == "missing"
    real = PhotoRef(kind="destruction", sha256="d", phash="ffffffffffff0000")
    assert check_destruction_presence(facts(photos=(label, real)), remedy).verdict == "present"
    assert check_destruction_presence(facts(), RemedyRules()).verdict == "not_required"


# duplicates


def test_duplicates_by_kind() -> None:
    a = facts(
        photos=(
            PhotoRef(kind="label", sha256="x", phash="ffff000000000000"),
            PhotoRef(kind="receipt", sha256="r1", phash="0000000000000000"),
        ),
        address="1 Elm St",
        email="a@example.com",
        serial_claimed="S1",
        payout_destination="d1",
    )
    b = facts(
        photos=(
            PhotoRef(kind="label", sha256="y", phash="ffff000000000003"),
            PhotoRef(kind="receipt", sha256="r2", phash="0000000000000001"),
        ),
        address="1 elm st",
        serial_claimed="s-1",
        payout_destination="d1",
    )
    kinds = sorted(h.kind for h in check_duplicates(a, [a, b]).hits)
    assert kinds == ["same_address", "same_payout", "same_photo", "same_serial"]
    assert all(h.detail != "receipt phash 1" for h in check_duplicates(a, [b]).hits)
    c = facts(email="A@example.com")
    assert [h.kind for h in check_duplicates(facts(email="a@example.com"), [c]).hits] == ["same_address"]


# rules-only decision table against the policy


def test_decision_table_follows_policy_precedence() -> None:
    policy = load_yaml(ADJ / "outcome_policy.yaml")
    assert expected_outcome([], policy) == "approve"
    assert expected_outcome(["retailer_not_listed", "wrong_model"], policy) == "deny"
    assert expected_outcome(["wrong_model", "duplicate_serial"], policy) == "flag"
    rules = ExtractedRules(
        identification=ident(
            models=["SE4570"], sold_at=["Grainger"], sold_from=date(2022, 9, 1), sold_to=date(2023, 9, 30)
        ),
        remedy=RemedyRules(remedy_type="replacement"),
    )
    clean = facts(model_claimed="SE4570", retailer_claimed="Grainger", purchase_date=date(2023, 1, 1), refund_cents=500)
    cases = {
        "approve": clean,
        "deny": clean.model_copy(update={"model_claimed": "SE4571"}),
        "needs_info": clean.model_copy(update={"retailer_claimed": "Elsewhere"}),
    }
    for outcome, claim in cases.items():
        trace = MemoryTrace("rules_only")
        result = adjudicate(claim, rules, [claim], 10_000, trace)
        assert result.outcome == outcome
        assert [s.node for s in trace.steps][-1] == "decide" and len(trace.steps) == 7
    twin = clean.model_copy(update={"claim_id": uuid.uuid4(), "serial_claimed": None, "payout_destination": "p"})
    first = clean.model_copy(update={"payout_destination": "p"})
    assert adjudicate(first, rules, [first, twin], 10_000, MemoryTrace("rules_only")).outcome == "flag"
    pricey = clean.model_copy(update={"refund_cents": 50_000})
    assert adjudicate(pricey, rules, [pricey], 10_000, MemoryTrace("rules_only")).routed_to_human


def test_adjudicator_never_reads_ground_truth() -> None:
    source = "\n".join(p.read_text() for p in (REPO_ROOT / "apps/api/src/recallops/claims").rglob("*.py"))
    assert not re.search(r"labels/|expected_outcome|sidecar|violations", source)


# pHash and rendering


def test_phash_identity_near_and_far() -> None:
    rng = random.Random(1)
    a = render_label(rng, draw_style(rng), ["MODEL AB-1", "SER 123"])
    with Image.open(io.BytesIO(a)) as image:
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=60)
    near = out.getvalue()
    rng2 = random.Random(99)
    far = render_label(rng2, draw_style(rng2), ["MODEL ZZ-9", "SER 999"])
    assert hamming(phash_bytes(a), phash_bytes(a)) == 0
    assert hamming(phash_bytes(a), phash_bytes(near)) <= 6
    assert hamming(phash_bytes(a), phash_bytes(far)) > 10


# synthetic generator


@pytest.fixture(scope="module")
def program_claims() -> list:  # type: ignore[type-arg]
    golden = {n.source_id: n for n in load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json")}
    planted, policy = load_yaml(ADJ / "planted.yaml"), load_yaml(ADJ / "outcome_policy.yaml")
    rules = load_program_rules(ADJ / "program_rules.json")["24118"]
    recall_date = golden["24118"].recall_date
    return [generate_program(rules, "24118", 12, 7, planted, policy, recall_date) for _ in range(2)]


def test_generator_is_deterministic(program_claims: list) -> None:  # type: ignore[type-arg]
    first, second = program_claims
    assert [c.claim_id for c in first] == [c.claim_id for c in second]
    assert [[p.sha256 for p in c.photos] for c in first] == [[p.sha256 for p in c.photos] for c in second]
    assert [c.expected_outcome for c in first] == [c.expected_outcome for c in second]


def test_synthetic_claimants_use_reserved_values(program_claims: list) -> None:  # type: ignore[type-arg]
    for claim in program_claims[0]:
        person = claim.claimant
        assert person["synthetic"] is True
        assert person["email"].split("@")[1] in {"example.com", "example.net", "example.org"}
        assert re.fullmatch(r"\(\d{3}\) 555-01\d{2}", person["phone"])
        assert person["payout_destination"].startswith("synth_dest_")


def test_committed_golden_claims_match_their_labels() -> None:
    payload = json.loads((ADJ / "golden_claims.json").read_text())
    assert len(payload["claims"]) == 300
    sample = payload["claims"][0]
    assert "violations" not in sample and "expected_outcome" not in sample
    label = json.loads((ADJ / "labels" / f"{sample['claim_id']}.json").read_text())
    assert label["expected_outcome"] in {"approve", "deny", "needs_info", "flag"}
    assert Path(ADJ / "labels").is_dir() and len(list((ADJ / "labels").glob("*.json"))) == 300
