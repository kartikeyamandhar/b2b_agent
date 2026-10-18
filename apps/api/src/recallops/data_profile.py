"""Data profile: every number used to describe the data sources comes from here.

python -m recallops.data_profile            (writes evals/results/data_profile.json)

Content features (hazards, remedies, destruction steps, channels) are measured with keyword rules and the regex
baseline extractor, so they are estimates (the baseline scores about 93 on held-out golden notices); structural
features (field fill rates, counts, dates, units) are exact.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from recallops.evals.golden import load_golden
from recallops.evals.sampling import categorize
from recallops.ingest.cpsc_api import ParsedRecall, canonical_recall_number, parse_record
from recallops.rules import baseline
from recallops.settings import REPO_ROOT

HAZARDS = {
    "fire_burn": r"fire|burn|overheat|ignit|flame",
    "fall_crash_tipover": r"\bfall|crash|tip-?over|tip over",
    "entrapment_suffocation_strangulation": r"entrap|suffocat|strangul|asphyx",
    "lead_chemical_poisoning": r"lead|poison|chemical|carbon monoxide|toxic|bacteria",
    "ingestion_magnets_batteries": r"ingest|swallow",
    "laceration": r"lacerat|cut hazard",
    "choking": r"chok",
    "electric_shock": r"shock|electrocut",
    "drowning": r"drown",
}
RETAILERS = {
    "amazon": r"amazon",
    "walmart": r"walmart",
    "target": r"\btarget",
    "home_depot": r"home ?depot",
    "lowes": r"lowe",
    "temu": r"temu",
    "shein": r"shein",
    "tiktok": r"tiktok",
}
MPR_SNAPSHOTS = {
    "MPR": ("PRESS RELEASE NUMBER", date(2026, 9, 21)),
    "MPR 08.1.22 and Post 08.1.2 (2)": ("PRESS RELEASE LINK", date(2023, 6, 20)),
}


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _q(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _share(n: int, d: int) -> dict[str, float | int]:
    return {"n": n, "pct": round(100 * n / d, 1) if d else 0.0}


def load_raw(directory: Path) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        raw += json.loads(path.read_text(encoding="utf-8"))
    return raw


def profile_recalls(raw: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = [parse_record(r) for r in raw]
    n = len(raw)
    fields = {k: _share(sum(_filled(r.get(k)) for r in raw), n) for k in raw[0]}
    product_fields: Counter[str] = Counter()
    for r in raw:
        for p in r.get("Products") or []:
            product_fields.update(k for k, v in p.items() if _filled(v))
    dates = [p.recall_date for p in parsed]
    units = [p.units.value for p in parsed if p.units.value]
    countries = [[c["Country"] for c in r.get("ManufacturerCountries") or []] for r in raw]
    hazard_text = [" ".join(h["Name"] for h in r.get("Hazards") or []).lower() for r in raw]
    injury_text = [" ".join(i["Name"] for i in r.get("Injuries") or []).lower() for r in raw]
    remedy_text = [" ".join(p.remedies).lower() for p in parsed]
    sold_text = [" ".join(p.retailers).lower() for p in parsed]
    contact = [r.get("ConsumerContact") or "" for r in raw]
    phone = [bool(re.search(r"\d{3}-\d{3}-\d{4}", c)) for c in contact]
    photo = [bool(re.search(r"photo", t)) for t in remedy_text]
    destroy = [
        bool(re.search(r"destroy|cut (off|the|it)|cutting|dispose|throw away|write .?recall|disassembl", t))
        for t in remedy_text
    ]
    refund = [any(o["Option"] == "Refund" for o in r.get("RemedyOptions") or []) for r in raw]
    china = ["China" in c for c in countries]
    amazon_title = [
        bool(re.search(r"sold (exclusively )?on amazon|sold exclusively at amazon|on amazon\.com by", p.title.lower()))
        for p in parsed
    ]
    extracted = [baseline.extract(p.source_text) for p in parsed]
    prices = [
        (e.identification.price_range_cents.min_cents + e.identification.price_range_cents.max_cents) / 200
        for e in extracted
        if e.identification.price_range_cents
    ]
    windows = [
        (e.identification.sold_to - e.identification.sold_from).days
        for e in extracted
        if e.identification.sold_from
        and e.identification.sold_to
        and e.identification.sold_to >= e.identification.sold_from
    ]
    lags = [
        (p.recall_date - e.identification.sold_to).days
        for p, e in zip(parsed, extracted, strict=True)
        if e.identification.sold_to
    ]
    models_per = [len(e.identification.models) for e in extracted if e.identification.models]
    by_year: dict[str, dict[str, Any]] = {}
    for year in sorted({str(d.year) for d in dates}):
        idx = [k for k, d in enumerate(dates) if str(d.year) == year]
        by_year[year] = {
            "recalls": len(idx),
            **{
                name: round(100 * sum(arr[k] for k in idx) / len(idx), 1)
                for name, arr in (
                    ("pct_refund_option", refund),
                    ("pct_photo_in_remedy", photo),
                    ("pct_destroy_in_remedy", destroy),
                    ("pct_sold_on_amazon_title", amazon_title),
                    ("pct_made_in_china", china),
                )
            },
        }
    biggest = sorted(((p.units.value or 0, p.source_id, p.title) for p in parsed), reverse=True)[:5]
    return {
        "records": n,
        "date_range": [min(dates).isoformat(), max(dates).isoformat()],
        "by_year": by_year,
        "weekday": dict(Counter(d.strftime("%A") for d in dates).most_common()),
        "republished_after_recall_date": _share(
            sum(1 for r in raw if r.get("LastPublishDate", "")[:10] > r["RecallDate"][:10]), n
        ),
        "field_fill": fields,
        "product_subfield_fill": {k: _share(v, n) for k, v in product_fields.items()},
        "images_per_record_median": statistics.median(len(r.get("Images") or []) for r in raw),
        "description_chars": {
            "median": statistics.median(len(p.description) for p in parsed),
            "p90": _q([len(p.description) for p in parsed], 0.9),
            "max": max(len(p.description) for p in parsed),
        },
        "source_text_chars_median": statistics.median(len(p.source_text) for p in parsed),
        "units": {
            "status": dict(Counter(p.units.status for p in parsed)),
            "median": statistics.median(units),
            "p25": _q(units, 0.25),
            "p75": _q(units, 0.75),
            "p90": _q(units, 0.9),
            "total": sum(units),
            "buckets": dict(
                Counter(
                    "<1k"
                    if v < 1_000
                    else "1k-10k"
                    if v < 10_000
                    else "10k-100k"
                    if v < 100_000
                    else "100k-1M"
                    if v < 1_000_000
                    else ">=1M"
                    for v in units
                )
            ),
            "also_sold_in_canada": _share(sum(1 for p in parsed if "canada" in p.units.text.lower()), n),
            "largest": [{"units": u, "recall": s, "title": t} for u, s, t in biggest],
        },
        "remedy_options": dict(
            Counter(", ".join(sorted(o["Option"] for o in r.get("RemedyOptions") or [])) for r in raw).most_common(8)
        ),
        "remedy_requires_photo": _share(sum(photo), n),
        "remedy_requires_destroy_or_mark": _share(sum(destroy), n),
        "hazards": {k: _share(sum(1 for t in hazard_text if re.search(rx, t)), n) for k, rx in HAZARDS.items()},
        "injuries_none_reported": _share(sum(1 for t in injury_text if re.search(r"none reported|no injuries", t)), n),
        "deaths_mentioned": _share(sum(1 for t in injury_text if re.search(r"\bdeath|died|fatal", t)), n),
        "manufacturer_countries": dict(Counter(c for cs in countries for c in cs).most_common(6)),
        "retailer_mentions": {
            k: _share(sum(1 for s in sold_text if re.search(rx, s)), n) for k, rx in RETAILERS.items()
        },
        "title_sold_on_amazon": _share(sum(amazon_title), n),
        "contact": {
            "phone": _share(sum(phone), n),
            "email": _share(sum("@" in c for c in contact), n),
            "email_only": _share(sum("@" in c and not p for c, p in zip(contact, phone, strict=True)), n),
        },
        "title_category": dict(Counter(categorize(p.title) for p in parsed).most_common()),
        "baseline_measured": {
            "has_model_numbers": _share(sum(bool(e.identification.models) for e in extracted), n),
            "models_per_notice_median": statistics.median(models_per),
            "models_per_notice_max": max(models_per),
            "has_serial_rules": _share(sum(bool(e.identification.serial_ranges) for e in extracted), n),
            "has_date_codes": _share(sum(bool(e.identification.date_codes) for e in extracted), n),
            "has_destruction_instruction": _share(sum(bool(e.remedy.destruction_instruction) for e in extracted), n),
            "price_midpoint_usd": {
                "parsed": len(prices),
                "median": statistics.median(prices),
                "p25": _q(prices, 0.25),
                "p75": _q(prices, 0.75),
                "p90": _q(prices, 0.9),
            },
            "sale_window_days": {
                "parsed": len(windows),
                "median": statistics.median(windows),
                "p25": _q(windows, 0.25),
                "p75": _q(windows, 0.75),
                "over_5_years": sum(1 for w in windows if w > 5 * 365),
            },
            "days_from_end_of_sale_to_recall": {
                "median": statistics.median(lags),
                "p25": _q(lags, 0.25),
                "p75": _q(lags, 0.75),
            },
        },
    }


def _mpr_sheet(path: Path, name: str) -> pl.DataFrame:
    raw = pl.read_excel(path, sheet_name=name, has_header=False, infer_schema_length=0)
    header = next(
        i
        for i, row in enumerate(raw.iter_rows())
        if any(isinstance(v, str) and "PRESS RELEASE" in v.upper() for v in row)
    )
    body = raw.slice(header + 1)
    body.columns = [(" ".join(str(v).split()) if v is not None else f"col_{j}") for j, v in enumerate(raw.row(header))]
    return body


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _median_by(groups: dict[str, list[float]]) -> dict[str, dict[str, float | int]]:
    return {
        k: {"n": len(v), "median_rate": round(statistics.median(v), 3)}
        for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    }


def profile_mpr(path: Path, recalls: dict[str, tuple[ParsedRecall, dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (id_col, snapshot) in MPR_SNAPSHOTS.items():
        df = _mpr_sheet(path, name)
        rows = []
        for row in df.iter_rows(named=True):
            recalled, total = _num(row.get("NUMBER OF PRODUCTS RECALLED")), _num(row.get("TOTAL CORRECTED"))
            consumer = _num(row.get("NUMBER OF PRODUCTS CORRECTED CONSUMER LEVEL"))
            rows.append((canonical_recall_number(str(row[id_col])), recalled, total, consumer))
        usable = [r for r in rows if r[1] and r[2] is not None]
        rates = [min(r[2] / r[1], 5.0) for r in usable if r[1] and r[2] is not None]
        consumer_rates = [r[3] / r[1] for r in usable if r[1] and r[3] is not None]
        groups: dict[str, dict[str, list[float]]] = {
            "remedy": defaultdict(list),
            "category": defaultdict(list),
            "size": defaultdict(list),
            "age": defaultdict(list),
        }
        joined = 0
        for rid, recalled, total, _ in usable:
            if rid not in recalls or not recalled or total is None:
                continue
            joined += 1
            parsed, record = recalls[rid]
            rate = min(total / recalled, 5.0)
            groups["remedy"]["+".join(sorted(o["Option"] for o in record.get("RemedyOptions") or []))].append(rate)
            groups["category"][categorize(parsed.title)].append(rate)
            groups["size"][
                "<1k" if recalled < 1e3 else "1k-10k" if recalled < 1e4 else "10k-100k" if recalled < 1e5 else ">=100k"
            ].append(rate)
            age = (snapshot - parsed.recall_date).days
            groups["age"][
                "0-180d" if age < 180 else "180-365d" if age < 365 else "1-2y" if age < 730 else ">2y"
            ].append(rate)
        out[name] = {
            "snapshot_date": snapshot.isoformat(),
            "rows": df.height,
            "rows_with_numbers": len(usable),
            "joined_to_archive": joined,
            "total_correction_rate": {
                "median": round(statistics.median(rates), 3),
                "p25": round(_q(rates, 0.25), 3),
                "p75": round(_q(rates, 0.75), 3),
                "mean": round(statistics.mean(rates), 3),
                "under_10pct": sum(1 for r in rates if r < 0.1),
            },
            "consumer_level_rate_median": round(statistics.median(consumer_rates), 3),
            "median_rate_by": {k: _median_by(v) for k, v in groups.items()},
        }
    ids = [
        {canonical_recall_number(str(x)) for x in _mpr_sheet(path, name)[col].to_list()}
        for name, (col, _) in MPR_SNAPSHOTS.items()
    ]
    out["recalls_in_both_snapshots"] = len(ids[0] & ids[1])
    third = _mpr_sheet(path, "RecallsBetween 3.1.24 - 9.19.24")
    out["recall_list_sheet_rows"] = third.height
    return out


def profile_recallbench(path: Path) -> dict[str, Any]:
    frame = pl.read_csv(path, infer_schema=False)
    day = frame["recall_date"].str.slice(0, 10)
    return {
        "rows": frame.height,
        "columns": frame.columns,
        "date_range": [day.min(), day.max()],
        "rows_since_2022_08_01": int((day >= "2022-08-01").sum()),
        "by_decade": dict(sorted(Counter(d[:3] + "0s" for d in day.to_list() if d).items())),
    }


def _count(values: Iterable[bool]) -> int:
    return sum(1 for v in values if v)


def profile_golden() -> dict[str, Any]:
    notices = load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json")
    ident = [n.rules.identification for n in notices]
    remedy = [n.rules.remedy for n in notices]
    claims = json.loads((REPO_ROOT / "evals" / "adjudication" / "golden_claims.json").read_text())["claims"]
    labels = [
        json.loads((REPO_ROOT / "evals" / "adjudication" / "labels" / f"{c['claim_id']}.json").read_text())
        for c in claims
    ]
    return {
        "notices": {
            "count": len(notices),
            "label_status": notices[0].label_status,
            "years": dict(Counter(str(n.recall_date.year) for n in notices)),
            "categories": dict(Counter(n.category for n in notices).most_common()),
            "with_models": _count(bool(i.models) for i in ident),
            "models_total": sum(len(i.models) for i in ident),
            "with_serial_rules": _count(bool(i.serial_ranges) for i in ident),
            "serial_rules_total": sum(len(i.serial_ranges) for i in ident),
            "with_date_codes": _count(bool(i.date_codes) for i in ident),
            "retailers_total": sum(len(i.sold_at) for i in ident),
            "remedy_types": dict(Counter(str(r.remedy_type) for r in remedy)),
            "with_destruction_instruction": _count(bool(r.destruction_instruction) for r in remedy),
            "with_registration_url": _count(bool(r.registration_url) for r in remedy),
            "evidence_spans": sum(len(i.evidence) + len(r.evidence) for i, r in zip(ident, remedy, strict=True)),
        },
        "claims": {
            "count": len(claims),
            "programs": len({c["program"] for c in claims}),
            "expected_outcomes": dict(Counter(lab["expected_outcome"] for lab in labels)),
            "violations_per_claim": dict(sorted(Counter(len(lab["violations"]) for lab in labels).items())),
            "violations": dict(Counter(v for lab in labels for v in lab["violations"]).most_common()),
            "subtypes": dict(Counter(f"{k}:{v}" for lab in labels for k, v in lab["subtypes"].items())),
            "duplicate_clusters": len({lab["cluster"]["cluster_id"] for lab in labels if lab["cluster"]}),
            "image_tiers": dict(Counter(lab["tier"] for lab in labels)),
            "photos_by_kind": dict(Counter(p["kind"] for c in claims for p in c["photos"])),
            "remedy": dict(Counter(str(c["remedy"]) for c in claims)),
            "refund_cents_median": statistics.median(c["refund_cents"] for c in claims if c["refund_cents"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[0]) if args else REPO_ROOT / "evals" / "results" / "data_profile.json"
    raw = load_raw(REPO_ROOT / "data" / "raw" / "cpsc")
    recalls = {p.source_id: (p, r) for r, p in ((r, parse_record(r)) for r in raw)}
    profile = {
        "cpsc_recalls": profile_recalls(raw),
        "mpr_spreadsheet": profile_mpr(REPO_ROOT / "data" / "raw" / "mpr.xlsx", recalls),
        "recallbench": profile_recallbench(REPO_ROOT / "data" / "raw" / "recallbench" / "cpsc-recalls-full.csv"),
        **profile_golden(),
    }
    out.write_text(json.dumps(profile, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
