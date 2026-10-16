"""Canonical notice text (canonicalizer v1).

Every evidence span, golden label, and extractor reads source_text, so offsets stay valid. The cleaning
is deliberately small: unescape HTML entities once, drop tags and invisible formatting characters, turn
Unicode spaces into plain spaces, and collapse runs. Every other character (trademark signs, dashes,
quotes) is kept exactly as printed, because model numbers must survive verbatim.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Iterable, Mapping
from typing import Any

CANONICALIZER_VERSION = "v1"

_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
# Invisible formatting characters: soft hyphen, zero-width space/joiners, word joiner, BOM.
_INVISIBLE = dict.fromkeys([0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF])
# Space-like characters folded to a plain space: tab, NBSP, Ogham space, U+2000..U+200A,
# narrow NBSP, medium mathematical space, ideographic space.
_SPACE_CODEPOINTS = [0x20, 0x09, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]
_SPACES = re.compile("[" + "".join(re.escape(chr(c)) for c in _SPACE_CODEPOINTS) + "]+")
_BLANK_LINES = re.compile(r"\n{3,}")

SECTION_ORDER = (
    "TITLE",
    "DESCRIPTION",
    "PRODUCTS",
    "SOLD AT",
    "REMEDY",
    "HAZARD",
    "INCIDENTS",
    "FIRMS",
    "CONTACT",
    "UPC",
)


def clean(value: str | None) -> str:
    """Canonicalize one free-text field."""
    if not value:
        return ""
    text = html.unescape(value)
    text = _TAG.sub(" ", text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _names(items: Any, key: str) -> list[str]:
    out: list[str] = []
    if isinstance(items, Iterable) and not isinstance(items, str | bytes):
        for item in items:
            if isinstance(item, Mapping):
                text = clean(str(item.get(key) or ""))
                if text:
                    out.append(text)
    return out


def _product_lines(products: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(products, list):
        return lines
    for product in products:
        if not isinstance(product, Mapping):
            continue
        parts = [clean(str(product.get("Name") or ""))]
        for label, key in (("model", "Model"), ("type", "Type"), ("units", "NumberOfUnits")):
            value = clean(str(product.get(key) or ""))
            if value:
                parts.append(f"{label}: {value}")
        description = clean(str(product.get("Description") or ""))
        if description:
            parts.append(f"description: {description}")
        line = "; ".join(p for p in parts if p)
        if line:
            lines.append(line)
    return lines


def build_source_text(record: Mapping[str, Any]) -> str:
    """Compose the canonical source text for one CPSC API record, in a fixed section order."""
    sold_at = _names(record.get("Retailers"), "Name")
    sold_label = clean(str(record.get("SoldAtLabel") or ""))
    if sold_label:
        sold_at.append(sold_label)
    remedy = _names(record.get("Remedies"), "Name")
    options = _names(record.get("RemedyOptions"), "Option")
    if options:
        remedy.append("Remedy options: " + ", ".join(options))
    firms = [
        *(f"Manufacturer: {n}" for n in _names(record.get("Manufacturers"), "Name")),
        *(f"Importer: {n}" for n in _names(record.get("Importers"), "Name")),
        *(f"Distributor: {n}" for n in _names(record.get("Distributors"), "Name")),
    ]
    sections: dict[str, list[str]] = {
        "TITLE": [clean(str(record.get("Title") or ""))],
        "DESCRIPTION": [clean(str(record.get("Description") or ""))],
        "PRODUCTS": _product_lines(record.get("Products")),
        "SOLD AT": sold_at,
        "REMEDY": remedy,
        "HAZARD": _names(record.get("Hazards"), "Name"),
        "INCIDENTS": _names(record.get("Injuries"), "Name"),
        "FIRMS": firms,
        "CONTACT": [clean(str(record.get("ConsumerContact") or ""))],
        "UPC": _names(record.get("ProductUPCs"), "UPC"),
    }
    blocks = []
    for name in SECTION_ORDER:
        body = "\n".join(line for line in sections[name] if line)
        if body:
            blocks.append(f"{name}:\n{body}")
    return "\n\n".join(blocks) + "\n"


def section_bounds(source_text: str) -> dict[str, tuple[int, int]]:
    """Character range of each section body inside source_text."""
    bounds: dict[str, tuple[int, int]] = {}
    headers = [(m.start(), m.end(), m.group(1)) for m in re.finditer(r"(?m)^([A-Z ]+):\n", source_text)]
    for i, (_, body_start, name) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(source_text)
        bounds[name] = (body_start, end)
    return bounds


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_span(source_text: str, quote: str, start_hint: int = 0) -> tuple[int, int] | None:
    """Locate quote verbatim; prefer the first occurrence at or after start_hint."""
    if not quote:
        return None
    idx = source_text.find(quote, start_hint)
    if idx < 0:
        idx = source_text.find(quote)
    if idx < 0:
        return None
    return idx, idx + len(quote)
