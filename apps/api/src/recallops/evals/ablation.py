"""Adjudication ablation (01-backend.md section 10; evals/adjudication/PREREG.md Revision 2).

python -m recallops.evals.ablation                       replay every row on every block; writes ablation.md/.json
python -m recallops.evals.ablation --rows loop_full --blocks golden300 --workers 6
                                                         with LLM_MODE=live: record the missing responses

Rows: rules_only, rules_plus_vision, single_pass, loop_full, loop_no_retrieval, loop_keyword_only. Blocks: golden300
(kill-criterion block), probe60 (assistant-designed capability probe), standin40 (agent-written stand-in for the
classmate set). Each claim is scored against the other claims of its own block and program only. Labels are read
only by the scorer. In replay a row is reported only when every claim of the block has its recordings; otherwise it
is "not run (k/n)". Retrieval rows search the documents of an "Eval Harness" firm's programs, seeded idempotently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from recallops.claims.agent_tools import ToolContext
from recallops.claims.graph import VARIANTS, NoEffects, build_graph, run_budget, summarize
from recallops.claims.rules_only import adjudicate as rules_only
from recallops.claims.rules_plus_vision import adjudicate_with_vision
from recallops.claims.tools import ClaimFacts
from recallops.db.engine import internal_engine
from recallops.evals.adjudication import CLASSES, f1, facts_from_record
from recallops.evals.golden import load_golden, load_program_rules, notice_text
from recallops.llm.client import LiveCallsDisabled, LlmClient, ModelCallFailed, ReplayMiss, build_client
from recallops.llm.meter import BudgetExhausted
from recallops.logs import configure, get_logger, log_event
from recallops.models import Document, Firm, RecallProgram
from recallops.retrieval.documents import program_documents
from recallops.retrieval.embed import build_embedder
from recallops.retrieval.search import PgRetriever, index_program
from recallops.rules.schemas import ExtractedRules
from recallops.settings import REPO_ROOT, Settings, get_settings
from recallops.tracing.writer import DbTrace, Graph, MemoryTrace, TraceSink

log = get_logger("evals.ablation")

ADJ = REPO_ROOT / "evals" / "adjudication"
RESULTS = REPO_ROOT / "evals" / "results"
ROWS = ("rules_only", "rules_plus_vision", "single_pass", "loop_full", "loop_no_retrieval", "loop_keyword_only")
EVAL_FIRM = UUID("00000000-0000-4000-8000-00000000e7a1")
NS = UUID("e7a1e7a1-0000-4000-8000-000000000000")
THRESHOLD_CENTS = 10_000


@dataclass(frozen=True)
class Block:
    key: str
    title: str
    claims_file: Path
    labels_dir: Path
    population_key: str


BLOCKS = {
    "golden300": Block(
        "golden300",
        "golden300: 300 synthetic claims (kill-criterion block)",
        ADJ / "golden_claims.json",
        ADJ / "labels",
        "program",
    ),
    "probe60": Block(
        "probe60",
        "probe60: assistant-designed capability probe (targets rules_only blind spots by construction)",
        ADJ / "probe_claims.json",
        ADJ / "probe_labels",
        "population",
    ),
    "standin40": Block(
        "standin40",
        "standin40: agent-written stand-in for the classmate set (shares the generator's renderer)",
        ADJ / "standin_claims.json",
        ADJ / "standin_labels",
        "population",
    ),
}


@dataclass
class ClaimRun:
    claim_id: str
    population: str
    expected: str
    predicted: str
    routed: bool
    capped: bool
    counted_steps: int
    rows: int
    cost_usd: float
    seconds: float
    category: str
    missing: bool = False
    error: str | None = None


@dataclass
class RowResult:
    row: str
    block: str
    runs: list[ClaimRun] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(self.runs) and not any(r.missing or r.error for r in self.runs)


def load_block(block: Block) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    payload = json.loads(block.claims_file.read_text(encoding="utf-8"))
    labels = {
        r["claim_id"]: json.loads((block.labels_dir / f"{r['claim_id']}.json").read_text(encoding="utf-8"))
        for r in payload["claims"]
    }
    return payload["claims"], labels


def ensure_eval_programs(engine: Engine, settings: Settings, rules: dict[str, ExtractedRules]) -> dict[str, UUID]:
    """Idempotent: the Eval Harness firm, one program per rules id, and its notice and FAQ documents."""
    titles = {n.source_id: n.title for n in load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json")}
    embedder = build_embedder(settings.embedding_model_id, settings.embedding_dim)
    ids: dict[str, UUID] = {}
    with Session(engine) as session, session.begin():
        if session.get(Firm, EVAL_FIRM) is None:
            session.add(Firm(id=EVAL_FIRM, name="Eval Harness", plan="enterprise"))
            session.flush()
        for pid, r in sorted(rules.items()):
            program_id = uuid.uuid5(NS, f"eval-program:{pid}")
            ids[pid] = program_id
            rules_json = json.loads(r.model_dump_json())
            existing = session.get(RecallProgram, program_id)
            if existing is None:
                session.add(
                    RecallProgram(
                        id=program_id,
                        firm_id=EVAL_FIRM,
                        status="open",
                        identification_rules=rules_json["identification"],
                        remedy_rules=rules_json["remedy"],
                        value_threshold_cents=THRESHOLD_CENTS,
                        opened_at=datetime.now(UTC),
                    )
                )
                session.flush()
            has_docs = session.scalar(select(Document.id).where(Document.program_id == program_id).limit(1))
            if has_docs is None:
                title = titles.get(pid, pid)
                source = notice_text(REPO_ROOT / "evals" / "extraction" / "notices", pid)
                index_program(
                    session, EVAL_FIRM, program_id, program_documents(title, source, r, THRESHOLD_CENTS), embedder
                )
    return ids


def _photo_reader(record: dict[str, Any], live: bool) -> Any:
    paths = {p["kind"]: (REPO_ROOT / p["path"], p["sha256"]) for p in record["photos"]}

    def read(kind: str) -> bytes | None:
        if kind not in paths:
            return None
        path, sha = paths[kind]
        if not path.is_file():
            if live:
                raise FileNotFoundError(f"{path} is missing; rebuild the block's photos before a live run")
            return None
        data: bytes = path.read_bytes()
        if live and hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"{path} does not match its committed sha256; the photo was not regenerated identically")
        return data

    return read


def run_claim(
    row: str,
    record: dict[str, Any],
    label: dict[str, Any],
    rules: ExtractedRules,
    population: list[ClaimFacts],
    program_id: UUID,
    llm: LlmClient | None,
    settings: Settings,
    engine: Engine,
    live: bool,
) -> ClaimRun:
    facts = facts_from_record(record)
    category = label.get("category") or ",".join(label.get("violations", [])) or "clean"
    base = {
        "claim_id": record["claim_id"],
        "population": str(record.get("population") or record.get("program")),
        "expected": label["expected_outcome"],
        "category": category,
    }
    graph_name: Graph = (
        "rules_only" if row == "rules_only" else ("rules_plus_vision" if row == "rules_plus_vision" else "adjudicate")
    )
    trace: TraceSink
    if live and row != "rules_only":
        trace = DbTrace(
            engine,
            graph_name,
            EVAL_FIRM,
            program_id=program_id,
            model_id=settings.llm_model_id,
            config={"eval": True, "row": row, "claim": record["claim_id"]},
        )
    else:
        trace = MemoryTrace(graph_name)
    budget = run_budget(settings)
    t0 = time.monotonic()
    predicted: str
    try:
        if row == "rules_only":
            result = rules_only(facts, rules, population, THRESHOLD_CENTS, trace, settings.phash_duplicate_bits)
            predicted, routed, capped = str(result.outcome), result.routed_to_human, False
        else:
            with Session(engine) as rsession:
                config = VARIANTS.get(row)
                retrieval = config.retrieval if config else "none"
                retriever = (
                    PgRetriever(
                        rsession,
                        EVAL_FIRM,
                        build_embedder(settings.embedding_model_id, settings.embedding_dim),
                        retrieval,
                    )
                    if retrieval != "none"
                    else None
                )
                ctx = ToolContext(
                    claim=facts,
                    rules=rules,
                    program_id=program_id,
                    value_threshold_cents=THRESHOLD_CENTS,
                    others=lambda: population,
                    photo_bytes=_photo_reader(record, live),
                    retriever=retriever,
                    llm=llm,
                    phash_bits=settings.phash_duplicate_bits,
                )
                if row == "rules_plus_vision":
                    vt = adjudicate_with_vision(ctx, budget, trace)
                    predicted, routed, capped = vt.outcome, vt.routed_to_human, vt.capped
                else:
                    assert llm is not None and config is not None
                    graph = build_graph(ctx, llm, trace, budget, config, NoEffects(), human_in_loop=False)
                    res = summarize(graph.invoke({}))
                    capped = res.status in ("capped", "failed")
                    predicted, routed = (res.outcome or res.status), res.routed_to_human
        if isinstance(trace, DbTrace):
            trace.finish("done", {"predicted": predicted})
    except ReplayMiss:
        return ClaimRun(
            **base,
            predicted="missing",
            routed=False,
            capped=False,
            counted_steps=0,
            rows=0,
            cost_usd=0.0,
            seconds=0.0,
            missing=True,
        )
    except (ModelCallFailed, FileNotFoundError, ValueError) as exc:
        return ClaimRun(
            **base,
            predicted="error",
            routed=False,
            capped=False,
            counted_steps=0,
            rows=0,
            cost_usd=0.0,
            seconds=0.0,
            error=str(exc)[:300],
        )
    rows = len(trace.steps) if isinstance(trace, MemoryTrace) else budget.steps
    seconds = budget.elapsed() if row != "rules_only" else time.monotonic() - t0
    return ClaimRun(
        **base,
        predicted=predicted,
        routed=routed,
        capped=capped,
        counted_steps=budget.steps,
        rows=rows,
        cost_usd=float(budget.cost_usd),
        seconds=round(seconds, 4),
    )


def run_row(
    row: str, block: Block, llm: LlmClient | None, settings: Settings, engine: Engine, live: bool, workers: int
) -> RowResult:
    claims, labels = load_block(block)
    rules = load_program_rules(ADJ / "program_rules.json")
    program_ids = ensure_eval_programs(engine, settings, rules) if row != "rules_only" else {}
    by_population: dict[str, list[ClaimFacts]] = defaultdict(list)
    for record in claims:
        by_population[str(record.get(block.population_key) or record["program"])].append(facts_from_record(record))
    jobs = []
    for record in claims:
        pid = str(record["program"])
        population = by_population[str(record.get(block.population_key) or pid)]
        jobs.append((record, labels[record["claim_id"]], rules[pid], population, program_ids.get(pid, NS)))

    def one(job: tuple[Any, ...]) -> ClaimRun:
        record, label, r, population, program_id = job
        return run_claim(row, record, label, r, population, program_id, llm, settings, engine, live)

    if workers > 1 and live:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            runs = list(pool.map(one, jobs))
    else:
        runs = [one(job) for job in jobs]
    result = RowResult(row, block.key, runs)
    log_event(
        log,
        "ablation_row_done",
        row=row,
        block=block.key,
        claims=len(runs),
        missing=sum(r.missing for r in runs),
        errors=sum(1 for r in runs if r.error),
    )
    return result


def metrics(runs: list[ClaimRun]) -> dict[str, Any]:
    n = len(runs)
    per_class: dict[str, dict[str, Any]] = {}
    for cls in CLASSES:
        tp = sum(1 for r in runs if r.predicted == cls and r.expected == cls)
        fp = sum(1 for r in runs if r.predicted == cls and r.expected != cls)
        fn = sum(1 for r in runs if r.predicted != cls and r.expected == cls)
        per_class[cls] = {
            "f1": round(f1(tp, fp, fn), 4),
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    present = [c for c in CLASSES if any(r.expected == c for r in runs)]
    return {
        "n": n,
        "outcome_accuracy": round(sum(r.predicted == r.expected for r in runs) / n, 4),
        "macro_f1": round(sum(per_class[c]["f1"] for c in present) / len(present), 4),
        "macro_f1_classes": present,
        "approval_accuracy": round(sum((r.predicted == "approve") == (r.expected == "approve") for r in runs) / n, 4),
        "fraud_precision": per_class["flag"]["precision"],
        "fraud_recall": per_class["flag"]["recall"],
        "needs_info_precision": per_class["needs_info"]["precision"],
        "routed_share": round(sum(r.routed for r in runs) / n, 4),
        "capped_share": round(sum(r.capped for r in runs) / n, 4),
        "mean_counted_steps": round(statistics.mean(r.counted_steps for r in runs), 2),
        "mean_rows": round(statistics.mean(r.rows for r in runs), 2),
        "mean_cost_usd": round(statistics.mean(r.cost_usd for r in runs), 6),
        "p50_seconds": round(statistics.median(r.seconds for r in runs), 3),
        "per_class": per_class,
        "confusion": {e: dict(Counter(r.predicted for r in runs if r.expected == e)) for e in CLASSES},
        "by_category": {
            cat: {
                "n": sum(1 for r in runs if r.category == cat),
                "correct": sum(1 for r in runs if r.category == cat and r.predicted == r.expected),
            }
            for cat in sorted({r.category for r in runs})
        },
    }


def _fmt(v: Any, digits: int = 3) -> str:
    return "n/a" if v is None else (f"{v:.{digits}f}" if isinstance(v, float) else str(v))


def render(results: dict[str, dict[str, RowResult]], settings: Settings) -> tuple[str, dict[str, Any]]:
    lines = [
        "# Adjudication ablation",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`. Pre-registration: "
        "evals/adjudication/PREREG.md (Revision 2). LLM_MODEL_ID="
        f"{settings.llm_model_id}, LLM_MODE={settings.llm_mode}.",
        "",
        "- Every row that reads photos gets the same claim policy (outcome_policy.yaml with photo_rules): the "
        "rules_plus_vision decision table in code, the agent rows in their instructions. rules_only cannot read "
        "photos.",
        "- Counted steps are model calls plus rejected tool calls (the cap counts these); rows are trace rows written. "
        "Cost is nominal (recorded usage times the price table), the same in live and replay. Seconds use the replay "
        "virtual clock (recorded live latency).",
        "- loop_full_cheap_model is not applicable: one model is priced, and it is already the cheapest vision model.",
        "",
    ]
    payload: dict[str, Any] = {"model": settings.llm_model_id, "blocks": {}}
    head = (
        "| row | n | outcome acc | macro F1 | approval acc | fraud P | fraud R | needs_info P | routed | capped "
        "| counted steps | rows | mean cost USD | p50 s |"
    )
    for block_key, rows in results.items():
        block = BLOCKS[block_key]
        lines += [f"**{block.title}**", "", head, "|" + "---|" * 14]
        payload["blocks"][block_key] = {}
        for row in ROWS:
            rr = rows.get(row)
            if rr is None:
                continue
            if not rr.complete:
                have = sum(1 for r in rr.runs if not (r.missing or r.error))
                lines.append(f"| {row} | {len(rr.runs)} | not run ({have}/{len(rr.runs)} recorded) |" + " |" * 11)
                payload["blocks"][block_key][row] = {"status": "not run", "recorded": have, "n": len(rr.runs)}
                continue
            m = metrics(rr.runs)
            payload["blocks"][block_key][row] = m
            lines.append(
                f"| {row} | {m['n']} | {m['outcome_accuracy']:.3f} | **{m['macro_f1']:.3f}** | "
                f"{m['approval_accuracy']:.3f} | {_fmt(m['fraud_precision'])} | {_fmt(m['fraud_recall'])} | "
                f"{_fmt(m['needs_info_precision'])} | {m['routed_share']:.3f} | {m['capped_share']:.3f} | "
                f"{m['mean_counted_steps']:.1f} | {m['mean_rows']:.1f} | {m['mean_cost_usd']:.5f} | "
                f"{m['p50_seconds']:.2f} |"
            )
        cats = sorted({r.category for rr in rows.values() for r in rr.runs}) if block_key != "golden300" else []
        if cats:
            complete_rows = [row for row in ROWS if row in rows and rows[row].complete]
            lines += [
                "",
                "Correct by category:",
                "",
                "| category | n | " + " | ".join(complete_rows) + " |",
                "|" + "---|" * (len(complete_rows) + 2),
            ]
            for cat in cats:
                n = sum(1 for r in next(iter(rows.values())).runs if r.category == cat)
                cells = [
                    str(sum(1 for r in rows[row].runs if r.category == cat and r.predicted == r.expected))
                    for row in complete_rows
                ]
                lines.append(f"| {cat} | {n} | " + " | ".join(cells) + " |")
        lines.append("")
    golden = payload["blocks"].get("golden300", {})
    ro, lf = golden.get("rules_only", {}), golden.get("loop_full", {})
    if "macro_f1" in ro and "macro_f1" in lf:
        gap = lf["macro_f1"] - ro["macro_f1"]
        fired = gap <= 0.05
        lines.append(
            f"**Kill criterion (golden300):** loop_full macro F1 {lf['macro_f1']:.3f} minus rules_only "
            f"{ro['macro_f1']:.3f} = {gap:+.3f}. "
            + (
                "Within 0.05: the criterion fires, so the agent's value is in fraud detection, photo reading, and "
                "human routing (see probe60 and standin40), and the write-up leads with those numbers."
                if fired
                else "More than 0.05: the criterion does not fire."
            )
        )
        payload["kill_criterion"] = {"gap": round(gap, 4), "fired": fired}
    else:
        lines.append("**Kill criterion (golden300):** not evaluable until rules_only and loop_full are both complete.")
    return "\n".join(lines) + "\n", payload


def main(argv: list[str] | None = None, results_dir: Path = RESULTS) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", nargs="*", default=list(ROWS), choices=ROWS)
    parser.add_argument("--blocks", nargs="*", default=list(BLOCKS), choices=list(BLOCKS))
    parser.add_argument("--workers", type=int, default=1, help="concurrent claims in a live run")
    parser.add_argument("--no-write", action="store_true", help="record only; do not rewrite the results table")
    args = parser.parse_args(argv or [])
    settings = get_settings()
    live = settings.llm_mode == "live"
    engine = internal_engine()
    try:
        llm: LlmClient | None = build_client(settings)
    except LiveCallsDisabled:
        llm = None
    results: dict[str, dict[str, RowResult]] = {}
    for block_key in args.blocks:
        block = BLOCKS[block_key]
        if not block.claims_file.exists():
            log_event(log, "ablation_block_missing", block=block_key)
            continue
        results[block_key] = {}
        for row in args.rows:
            if row != "rules_only" and llm is None:
                continue
            try:
                results[block_key][row] = run_row(row, block, llm, settings, engine, live, args.workers)
            except BudgetExhausted as exc:
                log_event(log, "ablation_budget_exhausted", level=40, error=str(exc))
                raise
    md, payload = render(results, settings)
    if not args.no_write:
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "ablation.md").write_text(md, encoding="utf-8")
        (results_dir / "ablation.json").write_text(json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8")
    return payload


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        block: {row: (m.get("macro_f1") if "macro_f1" in m else m.get("status")) for row, m in rows.items()}
        for block, rows in payload["blocks"].items()
    }


if __name__ == "__main__":
    configure(get_settings().log_level)
    print(json.dumps(_summary(main(sys.argv[1:])), indent=1))
