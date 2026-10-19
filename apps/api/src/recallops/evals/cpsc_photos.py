"""The vision tools on CPSC's own recall photos, one photo at a time (evals/cpsc_photos/PREREG.md).

    python -m recallops.evals.cpsc_photos select    download the pre-registered sample (network: www.cpsc.gov)
    python -m recallops.evals.cpsc_photos export    write the images as the model receives them, for blind labeling
    python -m recallops.evals.cpsc_photos run       score against labels.json (LLM_MODE, replay by default)

Image files stay in data/raw/cpsc_images (gitignored). The manifest commits each URL, sha256, and the size the model
receives, so replay (keyed by sha256 and size, llm.client.ImageInput.key) needs no image file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from PIL import Image

from recallops.claims.tools.checks import norm_code
from recallops.claims.vision import (
    VISION_VERSION,
    VisionError,
    check_destruction_photo,
    prepare_image,
    read_label_photo,
)
from recallops.llm.client import (
    Completion,
    ImageInput,
    LiveCallsDisabled,
    LlmClient,
    ModelCallFailed,
    ReplayMiss,
    build_client,
)
from recallops.llm.meter import RunBudget
from recallops.logs import configure, get_logger, log_event
from recallops.settings import REPO_ROOT, Settings, get_settings

log = get_logger(__name__)

Task = Literal["label", "product", "destruction"]
TASKS: tuple[Task, ...] = ("label", "product", "destruction")
SEED = "20261019"
POOL_SIZE: dict[Task, int | None] = {"label": 40, "product": 20, "destruction": None}  # None: every match
PREFIX: dict[Task, str] = {"label": "L", "product": "P", "destruction": "D"}
LABEL_CAPTION = re.compile(r"\b(?:labels?|model (?:number|no\.?|#)|serial|date code)", re.IGNORECASE)
DESTRUCTION_CAPTION = re.compile(r"\b(?:cut|destroy|destroyed|destruction|disposal|dispose)\b", re.IGNORECASE)
ALLOWED_HOSTS = frozenset({"www.cpsc.gov", "cpsc.gov", "cpsc-d8-media-prod.s3.amazonaws.com"})
DEFAULT_INSTRUCTION = "destroy the product"  # the production fallback (claims/agent_tools.py)

DIR = REPO_ROOT / "evals" / "cpsc_photos"
MANIFEST = DIR / "manifest.json"
LABELS = DIR / "labels.json"
RAW = REPO_ROOT / "data" / "raw" / "cpsc"
IMAGES = REPO_ROOT / "data" / "raw" / "cpsc_images"
EXPORT = REPO_ROOT / "data" / "generated" / "cpsc_photo_labeling"
SOURCES = REPO_ROOT / "data" / "raw" / "SOURCES.md"
RESULTS_DIR = REPO_ROOT / "evals" / "results"

# Pre-registered expectations (PREREG.md, written before the run).
EXPECT = {
    "E1 read rate on readable labels >= 0.80": ("label_read_rate", ">=", 0.80),
    "E2 misread rate on readable labels <= 0.10": ("label_misread_rate", "<=", 0.10),
    "E3 hallucination rate on labels with no readable model <= 0.20": ("label_hallucination_rate", "<=", 0.20),
    "E4 complies false on >= 18 of the intact product photos": ("product_rejected", ">=", 18),
    "E5 complies true on >= 3 of the destruction photos showing it": ("destruction_accepted", ">=", 3),
}


# --------------------------------------------------------------------------------------------- selection


@dataclass(frozen=True)
class Candidate:
    task: Task
    recall_number: str
    image_index: int
    url: str
    caption: str
    instruction: str | None


def load_records(raw: Path = RAW) -> list[dict[str, Any]]:
    """One record per recall (the last copy in file order wins), sorted by recall number."""
    by_number: dict[str, dict[str, Any]] = {}
    for path in sorted(raw.glob("**/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for record in data if isinstance(data, list) else []:
            if isinstance(record, dict) and record.get("RecallNumber"):
                by_number[str(record["RecallNumber"])] = record
    return [by_number[k] for k in sorted(by_number)]


def instruction_for(record: dict[str, Any]) -> str | None:
    from recallops.rules import baseline
    from recallops.text import build_source_text

    return baseline.extract(build_source_text(record)).remedy.destruction_instruction


def candidates(records: list[dict[str, Any]]) -> dict[Task, list[Candidate]]:
    pools: dict[Task, list[Candidate]] = {t: [] for t in TASKS}
    for record in records:
        number = str(record["RecallNumber"])
        images = [
            (i, str(img.get("URL") or ""), str(img.get("Caption") or ""))
            for i, img in enumerate(record.get("Images") or [])
        ]
        images = [x for x in images if x[1]]
        label = next((x for x in images if LABEL_CAPTION.search(x[2]) and not DESTRUCTION_CAPTION.search(x[2])), None)
        if label is not None:
            pools["label"].append(Candidate("label", number, label[0], label[1], label[2], None))
        destruction = [x for x in images if DESTRUCTION_CAPTION.search(x[2])]
        product = next(
            (
                x
                for x in images
                if x[2].strip().lower().startswith("recalled")
                and not LABEL_CAPTION.search(x[2])
                and not DESTRUCTION_CAPTION.search(x[2])
            ),
            None,
        )
        if destruction or product is not None:
            instruction = instruction_for(record)
            for i, url, caption in destruction:
                pools["destruction"].append(
                    Candidate("destruction", number, i, url, caption, instruction or DEFAULT_INSTRUCTION)
                )
            if product is not None and instruction:
                pools["product"].append(Candidate("product", number, product[0], product[1], product[2], instruction))
    for task in TASKS:
        pools[task].sort(key=lambda c: (c.recall_number, c.image_index))
        random.Random(f"{SEED}:{task}").shuffle(pools[task])
    return pools


class Rejected(Exception):
    pass


def download(client: Any, url: str, settings: Settings) -> bytes:
    """GET from CPSC's hosts only (redirects followed only within them), within the portal's size limits."""
    for _ in range(3):
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or host not in ALLOWED_HOSTS:
            raise Rejected(f"host {host or '?'} is not CPSC's")
        response = client.get(url)
        if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
            url = str(response.headers["location"])
            continue
        if response.status_code != 200:
            raise Rejected(f"HTTP {response.status_code}")
        data: bytes = response.content
        if len(data) > settings.max_photo_bytes:
            raise Rejected(f"{len(data)} bytes is over MAX_PHOTO_BYTES")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > settings.max_photo_pixels:
                    raise Rejected("over MAX_PHOTO_PIXELS")
                image.load()
        except Rejected:
            raise
        except Exception as exc:
            raise Rejected(f"not an image Pillow can open ({type(exc).__name__})") from exc
        return data
    raise Rejected("too many redirects")


def image_meta(data: bytes) -> dict[str, Any]:
    sha = hashlib.sha256(data).hexdigest()
    sent = prepare_image(data, sha)
    with Image.open(io.BytesIO(data)) as image:
        fmt, width, height = (image.format or "?").upper(), image.width, image.height
    return {
        "sha256": sha,
        "bytes": len(data),
        "format": fmt,
        "width": width,
        "height": height,
        "sent_width": sent.width,
        "sent_height": sent.height,
        "sent_media_type": sent.media_type,
    }


def image_path(sha256: str, fmt: str) -> Path:
    ext = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}.get(fmt, "img")
    return IMAGES / f"{sha256}.{ext}"


def select(settings: Settings) -> dict[str, Any]:
    import httpx

    records = load_records()
    pools = candidates(records)
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    IMAGES.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": settings.http_user_agent}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=False) as client:
        for task in TASKS:
            wanted = POOL_SIZE[task]
            taken = 0
            for cand in pools[task]:
                if wanted is not None and taken >= wanted:
                    break
                try:
                    data = download(client, cand.url, settings)
                except (Rejected, httpx.HTTPError) as exc:
                    skipped.append(
                        {"task": task, "recall_number": cand.recall_number, "url": cand.url, "reason": str(exc)[:120]}
                    )
                    continue
                finally:
                    time.sleep(0.5)  # one request at a time, politely
                meta = image_meta(data)
                image_path(meta["sha256"], meta["format"]).write_bytes(data)
                taken += 1
                items.append(
                    {
                        "id": f"{PREFIX[task]}{taken:02d}",
                        "task": task,
                        "recall_number": cand.recall_number,
                        "image_index": cand.image_index,
                        "url": cand.url,
                        "caption": cand.caption,
                        "instruction": cand.instruction,
                        **meta,
                    }
                )
                log_event(log, "cpsc_photo_selected", task=task, recall_number=cand.recall_number)
    manifest = {
        "version": 1,
        "seed": SEED,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "vision_version": VISION_VERSION,
        "pool_sizes": {t: len(pools[t]) for t in TASKS},
        "items": items,
        "skipped": skipped,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def load_manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data


def export() -> list[dict[str, Any]]:
    """The images exactly as the model receives them, renamed by item id, and a sheet without captions."""
    EXPORT.mkdir(parents=True, exist_ok=True)
    sheet = []
    for item in load_manifest()["items"]:
        data = image_path(item["sha256"], item["format"]).read_bytes()
        sent = prepare_image(data, item["sha256"])
        ext = "png" if sent.media_type == "image/png" else "jpg"
        (EXPORT / f"{item['id']}.{ext}").write_bytes(base64.b64decode(sent.data_b64))
        row = {"id": item["id"], "task": item["task"], "file": str(EXPORT / f"{item['id']}.{ext}")}
        if item["task"] != "label":
            row["instruction"] = item["instruction"]
        sheet.append(row)
    (EXPORT / "sheet.json").write_text(json.dumps(sheet, indent=1) + "\n", encoding="utf-8")
    return sheet


# --------------------------------------------------------------------------------------------- run and score


def image_input(item: dict[str, Any], live: bool) -> ImageInput:
    """Replay needs only sha256 and the sent size; a present file must still hash and size to the manifest."""
    path = image_path(item["sha256"], item["format"])
    if not path.exists():
        if live:
            raise FileNotFoundError(f"{item['id']}: image file missing for a live run (run select first)")
        return ImageInput(item["sent_media_type"], "", item["sent_width"], item["sent_height"], item["sha256"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError(f"{item['id']}: image file does not match its manifest sha256")
    sent = prepare_image(data, item["sha256"])
    if (sent.width, sent.height) != (item["sent_width"], item["sent_height"]):
        raise ValueError(f"{item['id']}: sent size {sent.width}x{sent.height} differs from the manifest")
    return sent


def call_item(
    llm: LlmClient, item: dict[str, Any], live: bool, settings: Settings
) -> tuple[dict[str, Any], Completion]:
    run = RunBudget(
        max_steps=settings.run_cap_steps, max_usd=settings.run_cap_usd, max_seconds=settings.run_cap_seconds
    )
    image = image_input(item, live)
    if item["task"] == "label":
        label = read_label_photo(llm, run, image)
        return label.reading.model_dump(mode="json"), label.completion
    seen = check_destruction_photo(llm, run, image, item["instruction"])
    return seen.reading.model_dump(mode="json"), seen.completion


def score_item(item: dict[str, Any], truth: dict[str, Any], reading: dict[str, Any] | None) -> dict[str, Any]:
    """One photo's verdict. reading None means the call failed or had no recording."""
    if reading is None:
        return {"verdict": "missing"}
    if item["task"] == "label":
        texts = [t for t in truth.get("model_texts") or [] if t]
        read = reading.get("model_text") if reading.get("legible") else None
        # Diagnostic only (labels.json, adjudication; PREREG.md Outcome): a wrong model text that holds the right model
        # plus extra characters, holds another code printed in the photo (a serial, SKU, or batch number, possibly
        # with its field word, as in "ITEM 61970"), or is printed nowhere in it.
        printed = {norm_code(c) for c in truth.get("printed_codes") or [] if c}
        got = norm_code(read)
        kind = "unprinted"
        if any(norm_code(t) and norm_code(t) in got for t in texts):
            kind = "formatting"
        elif got in printed or any(len(c) >= 4 and c in got for c in printed):
            kind = "other_code"
        if truth.get("legible") and texts:
            if not read:
                return {"verdict": "abstained", "group": "readable"}
            if norm_code(read) in {norm_code(t) for t in texts}:
                return {"verdict": "read", "group": "readable", "exact": read in texts}
            return {"verdict": "misread", "group": "readable", "exact": False, "kind": kind}
        if read:
            return {"verdict": "hallucinated", "group": "no_model", "kind": kind}
        return {"verdict": "abstained_ok", "group": "no_model"}
    expected = bool(truth.get("destruction_shown"))
    complies = bool(reading.get("complies"))
    return {"verdict": "correct" if complies == expected else "wrong", "group": "shown" if expected else "not_shown"}


def summarize(items: list[dict[str, Any]], verdicts: dict[str, dict[str, Any]], costs: list[Decimal]) -> dict[str, Any]:
    def count(task: str, group: str | None = None, verdict: str | None = None) -> int:
        return sum(
            1
            for it in items
            if it["task"] == task
            and (group is None or verdicts[it["id"]].get("group") == group)
            and (verdict is None or verdicts[it["id"]]["verdict"] == verdict)
        )

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None

    readable = count("label", "readable")
    no_model = count("label", "no_model")
    exact = sum(1 for it in items if verdicts[it["id"]].get("exact"))
    metrics: dict[str, Any] = {
        "label_n": count("label"),
        "label_readable": readable,
        "label_read": count("label", "readable", "read"),
        "label_read_exact": exact,
        "label_misread": count("label", "readable", "misread"),
        "label_abstained": count("label", "readable", "abstained"),
        "label_no_model": no_model,
        "label_hallucinated": count("label", "no_model", "hallucinated"),
        "label_wrong_formatting": sum(1 for v in verdicts.values() if v.get("kind") == "formatting"),
        "label_wrong_other_code": sum(1 for v in verdicts.values() if v.get("kind") == "other_code"),
        "label_wrong_unprinted": sum(1 for v in verdicts.values() if v.get("kind") == "unprinted"),
        "label_read_rate": rate(count("label", "readable", "read"), readable),
        "label_exact_rate": rate(exact, readable),
        "label_misread_rate": rate(count("label", "readable", "misread"), readable),
        "label_hallucination_rate": rate(count("label", "no_model", "hallucinated"), no_model),
        "product_n": count("product"),
        "product_not_shown": count("product", "not_shown"),
        "product_rejected": count("product", "not_shown", "correct"),
        "product_shown": count("product", "shown"),
        "product_shown_accepted": count("product", "shown", "correct"),
        "destruction_n": count("destruction"),
        "destruction_shown": count("destruction", "shown"),
        "destruction_accepted": count("destruction", "shown", "correct"),
        "destruction_not_shown": count("destruction", "not_shown"),
        "destruction_not_shown_rejected": count("destruction", "not_shown", "correct"),
        "missing": sum(1 for v in verdicts.values() if v["verdict"] == "missing"),
        "model_calls": len(costs),
        "nominal_cost_usd": str(sum(costs, Decimal(0))),
    }
    return metrics


def expectations(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, (key, op, threshold) in EXPECT.items():
        value = metrics.get(key)
        met = None if value is None else (value >= threshold if op == ">=" else value <= threshold)
        out.append({"expectation": name, "value": value, "met": met})
    return out


def agreement(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Between the two blind labelers (labels_a.json, labels_b.json), before adjudication."""
    paths = [DIR / "labels_a.json", DIR / "labels_b.json"]
    if not all(p.exists() for p in paths):
        return {"available": False}
    a, b = (json.loads(p.read_text(encoding="utf-8"))["items"] for p in paths)
    same, total = 0, 0
    for it in items:
        x, y = a.get(it["id"]), b.get(it["id"])
        if x is None or y is None:
            continue
        total += 1
        if it["task"] == "label":
            nx = {norm_code(t) for t in x.get("model_texts") or [] if t} if x.get("legible") else set()
            ny = {norm_code(t) for t in y.get("model_texts") or [] if t} if y.get("legible") else set()
            same += int(nx == ny)
        else:
            same += int(bool(x.get("destruction_shown")) == bool(y.get("destruction_shown")))
    return {"available": True, "items": total, "agreed": same, "rate": round(same / total, 4) if total else None}


def run(settings: Settings, results_dir: Path = RESULTS_DIR, llm: LlmClient | None = None) -> dict[str, Any]:
    from recallops.db.engine import internal_engine
    from recallops.evals.ablation import EVAL_FIRM
    from recallops.tracing.writer import DbTrace

    manifest = load_manifest()
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    truth: dict[str, dict[str, Any]] = labels["items"]
    items = manifest["items"]
    unlabeled = [it["id"] for it in items if it["id"] not in truth]
    if unlabeled:
        raise SystemExit(f"labels.json has no label for {', '.join(unlabeled[:5])}")
    live = settings.llm_mode == "live"
    if llm is None:
        try:
            llm = build_client(settings)
        except LiveCallsDisabled:  # LLM_MODE=fake (the test suite): report the block as not run, never guess
            llm = None
    if live:
        ensure_eval_firm(EVAL_FIRM)
    readings: dict[str, dict[str, Any] | None] = {}
    verdicts: dict[str, dict[str, Any]] = {}
    costs: list[Decimal] = []
    for item in items:
        reading: dict[str, Any] | None = None
        if llm is None:
            readings[item["id"]] = None
            verdicts[item["id"]] = score_item(item, truth[item["id"]], None)
            continue
        try:
            reading, done = call_item(llm, item, live, settings)
            costs.append(done.nominal_cost_usd)
            if live:
                tool = "read_label_photo" if item["task"] == "label" else "check_destruction_photo"
                usage = done.response.usage
                trace = DbTrace(
                    internal_engine(),
                    "rules_plus_vision",
                    EVAL_FIRM,
                    model_id=settings.llm_model_id,
                    config={"eval": True, "block": "cpsc_photos", "item": item["id"]},
                )
                trace.step(
                    tool,
                    tool=tool,
                    args={"item": item["id"], "sha256": item["sha256"]},
                    result=reading,
                    latency_ms=done.latency_ms,
                    tokens_in=usage.total_input,
                    tokens_out=usage.output_tokens,
                    cost_usd=done.cost_usd,
                    nominal_cost_usd=done.nominal_cost_usd,
                    counted=True,
                )
                trace.finish("done", {"item": item["id"]})
        except (ReplayMiss, ModelCallFailed, VisionError) as exc:
            log_event(log, "cpsc_photo_call_failed", item=item["id"], error=type(exc).__name__)
            reading = None
        readings[item["id"]] = reading
        verdicts[item["id"]] = score_item(item, truth[item["id"]], reading)
    metrics = summarize(items, verdicts, costs)
    payload = {
        "block": "cpsc_photos",
        "vision_version": VISION_VERSION,
        "status": "run" if llm is not None else f"not run (LLM_MODE={settings.llm_mode})",
        "label_status": labels.get("status"),
        "metrics": metrics,
        "expectations": expectations(metrics),
        "labeler_agreement": agreement(items),
        "items": [
            {
                "id": it["id"],
                "task": it["task"],
                "recall_number": it["recall_number"],
                "verdict": verdicts[it["id"]]["verdict"],
                "reading": readings[it["id"]],
            }
            for it in items
        ],
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "cpsc_photos.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    (results_dir / "cpsc_photos.md").write_text(render(payload, manifest), encoding="utf-8")
    return payload


def ensure_eval_firm(firm_id: Any) -> None:
    from sqlalchemy.orm import Session

    from recallops.db.engine import internal_engine
    from recallops.models import Firm

    with Session(internal_engine()) as session, session.begin():
        if session.get(Firm, firm_id) is None:
            session.add(Firm(id=firm_id, name="Eval Harness", plan="enterprise"))


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render(payload: dict[str, Any], manifest: dict[str, Any]) -> str:
    m = payload["metrics"]
    agree = payload["labeler_agreement"]
    lines = [
        "# Vision tools on CPSC's own recall photos",
        "",
        "Generated by `make eval` (replay). Pre-registration: evals/cpsc_photos/PREREG.md. Photos: "
        f"{len(manifest['items'])} images CPSC published with its recall notices (manifest.json lists every URL and "
        f"sha256), drawn by a seeded rule from pools of {manifest['pool_sizes']['label']} label, "
        f"{manifest['pool_sizes']['product']} product, and {manifest['pool_sizes']['destruction']} destruction "
        f"photos. Vision tools {payload['vision_version']} (unchanged, frozen with the ablation). Labels: "
        f"{payload['label_status']}. Status: {payload['status']}.",
        "",
        "| task | photos | result |",
        "|---|---|---|",
        f"| read the model number (readable labels) | {m['label_readable']} | read {m['label_read']} "
        f"({_fmt(m['label_read_rate'])}; exact string {m['label_read_exact']}), misread {m['label_misread']}, "
        f"abstained {m['label_abstained']} |",
        f"| no readable model number | {m['label_no_model']} | returned a model text {m['label_hallucinated']} "
        f"({_fmt(m['label_hallucination_rate'])}) |",
        f"| intact product photos, destruction instruction | {m['product_not_shown']} | correctly rejected "
        f"{m['product_rejected']} |",
        f"| CPSC's own destruction photos showing it done | {m['destruction_shown']} | accepted "
        f"{m['destruction_accepted']} |",
        f"| destruction-captioned photos not showing it done | {m['destruction_not_shown']} | correctly rejected "
        f"{m['destruction_not_shown_rejected']} |",
        "",
        f"Every wrong model text, misread or returned where none is readable: {m['label_wrong_formatting']} held the "
        f"right model with extra characters the label prints, {m['label_wrong_other_code']} were another code printed "
        f"in the photo (a serial, SKU, item, or style number), and {m['label_wrong_unprinted']} were printed "
        "nowhere in it.",
        "",
        f"Model calls: {m['model_calls']}, nominal cost {m['nominal_cost_usd']} USD. Missing recordings: "
        f"{m['missing']}.",
        "",
    ]
    if agree.get("available"):
        lines += [
            f"Blind labelers agreed on {agree['agreed']} of {agree['items']} photos ({_fmt(agree['rate'])}) "
            "before adjudication.",
            "",
        ]
    lines += ["| expectation (pre-registered) | value | met |", "|---|---|---|"]
    for e in payload["expectations"]:
        met = "n/a" if e["met"] is None else ("met" if e["met"] else "missed")
        lines.append(f"| {e['expectation']} | {_fmt(e['value'])} | {met} |")
    wrong = [it for it in payload["items"] if it["verdict"] in ("misread", "hallucinated", "wrong", "abstained")]
    if wrong:
        lines += [
            "",
            "Photos the tools got wrong or abstained on:",
            "",
            "| id | task | recall | verdict | model said |",
            "|---|---|---|---|---|",
        ]
        for it in wrong:
            r = it["reading"] or {}
            said = r.get("model_text") if it["task"] == "label" else r.get("observed")
            said = (str(said) if said is not None else "nothing").replace("|", "/")
            lines.append(f"| {it['id']} | {it['task']} | {it['recall_number']} | {it['verdict']} | {said} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("select", "export", "run"), nargs="?", default="run")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure(settings.log_level)
    if args.command == "select":
        manifest = select(settings)
        return {
            "items": len(manifest["items"]),
            "skipped": len(manifest["skipped"]),
            "pool_sizes": manifest["pool_sizes"],
        }
    if args.command == "export":
        return {"exported": len(export()), "dir": str(EXPORT)}
    return run(settings, results_dir)


if __name__ == "__main__":
    out = main(sys.argv[1:])
    print(
        json.dumps(
            out if "metrics" not in out else {"metrics": out["metrics"], "expectations": out["expectations"]}, indent=1
        )
    )
