"""Labeling CLI for the owner's audit of the golden set (and blind labeling of new notices in M2).

Audit:  python evals/extraction/label.py --audit [--source-id 24082]
Blind:  python evals/extraction/label.py --blind --source-id 99999   (notice text must exist in notices/)

The CLI never shows extractor output. In audit mode it shows the current gold label for each field; Enter
keeps it, "-" clears it, and anything else replaces it. Every change is appended to audit_log.jsonl, the
notice becomes "owner-audited", and spans are recomputed from verbatim quotes, so a value that is not in
the notice text is refused.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from recallops.evals.golden import GoldenNotice, QuoteNotFound, build_rules, load_golden, notice_text
from recallops.rules.schemas import ExtractedRules
from recallops.settings import REPO_ROOT
from recallops.text import sha256_text

EXTRACTION_DIR = REPO_ROOT / "evals" / "extraction"
AUDITED = "owner-audited"

Ask = Callable[[str], str]
Say = Callable[[str], None]

LIST_FIELDS = ("models", "sold_at")
STRING_FIELDS = ("model_label_location", "destruction_instruction", "registration_url", "contact")


def to_label(rules: ExtractedRules) -> dict[str, Any]:
    """Inverse of build_rules: rules back to the value-plus-quote label format."""
    quotes = {(s.field, s.quote) for s in [*rules.identification.evidence, *rules.remedy.evidence]}

    def quote_for(field: str, fallback: str) -> str:
        return next((q for f, q in quotes if f == field), fallback)

    i, r = rules.identification, rules.remedy
    return {
        "models": [[m, m] for m in i.models],
        "model_label_location": [
            i.model_label_location,
            quote_for("model_label_location", i.model_label_location),
        ]
        if i.model_label_location
        else None,
        "serial_ranges": [s.model_dump() for s in i.serial_ranges],
        "date_codes": [d.model_dump() for d in i.date_codes],
        "sold_at": [[s, s] for s in i.sold_at],
        "sold_from": [i.sold_from.isoformat(), quote_for("sold_from", "")] if i.sold_from else None,
        "sold_to": [i.sold_to.isoformat(), quote_for("sold_to", "")] if i.sold_to else None,
        "price_range": [
            i.price_range_cents.min_cents,
            i.price_range_cents.max_cents,
            quote_for("price_range_cents", ""),
        ]
        if i.price_range_cents
        else None,
        "units_affected": [i.units_affected, quote_for("units_affected", "")] if i.units_affected is not None else None,
        "remedy_type": [r.remedy_type, quote_for("remedy_type", "")] if r.remedy_type else None,
        "options": [
            {
                "type": o.type,
                "steps": o.steps,
                "proof_required": o.proof_required,
                "payout_method": o.payout.method,
                "amount_rule": o.payout.amount_rule,
            }
            for o in r.options
        ],
        "destruction_instruction": [
            r.destruction_instruction,
            quote_for("destruction_instruction", r.destruction_instruction),
        ]
        if r.destruction_instruction
        else None,
        "registration_url": [r.registration_url, r.registration_url] if r.registration_url else None,
        "contact": [r.contact, r.contact] if r.contact else None,
    }


def _edit_field(label: dict[str, Any], field: str, answer: str, ask: Ask) -> None:
    if answer == "-":
        label[field] = [] if field in LIST_FIELDS else None
    elif field in LIST_FIELDS:
        label[field] = [[v.strip(), v.strip()] for v in answer.split("|") if v.strip()]
    elif field in STRING_FIELDS:
        label[field] = [answer, answer]
    elif field in ("sold_from", "sold_to"):
        date.fromisoformat(answer)
        label[field] = [answer, ask(f"  verbatim quote supporting {field}: ")]
    elif field == "units_affected":
        label[field] = [int(answer.replace(",", "")), ask("  verbatim quote supporting units: ")]
    elif field == "remedy_type":
        label[field] = [answer, ask("  verbatim quote supporting remedy_type: ")]
    else:
        raise ValueError(f"{field} is edited in labels_raw.json directly")


EDITABLE = ("models", "sold_at", "sold_from", "sold_to", "units_affected", "remedy_type", *STRING_FIELDS)


def audit_notice(notice: GoldenNotice, text: str, ask: Ask, say: Say) -> tuple[GoldenNotice, list[dict[str, Any]]]:
    label = to_label(notice.rules)
    changes: list[dict[str, Any]] = []
    say(f"\n===== {notice.source_id} ({notice.label_status})\n{text}")
    for field in EDITABLE:
        current = label[field]
        shown = " | ".join(v[0] for v in current) if field in LIST_FIELDS else (current[0] if current else "null")
        answer = ask(f"{field} [{shown}] (Enter keeps, '-' clears): ").strip()
        if not answer:
            continue
        before = json.dumps(current)
        _edit_field(label, field, answer, ask)
        changes.append(
            {
                "source_id": notice.source_id,
                "field": field,
                "before": before,
                "after": json.dumps(label[field]),
                "at": datetime.now(UTC).isoformat(),
            }
        )
    try:
        rules = build_rules(label, text)
    except QuoteNotFound as exc:
        say(f"refused: {exc}. Nothing saved for {notice.source_id}.")
        return notice, []
    updated = notice.model_copy(update={"rules": rules, "label_status": AUDITED})
    return updated, changes


def blind_label(source_id: str, text: str, ask: Ask, say: Say) -> ExtractedRules:
    """Fresh label with no pre-fill. Every field must be answered (a value, or '-' for none)."""
    say(f"\n===== {source_id} (blind)\n{text}")
    label: dict[str, Any] = {
        "models": [],
        "model_label_location": None,
        "serial_ranges": [],
        "date_codes": [],
        "sold_at": [],
        "sold_from": None,
        "sold_to": None,
        "price_range": None,
        "units_affected": None,
        "remedy_type": None,
        "options": [],
        "destruction_instruction": None,
        "registration_url": None,
        "contact": None,
    }
    for field in EDITABLE:
        answer = ""
        while not answer:
            answer = ask(f"{field} (value, ' | ' between list items, or '-' for none): ").strip()
        _edit_field(label, field, answer, ask)
    return build_rules(label, text)


def save(path: Path, notices: list[GoldenNotice], changes: list[dict[str, Any]], log: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["notices"] = [json.loads(n.model_dump_json()) for n in notices]
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with log.open("a", encoding="utf-8") as fh:
        for change in changes:
            fh.write(json.dumps(change) + "\n")


def main(argv: list[str] | None = None, ask: Ask = input, say: Say = print, base: Path = EXTRACTION_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true")
    mode.add_argument("--blind", action="store_true")
    parser.add_argument("--source-id")
    args = parser.parse_args(argv)
    golden_path = base / "golden_notices.json"
    notices = load_golden(golden_path)
    if args.blind:
        if not args.source_id:
            parser.error("--blind needs --source-id")
        text = notice_text(base / "notices", args.source_id)
        rules = blind_label(args.source_id, text, ask, say)
        say(
            json.dumps(
                {
                    "source_id": args.source_id,
                    "source_text_sha256": sha256_text(text),
                    "rules": json.loads(rules.model_dump_json()),
                },
                indent=1,
            )
        )
        return 0
    targets = [n for n in notices if args.source_id in (None, n.source_id)]
    changes: list[dict[str, Any]] = []
    by_id = {n.source_id: n for n in notices}
    for notice in targets:
        updated, notice_changes = audit_notice(notice, notice_text(base / "notices", notice.source_id), ask, say)
        by_id[notice.source_id] = updated
        changes += notice_changes
    save(golden_path, list(by_id.values()), changes, base / "audit_log.jsonl")
    say(f"audited {len(targets)} notice(s), {len(changes)} change(s) logged")
    return 0
