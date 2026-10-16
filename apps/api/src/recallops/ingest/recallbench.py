"""Recall Bench comparison set: sample 30 rows inside the API archive window and compare them with
official API records (recall number, date, canonicalized title). Polars for the tabular work.

python -m recallops.ingest.recallbench \
    --csv data/raw/recallbench/cpsc-recalls-full.csv --archive data/raw/cpsc
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import polars as pl

from recallops.evals.sampling import load_archive
from recallops.ingest.cpsc_api import ParsedRecall, canonical_recall_number
from recallops.text import clean

SAMPLE_N = 30
SEED = 20261016
STOP_RATE = 0.02


@dataclass
class Comparison:
    sampled: int
    missing_in_api: int
    number_mismatch: int
    date_mismatch: int
    title_mismatch: int
    rows_with_any_mismatch: int
    mismatch_rate: float
    window_start: str
    window_end: str


def norm_title(value: str | None) -> str:
    return re.sub(r"\s+", " ", clean(value)).strip().casefold()


def load_csv(path: Path) -> pl.DataFrame:
    frame = pl.read_csv(path, infer_schema=False)
    return frame.with_columns(
        pl.col("recall_number").map_elements(canonical_recall_number, return_dtype=pl.String).alias("rn"),
        pl.col("recall_date").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).alias("rdate"),
    )


def compare(
    frame: pl.DataFrame, api: dict[str, ParsedRecall], start: date, end: date
) -> tuple[Comparison, pl.DataFrame]:
    window = frame.filter((pl.col("rdate") >= start) & (pl.col("rdate") <= end)).sort("rn")
    sample = window.sample(n=min(SAMPLE_N, window.height), seed=SEED)
    rows = []
    for row in sample.iter_rows(named=True):
        rec = api.get(row["rn"])
        rows.append(
            {
                "recall_number": row["rn"],
                "in_api": rec is not None,
                "number_ok": rec is not None,
                "date_ok": rec is not None and rec.recall_date == row["rdate"],
                "title_ok": rec is not None and norm_title(rec.title) == norm_title(row["title"]),
            }
        )
    detail = pl.from_dicts(rows)
    any_bad = detail.filter(~(pl.col("number_ok") & pl.col("date_ok") & pl.col("title_ok"))).height
    result = Comparison(
        sampled=detail.height,
        missing_in_api=detail.filter(~pl.col("in_api")).height,
        number_mismatch=detail.filter(~pl.col("number_ok")).height,
        date_mismatch=detail.filter(~pl.col("date_ok")).height,
        title_mismatch=detail.filter(~pl.col("title_ok")).height,
        rows_with_any_mismatch=any_bad,
        mismatch_rate=round(any_bad / detail.height, 4) if detail.height else 0.0,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )
    return result, detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)
    records = load_archive(args.archive)
    api = {r.source_id: r for r in records}
    start = min(r.recall_date for r in records)
    end = max(r.recall_date for r in records)
    result, detail = compare(load_csv(args.csv), api, start, end)
    print(json.dumps(asdict(result)))
    print(detail.filter(~(pl.col("date_ok") & pl.col("title_ok"))).write_json())
    return 0 if result.mismatch_rate <= STOP_RATE else 2


if __name__ == "__main__":
    sys.exit(main())
