"""Extraction eval: baseline vs graph on the golden notices (PREREG.md). Writes evals/results/extraction.md
and extraction.json. The graph runs through the configured LLM_MODE; in replay mode a missing recording is
reported as "not run", never silently scored as an empty extraction."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from recallops.evals.golden import GoldenNotice, load_golden, notice_text
from recallops.evals.metrics import HEADLINE_FIELDS, FieldTable, score
from recallops.llm.client import LiveCallsDisabled, LlmClient, build_client
from recallops.rules import baseline, graph
from recallops.rules.schemas import ExtractedRules
from recallops.settings import REPO_ROOT, Settings, get_settings
from recallops.tracing.writer import MemoryTrace

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


def run_graph(notices: list[GoldenNotice], llm: LlmClient | None, reason_if_none: str) -> GraphRun:
    if llm is None:
        return GraphRun("not run", reason_if_none, 0, "0", {}, {})
    outcomes: dict[str, int] = {}
    predictions: dict[str, ExtractedRules | None] = {}
    calls, cost = 0, 0.0
    for n in notices:
        result = graph.extract(notice_text(EXTRACTION_DIR / "notices", n.source_id), llm, MemoryTrace("extract_rules"))
        outcomes[result.status] = outcomes.get(result.status, 0) + 1
        predictions[n.source_id] = result.rules
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
    return GraphRun("run", "", calls, f"{cost:.6f}", outcomes, predictions)


def _row(name: str, table: FieldTable) -> str:
    cells = " | ".join(f"{table.per_field[f]:.1f}" for f in HEADLINE_FIELDS)
    grounded = f"{100 * table.span_grounded_rate:.1f}" if table.span_grounded_rate is not None else "n/a"
    return f"| {name} | {table.n} | {cells} | **{table.headline:.1f}** | {grounded} |"


def render(
    notices: list[GoldenNotice],
    base_all: FieldTable,
    base_heldout: FieldTable,
    graph_run: GraphRun,
    graph_all: FieldTable | None,
    graph_heldout: FieldTable | None,
    settings: Settings,
) -> str:
    header_fields = " | ".join(HEADLINE_FIELDS)
    lines = [
        "# Extraction eval",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} by `make eval`. Pre-registration: "
        "evals/extraction/PREREG.md.",
        "",
        f"- Golden set: {len(notices)} notices, **{notices[0].label_status}** (labeled blind in session 1 before "
        "either extractor existed; the owner audits them in M2).",
        f"- Baseline: rules/baseline.py {baseline.EXTRACTOR_VERSION}. It was developed on the 15 baseline-test "
        "notices, which are inside the golden set, so the 25 held-out notices are the fair baseline number.",
        f"- Graph: rules/graph.py {graph.EXTRACTOR_VERSION}, LLM_MODEL_ID={settings.llm_model_id}, "
        f"prompt sha256 {graph.PROMPT_SHA256[:16]}, LLM_MODE={settings.llm_mode}.",
        "- Scores are points (0 to 100): exact match for scalars, set F1 for lists (both empty counts as 1.0). "
        "Headline is the unweighted mean of the 11 fields. Grounded is the share of evidence spans whose quote "
        "equals the text at its offsets.",
        "",
        f"| extractor | n | {header_fields} | headline | grounded |",
        "|" + "---|" * (len(HEADLINE_FIELDS) + 4),
        _row("baseline, all", base_all),
        _row("baseline, 25 held-out", base_heldout),
    ]
    if graph_run.status == "run" and graph_all and graph_heldout:
        lines += [_row("graph, all", graph_all), _row("graph, 25 held-out", graph_heldout)]
        delta = graph_all.headline - base_all.headline
        verdict = (
            f"Graph minus baseline headline = {delta:+.1f} points. "
            + ("Kill criterion met (5.0 or less): " if delta <= 5.0 else "Kill criterion not met: ")
            + "provisional on the unaudited M1 set; the 100-notice audited set in M2 decides."
        )
        lines += [
            "",
            f"Graph runs: {graph_run.outcomes}, {graph_run.calls} model calls, {graph_run.cost_usd} USD.",
            "",
            f"**Kill criterion (provisional):** {verdict}",
        ]
    else:
        lines += [
            f"| graph | 0 | {' | '.join('not run' for _ in HEADLINE_FIELDS)} | not run | not run |",
            "",
            f"**Graph not run:** {graph_run.reason}. Model calls: {graph_run.calls}, spend: {graph_run.cost_usd} USD.",
            "",
            "**Kill criterion:** not evaluable in M1. It needs one approved live recording run of the graph "
            "(estimated below) or the M2 run.",
        ]
    return "\n".join(lines) + "\n"


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


def main(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    settings = get_settings()
    notices = load_golden(EXTRACTION_DIR / "golden_notices.json")
    texts = {n.source_id: notice_text(EXTRACTION_DIR / "notices", n.source_id) for n in notices}
    base_preds = {n.source_id: baseline.extract(texts[n.source_id]) for n in notices}

    def table(preds: dict[str, ExtractedRules | None], subset: list[GoldenNotice]) -> FieldTable:
        return score([(n.rules, preds.get(n.source_id), texts[n.source_id]) for n in subset])

    heldout = [n for n in notices if not n.baseline_test]
    base_all, base_heldout = table(dict(base_preds), notices), table(dict(base_preds), heldout)
    try:
        llm: LlmClient | None = build_client(settings)
        reason = ""
    except LiveCallsDisabled as exc:
        llm, reason = None, str(exc)
    graph_run = run_graph(notices, llm, reason)
    graph_all = graph_heldout = None
    if graph_run.status == "run":
        graph_all, graph_heldout = (
            table(graph_run.predictions, notices),
            table(graph_run.predictions, heldout),
        )
    estimate = estimate_live_cost(notices, settings)
    md = render(notices, base_all, base_heldout, graph_run, graph_all, graph_heldout, settings)
    md += (
        f"\nWorst-case cost of one live recording run of the graph on these {len(notices)} notices "
        f"(conservative token estimate, {settings.extract_max_calls} calls each): {estimate} USD.\n"
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "extraction.md").write_text(md, encoding="utf-8")
    payload = {
        "label_status": notices[0].label_status,
        "n": len(notices),
        "baseline_all": asdict(base_all),
        "baseline_heldout": asdict(base_heldout),
        "graph_status": graph_run.status,
        "graph_reason": graph_run.reason,
        "graph_all": asdict(graph_all) if graph_all else None,
        "graph_heldout": asdict(graph_heldout) if graph_heldout else None,
        "graph_calls": graph_run.calls,
        "graph_cost_usd": graph_run.cost_usd,
        "live_run_worst_case_usd": estimate,
    }
    (results_dir / "extraction.json").write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
