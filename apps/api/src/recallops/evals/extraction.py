"""Extraction eval: baseline vs graph on the golden notices (PREREG.md). Writes evals/results/extraction.md
and extraction.json. The graph runs through the configured LLM_MODE; in replay mode a missing recording is
reported as "not run", never silently scored as an empty extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recallops.db.engine import internal_engine
from recallops.evals.golden import GoldenNotice, load_golden, notice_text
from recallops.evals.metrics import HEADLINE_FIELDS, FieldTable, score
from recallops.llm.client import LiveCallsDisabled, LlmClient, build_client
from recallops.models import Recall
from recallops.rules import baseline, graph
from recallops.rules.schemas import ExtractedRules
from recallops.settings import REPO_ROOT, Settings, get_settings
from recallops.tracing.writer import DbTrace, MemoryTrace, TraceSink

EXTRACTION_DIR = REPO_ROOT / "evals" / "extraction"
RESULTS_DIR = REPO_ROOT / "evals" / "results"


@dataclass
class GraphRun:
    status: str  # "run" or "not run"
    reason: str
    calls: int
    cost_usd: str
    outcomes: dict[str, int]
    predictions: dict[str, ExtractedRules | None]
    drafts: dict[str, ExtractedRules | None] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)


def _trace(llm: LlmClient, source_id: str) -> TraceSink:
    """Live runs write real agent_runs and agent_steps rows (archive extraction, no firm): agent_steps.cost_usd is
    the spend ledger the meter reads, so the global budget stop sees every live call. Replay costs nothing and
    keeps the trace in memory."""
    if llm.settings.llm_mode != "live":
        return MemoryTrace("extract_rules")
    engine = internal_engine()
    with Session(engine) as session:
        recall_id = session.scalar(select(Recall.id).where(Recall.source == "cpsc", Recall.source_id == source_id))
    return DbTrace(engine, "extract_rules", firm_id=None, recall_id=recall_id, model_id=llm.model_id)


def run_graph(notices: list[GoldenNotice], llm: LlmClient | None, reason_if_none: str) -> GraphRun:
    if llm is None:
        return GraphRun("not run", reason_if_none, 0, "0", {}, {})
    outcomes: dict[str, int] = {}
    predictions: dict[str, ExtractedRules | None] = {}
    drafts: dict[str, ExtractedRules | None] = {}
    dropped: dict[str, int] = {}
    calls, cost = 0, 0.0
    for n in notices:
        result = graph.extract(notice_text(EXTRACTION_DIR / "notices", n.source_id), llm, _trace(llm, n.source_id))
        outcomes[result.status] = outcomes.get(result.status, 0) + 1
        predictions[n.source_id] = result.rules
        drafts[n.source_id] = result.draft
        for item in result.dropped:
            key = item.split(":", 1)[0]
            dropped[key] = dropped.get(key, 0) + 1
        calls += result.calls
        cost += float(result.cost_usd)
    if outcomes.get("done", 0) == 0:
        return GraphRun(
            "not run",
            f"no successful extraction ({outcomes}); replay recordings missing",
            calls,
            f"{cost:.6f}",
            outcomes,
            {},
        )
    return GraphRun("run", "", calls, f"{cost:.6f}", outcomes, predictions, drafts, dropped)


def _row(name: str, table: FieldTable) -> str:
    cells = " | ".join(f"{table.per_field[f]:.1f}" for f in HEADLINE_FIELDS)
    grounded = f"{100 * table.span_grounded_rate:.1f}" if table.span_grounded_rate is not None else "n/a"
    return f"| {name} | {table.n} | {cells} | **{table.headline:.1f}** | {grounded} |"


def _missing_row(name: str, n: int, reason: str) -> str:
    return f"| {name} | {n} | {' | '.join('-' for _ in HEADLINE_FIELDS)} | {reason} | - |"


@dataclass
class Block:
    key: str
    title: str
    notices: list[GoldenNotice]
    baseline: FieldTable
    graph: FieldTable | None
    graph_draft: FieldTable | None
    recorded: int


def estimate_live_cost(notices: list[GoldenNotice], settings: Settings) -> str:
    from recallops.llm.pricing import PriceTable, estimate_text_tokens

    prices = PriceTable.from_settings(settings)
    schema_tokens = estimate_text_tokens(
        json.dumps(ExtractedRules.model_json_schema()), settings.llm_chars_per_token_estimate
    )
    prompt_tokens = estimate_text_tokens(graph.SYSTEM_PROMPT, settings.llm_chars_per_token_estimate)
    worst = 0.0
    for n in notices:
        text_tokens = estimate_text_tokens(
            notice_text(EXTRACTION_DIR / "notices", n.source_id), settings.llm_chars_per_token_estimate
        )
        per_call = prices.cost(
            settings.llm_model_id, text_tokens + schema_tokens + prompt_tokens, settings.llm_max_output_tokens
        )
        worst += float(per_call) * settings.extract_max_calls
    return f"{worst:.4f}"


def render(blocks: list[Block], graph_run: GraphRun, settings: Settings, label_sha: str, label_status: str) -> str:
    header_fields = " | ".join(HEADLINE_FIELDS)
    table_head = [
        f"| extractor | n | {header_fields} | headline | grounded |",
        "|" + "---|" * (len(HEADLINE_FIELDS) + 4),
    ]
    decide = next(b for b in blocks if b.key == "new60")
    lines = [
        "# Extraction eval",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`. Pre-registration: "
        "evals/extraction/PREREG.md (Revisions 1 and 2).",
        "",
        f"- Golden set: 100 notices, **{label_status}**; golden_notices.json sha256 {label_sha[:16]}. The 60 "
        "extension notices were labeled blind by sub-agents and audited before either extractor ran on them; the "
        "40 M1 notices were labeled and audited by the main session, which had seen both extractors' M1 results.",
        f"- Baseline: rules/baseline.py {baseline.EXTRACTOR_VERSION}, developed only on the 15 baseline-test notices.",
        f"- Graph: rules/graph.py {graph.EXTRACTOR_VERSION}, LLM_MODEL_ID={settings.llm_model_id}, "
        f"prompt sha256 {graph.PROMPT_SHA256[:16]}, LLM_MODE={settings.llm_mode}.",
        "- Scores are points (0 to 100): exact match for scalars, set F1 for lists (both empty counts as 1.0). "
        "Headline is the unweighted mean of the 11 fields. Grounded is the share of evidence spans whose quote "
        "equals the text at its offsets.",
        "",
    ]
    for block in blocks:
        lines += [f"**{block.title}**", "", *table_head, _row("baseline", block.baseline)]
        if block.graph is not None:
            lines.append(_row("graph", block.graph))
        else:
            lines.append(_missing_row("graph", len(block.notices), f"not run ({block.recorded}/{len(block.notices)})"))
        if block.graph_draft is not None and block.key == "new60":
            lines.append(_row("graph draft before verify_spans (diagnostic)", block.graph_draft))
        lines.append("")
    if decide.graph is not None:
        delta = decide.graph.headline - decide.baseline.headline
        verdict = f"on the 60 blind extension notices, graph minus baseline headline = {delta:+.1f} points. " + (
            "Kill criterion met (5.0 or less): the model-based extractor is demoted to a fallback."
            if delta <= 5.0
            else "Kill criterion not met: the model-based extractor stays primary."
        )
    else:
        verdict = f"not evaluable: the graph has recordings for {decide.recorded} of the 60 extension notices."
    lines += [
        f"**Kill criterion (decides, PREREG Revision 2):** {verdict}",
        "",
        f"Graph runs: {graph_run.outcomes}, {graph_run.calls} model calls, {graph_run.cost_usd} USD nominal"
        + (" as recorded (replaying costs nothing)." if settings.llm_mode == "replay" else "."),
        "",
        f"Values dropped by verify_spans, by field: {dict(sorted(graph_run.dropped.items(), key=lambda kv: -kv[1]))}.",
    ]
    return "\n".join(lines) + "\n"


def main(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    settings = get_settings()
    golden_path = EXTRACTION_DIR / "golden_notices.json"
    notices = load_golden(golden_path)
    preaudit = {n.source_id: n for n in load_golden(EXTRACTION_DIR / "golden_notices_m1_preaudit.json")}
    texts = {n.source_id: notice_text(EXTRACTION_DIR / "notices", n.source_id) for n in notices}
    base_preds: dict[str, ExtractedRules | None] = {n.source_id: baseline.extract(texts[n.source_id]) for n in notices}
    try:
        llm: LlmClient | None = build_client(settings)
        reason = ""
    except LiveCallsDisabled as exc:
        llm, reason = None, str(exc)
    graph_run = run_graph(notices, llm, reason)
    recorded = {sid for sid, rules in graph_run.predictions.items() if rules is not None}

    def table(preds: dict[str, ExtractedRules | None], subset: list[GoldenNotice]) -> FieldTable:
        return score([(n.rules, preds.get(n.source_id), texts[n.source_id]) for n in subset])

    def block(key: str, title: str, subset: list[GoldenNotice]) -> Block:
        have = sum(1 for n in subset if n.source_id in recorded)
        complete = graph_run.status == "run" and have == len(subset)
        return Block(
            key,
            title,
            subset,
            table(base_preds, subset),
            table(graph_run.predictions, subset) if complete else None,
            table(graph_run.drafts, subset) if complete else None,
            have,
        )

    m1 = [n for n in notices if n.source_id in preaudit]
    blocks = [
        block("new60", "Decides: the 60 blind extension notices", [n for n in notices if n.source_id not in preaudit]),
        block("all100", "Context: all 100 notices", notices),
        block(
            "outside_test85",
            "Context: the 85 notices outside the baseline-test 15",
            [n for n in notices if not n.baseline_test],
        ),
        block("m1_audited", "Context: the 40 M1 notices, audited labels", m1),
        block(
            "m1_preaudit",
            "Context: the 40 M1 notices, pre-audit labels (audit drift check)",
            [preaudit[n.source_id] for n in m1],
        ),
    ]
    label_sha = hashlib.sha256(golden_path.read_bytes()).hexdigest()
    md = render(blocks, graph_run, settings, label_sha, notices[0].label_status)
    md += (
        f"\nWorst-case cost of one live recording run of the graph on all {len(notices)} notices "
        f"(conservative token estimate, {settings.extract_max_calls} calls each): "
        f"{estimate_live_cost(notices, settings)} USD.\n"
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "extraction.md").write_text(md, encoding="utf-8")
    by_key = {b.key: b for b in blocks}
    payload = {
        "label_status": notices[0].label_status,
        "golden_sha256": label_sha,
        "n": len(notices),
        "baseline_all": asdict(by_key["all100"].baseline),
        "baseline_heldout": asdict(by_key["outside_test85"].baseline),
        "blocks": {
            b.key: {
                "n": len(b.notices),
                "baseline": asdict(b.baseline),
                "graph": asdict(b.graph) if b.graph else None,
                "graph_draft": asdict(b.graph_draft) if b.graph_draft else None,
                "graph_recorded": b.recorded,
            }
            for b in blocks
        },
        "graph_status": graph_run.status,
        "graph_reason": graph_run.reason,
        "graph_dropped_by_field": graph_run.dropped,
        "graph_calls": graph_run.calls,
        "graph_cost_usd": graph_run.cost_usd,
    }
    (results_dir / "extraction.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
