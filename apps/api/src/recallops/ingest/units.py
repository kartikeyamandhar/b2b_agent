"""Parse CPSC NumberOfUnits text into a value plus a status. Unparsed is None, never 0."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from recallops.text import clean

UnitsStatus = Literal["ok", "ambiguous", "unparsed"]

_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_SCALE = {"million": 1_000_000, "thousand": 1_000}
# A quantity is digits (thousands separators allowed, with one stray space tolerated as in "17, 370"),
# or a number word only when a scale follows ("Two million"). Prices ($15,000) and product descriptors
# ("16-drawer") are not quantities.
_QUANTITY = re.compile(
    r"(?<![$\d.,\w])"
    r"(?:(?P<num>\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!-\w)\s*(?P<scale>million|thousand)?"
    r"|(?P<word>" + "|".join(_WORDS) + r")\s+(?P<wscale>million|thousand))\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"\.\s")


class UnitsParse(BaseModel):
    value: int | None
    status: UnitsStatus
    text: str


def _to_int(num: str, scale: str | None) -> int | None:
    word = _WORDS.get(num.lower())
    if word is not None:
        base = float(word)
    else:
        digits = num.replace(",", "").replace(" ", "")
        try:
            base = float(digits)
        except ValueError:
            return None
    multiplier = _SCALE.get((scale or "").lower(), 1)
    return round(base * multiplier)


def parse_units_text(raw: str | None) -> UnitsParse:
    """Parse one NumberOfUnits string. The leading quantity before any parenthetical is the US total."""
    text = clean(raw)
    head = _SENTENCE_END.split(text.split("(", 1)[0], maxsplit=1)[0]
    quantities = []
    for match in _QUANTITY.finditer(head):
        if match.group("word"):
            value = _to_int(match.group("word"), match.group("wscale"))
        else:
            value = _to_int(match.group("num"), match.group("scale"))
        if value is not None:
            quantities.append(value)
    if not quantities:
        return UnitsParse(value=None, status="unparsed", text=text)
    if len(set(quantities)) > 1:
        return UnitsParse(value=None, status="ambiguous", text=text)
    return UnitsParse(value=quantities[0], status="ok", text=text)


def parse_units(products: Sequence[Mapping[str, Any]] | None) -> UnitsParse:
    """Combine the NumberOfUnits of every product. Different values across products are ambiguous."""
    texts: list[str] = []
    for product in products or []:
        text = clean(str(product.get("NumberOfUnits") or ""))
        if text and text not in texts:
            texts.append(text)
    if not texts:
        return UnitsParse(value=None, status="unparsed", text="")
    parses = [parse_units_text(t) for t in texts]
    if len(parses) == 1:
        return parses[0]
    joined = " | ".join(texts)
    values = {p.value for p in parses}
    if all(p.status == "ok" for p in parses) and len(values) == 1:
        return UnitsParse(value=parses[0].value, status="ok", text=joined)
    return UnitsParse(value=None, status="ambiguous", text=joined)
