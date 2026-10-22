"""The question a claimant reads for a rules-only needs_info (visual audit, 2026-10-22): the frozen checker's reasons
reached the portal verbatim, "retailer not_listed; proof of purchase needed" among them."""

from __future__ import annotations

import re

import pytest

from recallops.claims.wording import FALLBACK, claimant_question


@pytest.mark.parametrize(
    ("reasons", "question"),
    [
        (
            ["retailer not_listed; proof of purchase needed"],
            "To finish your claim, please send the store or website where you bought it and a photo of your receipt or "
            "order confirmation.",
        ),
        (
            ["retailer not_listed; proof of purchase needed", "receipt required"],  # the receipt is asked for once
            "To finish your claim, please send the store or website where you bought it and a photo of your receipt or "
            "order confirmation.",
        ),
        (["receipt required"], "To finish your claim, please send a photo of your receipt or order confirmation."),
        (
            ["destruction photo needed: destruction photo duplicates the label photo", "receipt required"],
            "To finish your claim, please send a photo of the product after you disable it the way the recall notice "
            "says and a photo of your receipt or order confirmation.",
        ),
        (
            ["model ambiguous", "serial number needed", "date code missing", "purchase date after"],
            "To finish your claim, please send the model number printed on the product's label, the serial number "
            "printed on the product, the date code printed on the product, and the date you bought it.",
        ),
        (
            ["the label photo is unreadable; a clearer photo is needed"],
            "To finish your claim, please send a clearer photo of the product's label.",
        ),
        (
            ["receipt required", "receipt required"],
            "To finish your claim, please send a photo of your receipt or order confirmation.",
        ),
        (["something new the checker learns later"], FALLBACK),
        ([], FALLBACK),
    ],
)
def test_every_reason_is_worded_for_the_claimant(reasons: list[str], question: str | None) -> None:
    worded = claimant_question(reasons)
    if question is not None:
        assert worded == question
    assert not re.search(r"[a-z]+_[a-z]+", worded)  # no internal token ever reaches a claimant
    assert "not_listed" not in worded and ";" not in worded


def test_the_seed_and_the_worker_use_the_same_wording() -> None:
    from recallops import seed

    assert seed._question(["receipt required"]) == claimant_question(["receipt required"])
