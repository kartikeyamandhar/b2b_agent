"""Seeded stratified sample of golden notices (evals/extraction/PREREG.md)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl

from recallops.ingest.cpsc_api import ParsedRecall, parse_record

SEED = 20261016
SUBSET_SEED = 20261017
POOL_START = date(2024, 1, 1)
POOL_END = date(2026, 10, 16)
TOTAL = 40
IDENTIFIER_RICH = 10

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "children": (
        "child",
        "children",
        "baby",
        "infant",
        "crib",
        "toddler",
        "nursery",
        "stroller",
        "high chair",
        "toy",
        "kids",
        "youth",
        "bassinet",
        "pacifier",
        "teether",
        "play yard",
        "playpen",
        "car seat",
    ),
    "furniture": (
        "dresser",
        "chest",
        "furniture",
        "cabinet",
        "table",
        "chair",
        "bed",
        "mattress",
        "shelf",
        "bookcase",
        "sofa",
        "ottoman",
        "nightstand",
        "bunk",
        "desk",
    ),
    "battery_power": (
        "battery",
        "batteries",
        "e-bike",
        "ebike",
        "scooter",
        "charger",
        "power bank",
        "lithium",
        "electric bike",
        "hoverboard",
        "power station",
    ),
    "appliance_electrical": (
        "heater",
        "oven",
        "toaster",
        "blender",
        "cooker",
        "fryer",
        "kettle",
        "coffee",
        "appliance",
        "dehumidifier",
        "humidifier",
        "fan",
        "lamp",
        "light",
        "extension cord",
        "outlet",
        "power strip",
        "refrigerator",
        "fridge",
        "microwave",
        "dryer",
        "washer",
        "laminator",
        "electric",
    ),
    "outdoor_sports": (
        "helmet",
        "bicycle",
        "bike",
        "atv",
        "rov",
        "utv",
        "off-road",
        "snowmobile",
        "boat",
        "kayak",
        "generator",
        "mower",
        "chainsaw",
        "trimmer",
        "grill",
        "ladder",
        "tent",
        "sport",
        "fitness",
        "treadmill",
        "golf",
        "motorcycle",
    ),
    "clothing_textile": (
        "pajama",
        "garment",
        "clothing",
        "hoodie",
        "sweatshirt",
        "jacket",
        "shirt",
        "robe",
        "gown",
        "blanket",
        "apparel",
    ),
}
_IDENTIFIER = re.compile(
    r"serial number|serial no|date code|lot number|lot code|manufactured (?:between|from|on|in)|"
    r"production date|manufacture date",
    re.IGNORECASE,
)


def categorize(title: str) -> str:
    lowered = title.lower()
    for category, words in CATEGORY_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(w) + r"s?\b", lowered) for w in words):
            return category
    return "other"


def load_archive(directory: Path) -> list[ParsedRecall]:
    records: list[ParsedRecall] = []
    for path in sorted(directory.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            records.append(parse_record(raw))
    return records


def _largest_remainder(counts: dict[str, int], total: int) -> dict[str, int]:
    population = sum(counts.values())
    if population == 0 or total == 0:
        return dict.fromkeys(counts, 0)
    quotas = {k: total * v / population for k, v in counts.items()}
    alloc = {k: min(counts[k], int(q)) for k, q in quotas.items()}
    order = sorted(counts, key=lambda k: (-(quotas[k] - int(quotas[k])), k))
    i = 0
    while sum(alloc.values()) < total and i < 10 * len(order):
        k = order[i % len(order)]
        if alloc[k] < counts[k]:
            alloc[k] += 1
        i += 1
    return alloc


def _draw(frame: pl.DataFrame, by: str, total: int, seed: int) -> pl.DataFrame:
    counts = dict(frame.group_by(by).len().sort(by).iter_rows())
    alloc = _largest_remainder({str(k): int(v) for k, v in counts.items()}, total)
    parts = []
    for cell, n in sorted(alloc.items()):
        if n:
            cell_frame = frame.filter(pl.col(by) == cell).sort("source_id")
            parts.append(cell_frame.sample(n=n, seed=seed))
    return pl.concat(parts) if parts else frame.head(0)


def build_pool(records: Sequence[ParsedRecall]) -> pl.DataFrame:
    rows = [
        {
            "source_id": r.source_id,
            "recall_date": r.recall_date,
            "year": str(r.recall_date.year),
            "title": r.title,
            "category": categorize(r.title),
            "identifier_rich": bool(_IDENTIFIER.search(r.source_text)),
        }
        for r in records
        if POOL_START <= r.recall_date <= POOL_END
    ]
    return pl.from_dicts(rows).sort("source_id")


def draw_sample(pool: pl.DataFrame) -> pl.DataFrame:
    rich = _draw(pool.filter(pl.col("identifier_rich")), "year", IDENTIFIER_RICH, SEED)
    rest_pool = pool.filter(~pl.col("source_id").is_in(rich["source_id"].implode()))
    rest = _draw(rest_pool, "category", TOTAL - rich.height, SEED)
    return pl.concat([rich, rest]).sort("source_id")


def test_subset(source_ids: Sequence[str], n: int = 15) -> list[str]:
    frame = pl.DataFrame({"source_id": sorted(source_ids)})
    shuffled = frame.sample(fraction=1.0, shuffle=True, seed=SUBSET_SEED)
    return [str(x) for x in shuffled["source_id"].head(n).to_list()]


def permutation(source_ids: Sequence[str]) -> list[str]:
    frame = pl.DataFrame({"source_id": sorted(source_ids)})
    return [str(x) for x in frame.sample(fraction=1.0, shuffle=True, seed=SUBSET_SEED)["source_id"].to_list()]
