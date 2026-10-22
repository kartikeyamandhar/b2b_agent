"""The question a claimant reads when the rules-only checker needs more (visual audit, 2026-10-22). The checker's
reasons are frozen with the ablation and stay in the decision as they are ("retailer not_listed; proof of purchase
needed"); the portal showed them verbatim. Each reason is worded here as something the claimant can send, in the order
the checker gave them, and a reason with no wording falls back to a plain request, never to the raw text."""

from __future__ import annotations

import re
from collections.abc import Iterable

# (pattern on the checker's reason, what to ask for). First match wins, so the more specific patterns come first.
ASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^the label photo is unreadable"), "a clearer photo of the product's label"),
    (re.compile(r"^the label's model number"), "a clearer photo of the product's label"),
    (re.compile(r"^model\b"), "the model number printed on the product's label"),
    (re.compile(r"^serial number"), "the serial number printed on the product"),
    (re.compile(r"^date code"), "the date code printed on the product"),
    (re.compile(r"^purchase date"), "the date you bought it"),
    (re.compile(r"^retailer\b"), "the store or website where you bought it"),
    (re.compile(r"^(?:receipt|proof of purchase)"), "a photo of your receipt or order confirmation"),
    (re.compile(r"destruction"), "a photo of the product after you disable it the way the recall notice says"),
)
FALLBACK = "To finish your claim, the company needs a little more information. Reply here with what you can send."


def _join(items: list[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def claimant_question(reasons: Iterable[str]) -> str:
    asks: list[str] = []
    # One reason can hold two asks ("retailer not_listed; proof of purchase needed"); a repeated ask is asked once.
    parts = [p.strip().lower() for reason in reasons for p in reason.split(";") if p.strip()]
    for part in parts:
        ask = next((a for pattern, a in ASKS if pattern.search(part)), None)
        if ask is not None and ask not in asks:
            asks.append(ask)
    if not asks:
        return FALLBACK
    return f"To finish your claim, please send {_join(asks)}."
