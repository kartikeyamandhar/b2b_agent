"""Golden notice set builder (evals/extraction/PREREG.md, Revision 1).

python -m recallops.evals.golden_build extend      draw the 60-notice extension, write notices/*.txt and the manifest
python -m recallops.evals.golden_build build       labels_raw.json -> golden_notices.json with computed spans

The builder never reads extractor output. Spans are recomputed from verbatim quotes, so a label value whose quote
is not in the notice text fails the build.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

from recallops.evals.golden import GoldenNotice, build_rules, load_golden, notice_text, validate_golden
from recallops.evals.sampling import _draw, build_pool, load_archive
from recallops.settings import REPO_ROOT
from recallops.text import sha256_text

EXTRACTION_DIR = REPO_ROOT / "evals" / "extraction"
ARCHIVE_DIR = REPO_ROOT / "data" / "raw" / "cpsc"
EXTENSION_SEED = 20261018
EXTENSION_TOTAL = 60
EXTENSION_RICH = 15
AUDITED_STATUS = "model-labeled, assistant-audited (owner delegated)"


def draw_extension(pool: pl.DataFrame, existing: set[str]) -> pl.DataFrame:
    remaining = pool.filter(~pl.col("source_id").is_in(sorted(existing)))
    rich = _draw(remaining.filter(pl.col("identifier_rich")), "year", EXTENSION_RICH, EXTENSION_SEED)
    rest_pool = remaining.filter(~pl.col("source_id").is_in(rich["source_id"].implode()))
    rest = _draw(rest_pool, "category", EXTENSION_TOTAL - rich.height, EXTENSION_SEED)
    return pl.concat([rich, rest]).sort("source_id")


def extend(base: Path = EXTRACTION_DIR, archive: Path = ARCHIVE_DIR) -> dict[str, Any]:
    manifest_path = base / "sample_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "extension" in manifest:
        raise SystemExit("the extension is already drawn; PREREG forbids redraws")
    records = {r.source_id: r for r in load_archive(archive)}
    pool = build_pool(list(records.values()))
    existing = {n["source_id"] for n in manifest["notices"]}
    drawn = draw_extension(pool, existing)
    entries = []
    for row in drawn.iter_rows(named=True):
        record = records[row["source_id"]]
        (base / "notices" / f"{record.source_id}.txt").write_text(record.source_text, encoding="utf-8")
        entries.append(
            {
                "source_id": record.source_id,
                "recall_date": record.recall_date.isoformat(),
                "title": record.title,
                "category": row["category"],
                "identifier_rich": row["identifier_rich"],
                "source_text_sha256": sha256_text(record.source_text),
                "canonicalizer_version": record.canonicalizer_version,
            }
        )
    manifest["extension"] = {"seed": EXTENSION_SEED, "prereg": "PREREG.md Revision 1", "notices": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "drawn": len(entries),
        "identifier_rich": sum(e["identifier_rich"] for e in entries),
        "categories": drawn.group_by("category").len().sort("category").rows(),
    }


def build(base: Path = EXTRACTION_DIR, status: str | None = None) -> dict[str, Any]:
    """Rebuild golden_notices.json from labels_raw.json. M1 flags (baseline_test, synth_source) are kept."""
    manifest = json.loads((base / "sample_manifest.json").read_text(encoding="utf-8"))
    meta = {n["source_id"]: n for n in [*manifest["notices"], *manifest.get("extension", {}).get("notices", [])]}
    golden_path = base / "golden_notices.json"
    previous = {n.source_id: n for n in load_golden(golden_path)}
    raw = json.loads((base / "labels_raw.json").read_text(encoding="utf-8"))
    label_status = status or raw["label_status"]
    notices: list[GoldenNotice] = []
    for label in raw["labels"]:
        sid = label["source_id"]
        text = notice_text(base / "notices", sid)
        m = meta[sid]
        prior = previous.get(sid)
        notices.append(
            GoldenNotice(
                source_id=sid,
                recall_date=m["recall_date"],
                title=m["title"],
                category=m["category"],
                source_text_sha256=sha256_text(text),
                canonicalizer_version=m["canonicalizer_version"],
                label_status=label_status,
                rules=build_rules(label, text),
                notes=label.get("notes", ""),
                baseline_test=prior.baseline_test if prior else False,
                synth_source=prior.synth_source if prior else False,
            )
        )
    problems = validate_golden(notices, base / "notices")
    if problems:
        raise SystemExit("golden set invalid:\n" + "\n".join(problems))
    payload = json.loads(golden_path.read_text(encoding="utf-8"))
    payload.update(
        label_status=label_status,
        count=len(notices),
        notices=[json.loads(n.model_dump_json()) for n in sorted(notices, key=lambda n: n.source_id)],
    )
    golden_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"notices": len(notices), "label_status": label_status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extend", "build"])
    parser.add_argument("--status", help="label status written on every notice (build only)")
    args = parser.parse_args(argv)
    result = extend() if args.command == "extend" else build(status=args.status)
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
