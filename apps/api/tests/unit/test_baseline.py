"""Regex baseline on the 15 baseline-test golden notices (the owner's subset; PREREG.md), plus behaviors.

Known misses are listed explicitly so a regression or an accidental improvement both show up.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recallops.evals.golden import GoldenNotice, load_golden, notice_text
from recallops.evals.metrics import HEADLINE_FIELDS, field_score, score, span_grounding
from recallops.rules.baseline import extract
from recallops.settings import REPO_ROOT

GOLDEN = REPO_ROOT / "evals" / "extraction" / "golden_notices.json"
NOTICES = REPO_ROOT / "evals" / "extraction" / "notices"

# (source_id, field): documented limits of the regex baseline on the test subset.
KNOWN_MISSES = {
    ("24149", "models"),  # the recalled PA-10 is not introduced by "model"; bundled console models are
    ("26205", "models"),  # the label keeps "KABI-0176 G062401IC" as one printed string
    # The 2026-10-18 label audit removed generic dealer and supplier networks from sold_at (evals/extraction/
    # audit_log.jsonl); baseline 1.0.0 is frozen and still extracts them. The label changed, not the code.
    ("24124", "sold_at"),
    ("26027", "sold_at"),
}


def _test_notices() -> list[GoldenNotice]:
    return [n for n in load_golden(Path(GOLDEN)) if n.baseline_test]


TEST_NOTICES = _test_notices()


def test_subset_is_the_preregistered_fifteen() -> None:
    assert len(TEST_NOTICES) == 15


@pytest.mark.parametrize("notice", TEST_NOTICES, ids=lambda n: n.source_id)
def test_baseline_matches_gold_on_test_notice(notice: GoldenNotice) -> None:
    text = notice_text(Path(NOTICES), notice.source_id)
    pred = extract(text)
    for field in HEADLINE_FIELDS:
        expected_miss = (notice.source_id, field) in KNOWN_MISSES
        assert (field_score(field, notice.rules, pred) < 1.0) == expected_miss, field
    grounded, total = span_grounding(pred, text)
    assert grounded == total


def test_headline_on_test_subset() -> None:
    pairs = [(n.rules, extract(t), t) for n in TEST_NOTICES for t in [notice_text(Path(NOTICES), n.source_id)]]
    table = score(pairs)
    # 98.8 on the M1 labels (frozen there); 97.6 on the audited labels, the two new KNOWN_MISSES above.
    assert table.headline >= 97.6
    assert table.span_grounded_rate == 1.0


SYNTHETIC = """TITLE:
Acme Recalls Widgets

DESCRIPTION:
This recall involves Acme widgets with model numbers AB-1234, CD5678 and EF9012Z. Serial numbers beginning with "K7" are included. The model number is printed on a sticker on the bottom of the widget. The widgets were manufactured between March 2024 and May 2024.

PRODUCTS:
Acme Widgets; units: About 1.2 million (In addition, about 4,000 were sold in Canada)

SOLD AT:
Target, Walmart and other stores nationwide and online at Acme.com from January 2024 through February 2025 for between $19.99 and $25.

REMEDY:
Consumers should stop using the widgets and contact Acme for a full refund. Consumers should cut the cord and register at https://acme.example/recall to submit a photo.
Remedy options: Refund

CONTACT:
Acme at 800-555-0100 from 9 a.m. to 5 p.m. ET.
"""


def test_behaviors_on_synthetic_notice() -> None:
    rules = extract(SYNTHETIC)
    ident, remedy = rules.identification, rules.remedy
    assert ident.models == ["AB-1234", "CD5678", "EF9012Z"]
    assert [s.prefix for s in ident.serial_ranges] == ["K7"]
    assert ident.model_label_location == "printed on a sticker on the bottom of the widget"
    assert [(d.start, d.end) for d in ident.date_codes] == [("March 2024", "May 2024")]
    assert ident.sold_at == ["Target", "Walmart", "Acme.com"]
    assert (ident.sold_from, ident.sold_to) == (date(2024, 1, 1), date(2025, 2, 28))
    assert ident.price_range_cents is not None
    assert (ident.price_range_cents.min_cents, ident.price_range_cents.max_cents) == (1999, 2500)
    assert ident.units_affected == 1_200_000
    assert remedy.remedy_type == "refund"
    assert remedy.destruction_instruction is not None and "cut the cord" in remedy.destruction_instruction
    assert remedy.registration_url == "https://acme.example/recall"
    assert remedy.contact == "800-555-0100"
    assert remedy.options[0].proof_required == ["destruction_photo", "registration"]


def test_empty_notice_extracts_nothing() -> None:
    rules = extract("TITLE:\nNothing\n")
    assert rules.identification.models == []
    assert rules.remedy.remedy_type is None
    assert rules.identification.sold_from is None
