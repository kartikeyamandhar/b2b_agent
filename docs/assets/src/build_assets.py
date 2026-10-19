"""Build the README figures from committed results and the seeded development database.

    make readme-assets                         the three SVGs (the fraud figure needs `make seed` first)
    make readme-assets ARGS=--photos           also the sample claim photos, copied from the golden and probe
                                               photos under data/generated (python -m recallops.synth --golden and
                                               python -m recallops.synth.probe render them); each copy is checked
                                               against the sha256 committed in evals/adjudication

Every number in these three SVGs is read at build time: evals/results/*.json, the pre-registered constants of
tests/db/test_fraud_golden.py, and Postgres (fraud_clusters, agent_steps). Nothing in CI rebuilds or compares the
figures, so they match the tables as of the last run; rerun this after `make eval` or `make seed` changes them. The
other four SVGs in docs/assets (banner, architecture, agent loop, payout states) are hand-written.

The spend tile reads the meter's ledger. Replayed calls are charged 0, so only the database of the machine that made
the live recordings holds the project's spend; anywhere else the ledger reads 0 and the build stops instead of
drawing 0.00 USD.
Output is static SVG 1.1 (no script, no external fonts, links, or images), themed with an inline stylesheet whose
prefers-color-scheme block swaps the same classes to dark values.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "docs" / "assets"
IMG = ASSETS / "img"
RESULTS = ROOT / "evals" / "results"
ADJ = ROOT / "evals" / "adjudication"
FRAUD_TEST = ROOT / "apps" / "api" / "tests" / "db" / "test_fraud_golden.py"
MAX_BYTES = 120_000
FONT = 'system-ui, -apple-system, "Segoe UI", Helvetica, Arial, sans-serif'

# Theme tokens, light then dark. Text classes only ever use ink tokens; hues go on marks.
TOKENS: dict[str, tuple[str, str]] = {
    "card": ("fill:#fcfcfb;stroke:#0b0b0b;stroke-opacity:0.10", "fill:#1a1a19;stroke:#ffffff;stroke-opacity:0.10"),
    "ink": ("fill:#0b0b0b", "fill:#f0efec"),
    "ink2": ("fill:#52514e", "fill:#c3c2b7"),
    "muted": ("fill:#898781", "fill:#898781"),
    "grid": ("stroke:#e1e0d9", "stroke:#2c2c2a"),
    "axis": ("stroke:#c3c2b7", "stroke:#383835"),
}
# Categorical accent slots in fixed order (light, dark).
ACCENT: dict[int, tuple[str, str]] = {
    1: ("#2a78d6", "#3987e5"),
    2: ("#eb6834", "#d95926"),
    3: ("#1baf7a", "#199e70"),
    4: ("#eda100", "#c98500"),
    5: ("#e87ba4", "#d55181"),
    6: ("#008300", "#008300"),
    7: ("#6250d6", "#9085e9"),
}
CARD = ("#fcfcfb", "#1a1a19")

# Approximate advance widths (per 1000 em) of Verdana, the widest common fallback for system-ui, so a label that
# fits here fits in SF Pro, Segoe UI, and Helvetica too. Unknown characters count as 640.
_W: dict[str, int] = {" ": 352, ".": 364, ",": 364, ":": 454, ";": 454, "/": 454, "-": 431, "(": 454, ")": 454}
_W.update({"=": 818, "+": 818, "%": 1076, "_": 636, "'": 269})
_W.update(dict.fromkeys("0123456789", 636))
_LOWER = "601 623 521 623 596 352 623 633 274 344 592 274 973 633 607 623 623 427 521 394 633 592 818 592 592 525"
_UPPER = "684 686 698 771 632 575 775 751 421 455 693 557 843 748 787 603 787 695 684 616 732 684 989 685 615 685"
_W.update(zip("abcdefghijklmnopqrstuvwxyz", map(int, _LOWER.split()), strict=True))
_W.update(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", map(int, _UPPER.split()), strict=True))


def text_width(s: str, size: float, weight: int = 400) -> float:
    w = sum(_W.get(ch, 640) for ch in s) / 1000 * size
    return w * (1.08 if weight >= 600 else 1.0)


def wrap(s: str, size: float, max_w: float, weight: int = 400) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if line and text_width(trial, size, weight) > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    if len(lines) == 2:  # balance two lines instead of leaving one orphaned word
        words = s.split()
        splits = [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))]
        lines = min(splits, key=lambda pair: max(text_width(part, size, weight) for part in pair))
    for ln in lines:
        if text_width(ln, size, weight) > max_w:
            raise ValueError(f"{ln!r} does not fit in {max_w:.0f}px at {size}px")
    return lines


def fmt(x: float) -> str:
    """Coordinates with at most two decimals and no trailing zeros."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


class Svg:
    """A static SVG card: one inline stylesheet (light by default, dark under prefers-color-scheme)."""

    def __init__(self, width: int, height: int, title: str, desc: str, classes: dict[str, tuple[str, str]]) -> None:
        self.width, self.height, self.title, self.desc = width, height, title, desc
        self.classes = {**TOKENS, **classes}
        self.body: list[str] = []

    def add(self, element: str) -> None:
        self.body.append(element)

    def text(self, x: float, y: float, s: str, cls: str, size: float, weight: int = 400, anchor: str = "start") -> None:
        attrs = f'x="{fmt(x)}" y="{fmt(y)}" class="{cls}" font-size="{fmt(size)}"'
        if weight != 400:
            attrs += f' font-weight="{weight}"'
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        self.add(f"<text {attrs}>{escape(s)}</text>")

    def render(self) -> str:
        light = "\n".join(f"  .{k} {{ {v[0]} }}" for k, v in self.classes.items())
        dark = "\n".join(f"    .{k} {{ {v[1]} }}" for k, v in self.classes.items())
        w, h = self.width, self.height
        out = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                (
                    f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{w}" height="{h}" '
                    f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">'
                ),
                f'<title id="title">{escape(self.title)}</title>',
                f'<desc id="desc">{escape(self.desc)}</desc>',
                "<style>",
                f"  text {{ font-family: {FONT}; }}",
                light,
                "  @media (prefers-color-scheme: dark) {",
                dark,
                "  }",
                "</style>",
                f'<rect class="card" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="16" stroke-width="1"/>',
                *self.body,
                "</svg>",
                "",
            ]
        )
        check_text(out)
        return out


def check_text(svg: str) -> None:
    if "\u2014" in svg:  # em dash
        raise ValueError("em dash in SVG text")
    if re.search(r"\bM[1-9]\b", svg):
        raise ValueError("milestone label in SVG text")
    if len(svg.encode()) > MAX_BYTES:
        raise ValueError(f"SVG is {len(svg.encode())} bytes, over {MAX_BYTES}")


def bar_path(x0: float, y: float, length: float, thickness: float, r: float = 4) -> str:
    """Horizontal bar, square at the baseline (x0), rounded data end."""
    r = min(r, length / 2, thickness / 2)
    x1 = x0 + length
    return (
        f"M{fmt(x0)} {fmt(y)}H{fmt(x1 - r)}A{fmt(r)} {fmt(r)} 0 0 1 {fmt(x1)} {fmt(y + r)}"
        f"V{fmt(y + thickness - r)}A{fmt(r)} {fmt(r)} 0 0 1 {fmt(x1 - r)} {fmt(y + thickness)}H{fmt(x0)}Z"
    )


def write(name: str, svg: str) -> Path:
    path = ASSETS / name
    path.write_text(svg, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)} ({len(svg.encode())} bytes)")
    return path


# --------------------------------------------------------------------------------------------- facts


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


ABLATION_ROWS: list[tuple[str, str]] = [
    ("rules_only", "Rules only"),
    ("rules_plus_vision", "Rules + vision"),
    ("single_pass", "Single pass"),
    ("loop_full", "Agent loop (full)"),
    ("loop_no_retrieval", "Agent, no retrieval"),
    ("loop_keyword_only", "Agent, keyword retrieval"),
]
# Block id, panel title, panel subtitle. standin40 is the agent-written stand-in for the classmate set, written by a
# sub-agent that never saw the generator, tools, or labels (evals/adjudication/PREREG.md).
ABLATION_BLOCKS: list[tuple[str, str, str]] = [
    ("golden300", "Synthetic golden claims", "golden300, n={n}"),
    ("probe60", "Rules-blind probe", "probe60, assistant-designed, n={n}"),
    ("standin40", "Written blind to the generator", "standin40, agent-written, n={n}"),
]


def ablation_metric(ablation: dict[str, Any], block: str, row: str, metric: str) -> float:
    try:
        value = ablation["blocks"][block][row][metric]
    except KeyError as exc:
        raise SystemExit(f"evals/results/ablation.json has no {block} -> {row} -> {metric}") from exc
    if value is None:
        raise SystemExit(f"evals/results/ablation.json {block} -> {row} -> {metric} is null")
    return float(value)


def fraud_test_expected() -> int:
    """The pre-registered component count the fraud golden test asserts (graph components == check_duplicates)."""
    tree = ast.parse(FRAUD_TEST.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXPECTED" for t in node.targets):
            expected: dict[str, int] = ast.literal_eval(node.value)
            return sum(expected.values())
    raise SystemExit(f"{FRAUD_TEST.relative_to(ROOT)} has no EXPECTED constant")


def model_spend() -> tuple[Decimal, str]:
    """The meter's ledger: carried spend plus sum(agent_steps.cost_usd), read as recallops_internal."""
    from sqlalchemy import text

    from recallops.db.engine import internal_engine
    from recallops.settings import get_settings

    try:
        with internal_engine().connect() as conn:
            total = conn.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM agent_steps")).scalar_one()
    except Exception as exc:
        raise SystemExit(f"model spend needs the dev database (make db-up): {exc}") from exc
    carried = Decimal(get_settings().llm_spend_carried_usd)
    spend = Decimal(total) + carried
    if spend <= 0:
        raise SystemExit(
            "the spend ledger reads 0: replayed calls are free, so only the machine that made the live recordings "
            "holds the project's spend; run this there"
        )
    return spend, f"agent_steps sum {total} + carried {carried} (dev database)"


@dataclass(frozen=True)
class Cluster:
    kind: str
    members: tuple[str, ...]


def seed_clusters() -> tuple[list[Cluster], dict[str, Any]]:
    """fraud_clusters of the seed's demo program, limited to clusters whose members are all seed-generated claims.

    The seed's claim ids are deterministic (synth.generate: uuid5 of "<seed>:<program>:<i>"), so claims filed later
    through the portal or a trace driver are excluded and the figure always shows what `make seed` produced."""
    from sqlalchemy import text

    from recallops import seed
    from recallops.db.engine import internal_engine
    from recallops.synth.generate import NAMESPACE

    params = inspect.signature(seed.run).parameters
    n, rng_seed = int(params["n"].default), int(params["seed"].default)
    firm = seed.DEMO_FIRM_ID
    program = seed.program_id_for(firm, seed.DEMO_RECALL)
    seeded = {str(uuid.uuid5(NAMESPACE, f"{rng_seed}:{program}:{i}")) for i in range(n)}
    try:
        with internal_engine().connect() as conn:
            rows = conn.execute(
                text("SELECT kind, member_claim_ids FROM fraud_clusters WHERE firm_id = :f AND program_id = :p"),
                {"f": firm, "p": program},
            ).all()
            claims = conn.execute(
                text("SELECT count(*) FROM claims WHERE firm_id = :f AND program_id = :p"), {"f": firm, "p": program}
            ).scalar_one()
    except Exception as exc:
        raise SystemExit(f"fraud clusters need the dev database (make db-up, make seed): {exc}") from exc
    if not rows:
        raise SystemExit("fraud_clusters is empty for the seed program; run make seed (with Neo4j up) first")
    kept = [
        Cluster(kind, tuple(sorted(str(m) for m in members)))
        for kind, members in rows
        if all(str(m) in seeded for m in members)
    ]
    kept.sort(key=lambda c: (c.kind, -len(c.members), c.members))
    meta = {
        "firm_id": str(firm),
        "program_id": str(program),
        "seed_claims": n,
        "program_claims": int(claims),
        "rows": len(rows),
        "kept": len(kept),
        "excluded_non_seed": len(rows) - len(kept),
    }
    return kept, meta


# --------------------------------------------------------------------------------------------- figure 1


def results_at_a_glance(facts: dict[str, Any]) -> str:
    width, height, pad, inner, cols = 1200, 224, 16, 20, 5
    cw = (width - 2 * pad) / cols
    text_w = cw - 2 * inner
    # (label, [(value part, size ratio, class)], context)
    tiles: list[tuple[str, list[tuple[str, float, str]], str]] = [
        (
            "Fraud recall on the synthetic claims",
            [(f"{facts['fraud_recall']:.3f}", 1.0, "ink")],
            f"rules and agent both; {facts['blind_fraud_recall']:.3f} on the 40 agent-written",
        ),
        (
            "Agent vs rules on 40 agent-written claims",
            [(f"{facts['blind_agent_f1']:.3f}", 1.0, "ink"), (f"vs {facts['blind_rules_f1']:.3f}", 0.5, "ink")],
            "macro F1; written blind to the generator",
        ),
        (
            "MPR discrepancies",
            [(str(facts["mpr_discrepancies"]), 1.0, "ink")],
            "SQL report = Polars recount = scenario truth",
        ),
        (
            "Duplicate groups reproduced by the graph",
            [(f"{facts['graph_groups']} / {facts['graph_groups']}", 1.0, "ink")],
            "Neo4j vs check_duplicates, asserted by a CI test",
        ),
        (
            "Model spend, whole project",
            [(f"{facts['spend_usd']:.2f}", 1.0, "ink"), ("USD", 0.5, "ink2")],
            "every eval replays offline",
        ),
    ]
    gap = 7

    def value_width(parts: list[tuple[str, float, str]], size: float) -> float:
        return sum(text_width(p, size * r, 600 if r == 1 else 500) for p, r, _ in parts) + gap * (len(parts) - 1)

    size = next((s for s in range(40, 29, -1) if all(value_width(t[1], s) <= text_w for t in tiles)), None)
    if size is None:
        raise ValueError("stat values do not fit their tiles")
    svg = Svg(
        width,
        height,
        "RecallOps results at a glance",
        "Five headline numbers. Fraud recall on the 300 synthetic golden claims: "
        f"{facts['fraud_recall']:.3f} for the rules-only adjudicator and the agent, "
        f"{facts['blind_fraud_recall']:.3f} on the 40 agent-written claims (written blind to the generator). "
        f"Macro F1 on those 40 claims: agent loop {facts['blind_agent_f1']:.3f}, rules only "
        f"{facts['blind_rules_f1']:.3f}, rules plus vision {facts['blind_vision_f1']:.3f}. "
        "Monthly progress report discrepancies: "
        f"{facts['mpr_discrepancies']}. Duplicate groups the Neo4j graph reproduces from check_duplicates: "
        f"{facts['graph_groups']} of {facts['graph_groups']}. Model spend for the whole project: "
        f"{facts['spend_usd']:.2f} USD.",
        {"accent": (f"fill:{ACCENT[1][0]}", f"fill:{ACCENT[1][1]}")},
    )
    label_size, ctx_size, line = 14, 13, 19
    for i, (label, parts, context) in enumerate(tiles):
        x = pad + i * cw + inner
        if i:
            svg.add(
                f'<line class="grid" x1="{fmt(pad + i * cw)}" y1="30" x2="{fmt(pad + i * cw)}" '
                f'y2="{height - 30}" stroke-width="1"/>'
            )
        svg.add(f'<rect class="accent" x="{fmt(x)}" y="30" width="24" height="3" rx="1.5"/>')
        for j, ln in enumerate(wrap(label, label_size, text_w, 500)):
            svg.text(x, 60 + j * line, ln, "ink2", label_size, 500)
        tspans = []
        for k, (part, ratio, cls) in enumerate(parts):
            dx = f' dx="{gap}"' if k else ""
            weight = "" if ratio == 1 else ' font-weight="500"'
            tspans.append(f'<tspan class="{cls}" font-size="{fmt(size * ratio)}"{weight}{dx}>{escape(part)}</tspan>')
        svg.add(f'<text x="{fmt(x)}" y="142" font-weight="600">{"".join(tspans)}</text>')
        for j, ln in enumerate(wrap(context, ctx_size, text_w)):
            svg.text(x, 172 + j * 18, ln, "ink2", ctx_size)
    return svg.render()


# --------------------------------------------------------------------------------------------- figure 2


def ablation_chart(ablation: dict[str, Any]) -> str:
    width, height = 1200, 548
    left, label_right = 32, 210
    plot_x0, plot_w, panel_gap = 232, 256, 60
    row_pitch, bar_h = 40, 20
    plot_top = 214
    plot_bottom = plot_top + row_pitch * len(ABLATION_ROWS)
    blue, light_blue, neutral = ACCENT[1], ("#86b6ef", "#1c5cab"), ("#b5b3ab", "#5a5955")
    svg = Svg(
        width,
        height,
        "Adjudication ablation: macro F1 by block",
        "Macro F1 of six adjudicator configurations on three claim blocks. "
        + " ".join(
            f"{title} ({block}): "
            + ", ".join(
                f"{label} {ablation_metric(ablation, block, row, 'macro_f1'):.3f}" for row, label in ABLATION_ROWS
            )
            + "."
            for block, title, _ in ABLATION_BLOCKS
        ),
        {
            "b-full": (f"fill:{blue[0]}", f"fill:{blue[1]}"),
            "b-model": (f"fill:{light_blue[0]}", f"fill:{light_blue[1]}"),
            "b-rules": (f"fill:{neutral[0]}", f"fill:{neutral[1]}"),
            # A card-colored outline under each value label keeps the gridlines from crossing its digits.
            "halo": (f"fill:{CARD[0]};stroke:{CARD[0]}", f"fill:{CARD[1]};stroke:{CARD[1]}"),
        },
    )
    svg.text(left, 56, "Adjudication ablation: macro F1 by block", "ink", 26, 600)
    svg.text(left, 86, "Same claims, same policy; every row replays from committed recordings", "ink2", 15)
    # Legend: three encodings of one series.
    lx: float = left
    for cls, label in (
        ("b-rules", "Rules only (no model)"),
        ("b-model", "Model-driven variants"),
        ("b-full", "Agent loop (full)"),
    ):
        svg.add(f'<rect class="{cls}" x="{fmt(lx)}" y="112" width="18" height="12" rx="3"/>')
        svg.text(lx + 26, 123, label, "ink2", 13)
        lx += 26 + text_width(label, 13) + 28
    # Row labels, once, shared by the three panels.
    for i, (row, label) in enumerate(ABLATION_ROWS):
        cy = plot_top + row_pitch * i + row_pitch / 2
        strong = row == "loop_full"
        svg.text(label_right, cy + 5, label, "ink", 14, 600 if strong else 400, "end")
        if text_width(label, 14, 600 if strong else 400) > label_right - left:
            raise ValueError(f"row label {label!r} is too wide")
    for p, (block, title, sub) in enumerate(ABLATION_BLOCKS):
        x0 = plot_x0 + p * (plot_w + panel_gap)
        n = int(ablation["blocks"][block]["rules_only"]["n"])
        svg.text(x0, 164, title, "ink", 15, 600)
        svg.text(x0, 186, sub.format(n=n), "ink2", 13)
        for t in (0.0, 0.5, 1.0):
            gx = x0 + t * plot_w
            cls = "axis" if t == 0 else "grid"
            svg.add(
                f'<line class="{cls}" x1="{fmt(gx)}" y1="{plot_top - 6}" x2="{fmt(gx)}" '
                f'y2="{plot_bottom}" stroke-width="1"/>'
            )
            svg.text(gx, plot_bottom + 22, f"{t:g}", "ink2", 13, anchor="middle")
        for i, (row, _) in enumerate(ABLATION_ROWS):
            value = ablation_metric(ablation, block, row, "macro_f1")
            if not 0 <= value <= 1:
                raise ValueError(f"{block} {row} macro_f1 {value} outside 0..1")
            y = plot_top + row_pitch * i + (row_pitch - bar_h) / 2
            cls = "b-rules" if row == "rules_only" else "b-full" if row == "loop_full" else "b-model"
            length = max(value * plot_w, 2)
            svg.add(f'<path class="{cls}" d="{bar_path(x0, y, length, bar_h)}"/>')
            tx, ty = fmt(x0 + length + 6), fmt(y + bar_h / 2 + 4.5)
            svg.add(
                f'<text x="{tx}" y="{ty}" class="halo" font-size="13" stroke-width="5" '
                f'stroke-linejoin="round">{value:.3f}</text>'
            )
            svg.text(x0 + length + 6, y + bar_h / 2 + 4.5, f"{value:.3f}", "ink2", 13)
    svg.text(
        left,
        height - 28,
        "Macro F1 averages the per-outcome F1 of approve, deny, needs_info, and flag (the probe has no flag claims). "
        "Source: evals/results/ablation.json",
        "ink2",
        13,
    )
    return svg.render()


# --------------------------------------------------------------------------------------------- figure 3

# kind -> (row label, descriptor, shape, accent slot)
KINDS: dict[str, tuple[str, str, str, int]] = {
    "same_photo": ("Same photo", "identical or similar image", "square", 1),
    "same_address": ("Same address", "street address or email", "diamond", 2),
    "same_serial": ("Same serial", "claimed serial number", "triangle", 3),
    "same_payout": ("Same payout", "payout destination", "circle", 7),
    "same_mailing": ("Same mailing", "mailing address", "triangle-down", 4),
}


def shape(kind: str, cx: float, cy: float, cls: str) -> str:
    name = KINDS[kind][2]
    ring = 'stroke-width="2"'
    if name == "square":
        s = 11
        x, y = fmt(cx - s / 2), fmt(cy - s / 2)
        return f'<rect class="{cls}" x="{x}" y="{y}" width="{s}" height="{s}" rx="1.5" {ring}/>'
    if name == "circle":
        return f'<circle class="{cls}" cx="{fmt(cx)}" cy="{fmt(cy)}" r="6.5" {ring}/>'
    if name == "diamond":
        r = 7.8
        pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    elif name == "triangle":
        r = 8.2
        pts = [(cx, cy - r), (cx + r * 0.95, cy + r * 0.62), (cx - r * 0.95, cy + r * 0.62)]
    else:
        r = 8.2
        pts = [(cx, cy + r), (cx + r * 0.95, cy - r * 0.62), (cx - r * 0.95, cy - r * 0.62)]
    points = " ".join(f"{fmt(x)},{fmt(y)}" for x, y in pts)
    return f'<polygon class="{cls}" points="{points}" stroke-linejoin="round" {ring}/>'


def fraud_figure(clusters: Sequence[Cluster], meta: dict[str, Any]) -> str:
    by_kind: dict[str, list[Cluster]] = {}
    for c in clusters:
        if c.kind not in KINDS:
            raise SystemExit(f"fraud_clusters has an unknown kind {c.kind!r}; add it to KINDS")
        by_kind.setdefault(c.kind, []).append(c)
    kinds = [k for k in KINDS if k in by_kind and (k != "same_mailing" or by_kind[k])]
    left, stars_x0, right = 32, 268, 1168
    width, row_h = 1200, 78
    subtitle = (
        "Connected components in the Neo4j projection; members are claims, centers are the shared photo, "
        "address, serial, or payout destination"
    )
    sub_lines = wrap(subtitle, 15, right - left)
    key_y = 86 + 20 * len(sub_lines) + 14
    top = key_y + 30
    height = int(top + row_h * len(kinds) + 58)
    widest = max(len(by_kind[k]) for k in kinds)
    pitch = min(60.0, (right - stars_x0) / widest)
    spoke = 17.0
    memberships = sum(len(c.members) for c in clusters)
    distinct = len({m for c in clusters for m in c.members})
    classes: dict[str, tuple[str, str]] = {
        f"k-{k}": (
            f"fill:{ACCENT[KINDS[k][3]][0]};stroke:{CARD[0]}",
            f"fill:{ACCENT[KINDS[k][3]][1]};stroke:{CARD[1]}",
        )
        for k in kinds
    }
    classes["spoke"] = ("stroke:#b5b3ab", "stroke:#5a5955")
    classes["member"] = (f"fill:#898781;stroke:{CARD[0]}", f"fill:#898781;stroke:{CARD[1]}")
    counts = ", ".join(f"{KINDS[k][0].lower()} {len(by_kind[k])}" for k in kinds)
    svg = Svg(
        width,
        height,
        f"Fraud clusters found in {meta['seed_claims']} synthetic claims",
        f"{len(clusters)} fraud clusters from the seeded demo program, drawn as small stars grouped by kind: {counts}. "
        "Each star has a center marker for the shared photo, address, serial, or payout destination and one dot per "
        f"member claim. {memberships} memberships across {distinct} distinct claims.",
        classes,
    )
    svg.text(left, 56, f"Fraud clusters found in {meta['seed_claims']} synthetic claims", "ink", 26, 600)
    for j, ln in enumerate(sub_lines):
        svg.text(left, 86 + j * 20, ln, "ink2", 15)
    # Key: one claim dot on its spoke to a center marker.
    kx, ky = left, key_y
    svg.add(f'<line class="spoke" x1="{kx + 5}" y1="{ky}" x2="{kx + 27}" y2="{ky}" stroke-width="1"/>')
    svg.add(f'<circle class="member" cx="{kx + 5}" cy="{ky}" r="4.5" stroke-width="2"/>')
    svg.add(shape(kinds[0], kx + 28, ky, f"k-{kinds[0]}"))
    key = "claim joined to the value it shares; the marker's shape and color name the kind"
    svg.text(kx + 44, ky + 4.5, key, "ink2", 13)
    for r, kind in enumerate(kinds):
        y0 = top + r * row_h
        cy = y0 + row_h / 2
        svg.add(f'<line class="grid" x1="{left}" y1="{fmt(y0)}" x2="{right}" y2="{fmt(y0)}" stroke-width="1"/>')
        name, descriptor, _, _ = KINDS[kind]
        svg.add(shape(kind, left + 8, cy - 5, f"k-{kind}"))
        svg.text(left + 24, cy, f"{name}, {len(by_kind[kind])}", "ink", 15, 600)
        svg.text(left + 24, cy + 19, descriptor, "ink2", 13)
        if text_width(descriptor, 13) > stars_x0 - left - 40:
            raise ValueError(f"descriptor {descriptor!r} is too wide")
        for j, c in enumerate(by_kind[kind]):
            sx = stars_x0 + pitch * j + pitch / 2
            k = len(c.members)
            ends = [
                (
                    sx + spoke * math.cos(math.radians(-90 + 360 * m / k)),
                    cy + spoke * math.sin(math.radians(-90 + 360 * m / k)),
                )
                for m in range(k)
            ]
            for ex, ey in ends:
                end = f'x2="{fmt(ex)}" y2="{fmt(ey)}"'
                svg.add(f'<line class="spoke" x1="{fmt(sx)}" y1="{fmt(cy)}" {end} stroke-width="1"/>')
            for ex, ey in ends:
                svg.add(f'<circle class="member" cx="{fmt(ex)}" cy="{fmt(ey)}" r="4.5" stroke-width="2"/>')
            svg.add(shape(kind, sx, cy, f"k-{kind}"))
    bottom = top + row_h * len(kinds)
    svg.add(f'<line class="grid" x1="{left}" y1="{fmt(bottom)}" x2="{right}" y2="{fmt(bottom)}" stroke-width="1"/>')
    overlap = "" if memberships == distinct else f" ({memberships} memberships: a claim can share more than one value)"
    svg.text(
        left,
        bottom + 34,
        f"{len(clusters)} clusters covering {distinct} of the {meta['seed_claims']} claims{overlap}. "
        "Source: fraud_clusters for the seeded demo program (make seed)",
        "ink2",
        13,
    )
    return svg.render()


# --------------------------------------------------------------------------------------------- photos

# Both from recall 26007, whose instruction is "the unplugged power cord cut": a golden claim with no planted
# violation (expected approve), and a probe60 clean control whose cut cord the vision check read as intact.
GOLDEN_SAMPLE = "6a4643d2-3dba-5373-b8ba-5944aaabe75b"
REJECTED_SAMPLE = "335dd887-af7c-5de0-8337-1123236f8a0f"


def claim_record(path: Path, claim_id: str) -> dict[str, Any]:
    record: dict[str, Any] = next(c for c in load_json(path)["claims"] if c["claim_id"] == claim_id)
    return record


def save_photo(record: dict[str, Any], kind: str, name: str) -> None:
    from PIL import Image

    photo = next(p for p in record["photos"] if p["kind"] == kind)
    src = ROOT / photo["path"]
    data = src.read_bytes()
    if hashlib.sha256(data).hexdigest() != photo["sha256"]:
        raise SystemExit(f"{photo['path']} does not match its committed sha256; regenerate the photos")
    with Image.open(src) as im:
        rgb = im.convert("RGB")
        out = rgb.resize((640, round(rgb.height * 640 / rgb.width)), Image.Resampling.LANCZOS)
    IMG.mkdir(parents=True, exist_ok=True)
    out.save(IMG / name, "JPEG", quality=85, optimize=True)
    print(f"wrote {(IMG / name).relative_to(ROOT)} from {photo['path']}")


def build_photos() -> None:
    label = load_json(ADJ / "labels" / f"{GOLDEN_SAMPLE}.json")
    if label["expected_outcome"] != "approve" or label["violations"]:
        raise SystemExit(f"{GOLDEN_SAMPLE} is no longer a clean approved claim")
    record = claim_record(ADJ / "golden_claims.json", GOLDEN_SAMPLE)
    for kind in ("label", "destruction", "receipt"):
        save_photo(record, kind, f"sample-{kind}.jpg")
    probe = load_json(ADJ / "probe_labels" / f"{REJECTED_SAMPLE}.json")
    if probe["category"] != "clean" or probe["expected_outcome"] != "approve":
        raise SystemExit(f"{REJECTED_SAMPLE} is no longer a clean probe control")
    save_photo(claim_record(ADJ / "probe_claims.json", REJECTED_SAMPLE), "destruction", "rejected-destruction.jpg")


# --------------------------------------------------------------------------------------------- main


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos", action="store_true", help="also write the sample claim photos")
    args = parser.parse_args(argv)
    ASSETS.mkdir(parents=True, exist_ok=True)

    ablation = load_json(RESULTS / "ablation.json")
    reporting = load_json(RESULTS / "reporting.json")
    fraud_recall = ablation_metric(ablation, "golden300", "rules_only", "fraud_recall")
    agent_fraud_recall = ablation_metric(ablation, "golden300", "loop_full", "fraud_recall")
    if agent_fraud_recall != fraud_recall:
        raise SystemExit("golden300 fraud recall differs between rules_only and loop_full; reword the tile")
    blind_fraud_recall = ablation_metric(ablation, "standin40", "loop_full", "fraud_recall")
    if ablation_metric(ablation, "standin40", "rules_only", "fraud_recall") != blind_fraud_recall:
        raise SystemExit("standin40 fraud recall differs between rules_only and loop_full; reword the tile")
    if not reporting.get("passed"):
        raise SystemExit("evals/results/reporting.json did not pass")
    spend, spend_source = model_spend()
    facts = {
        "fraud_recall": fraud_recall,
        "blind_fraud_recall": blind_fraud_recall,
        "blind_agent_f1": ablation_metric(ablation, "standin40", "loop_full", "macro_f1"),
        "blind_rules_f1": ablation_metric(ablation, "standin40", "rules_only", "macro_f1"),
        "blind_vision_f1": ablation_metric(ablation, "standin40", "rules_plus_vision", "macro_f1"),
        "mpr_discrepancies": int(reporting["discrepancies"]),
        "graph_groups": fraud_test_expected(),
        "spend_usd": spend,
    }
    clusters, meta = seed_clusters()
    write("results-at-a-glance.svg", results_at_a_glance(facts))
    write("ablation.svg", ablation_chart(ablation))
    write("fraud-clusters.svg", fraud_figure(clusters, meta))
    if args.photos:
        build_photos()
    by_kind = Counter(c.kind for c in clusters)
    print(
        json.dumps(
            {
                **{k: str(v) for k, v in facts.items()},
                "spend_source": spend_source,
                "clusters": dict(sorted(by_kind.items())),
                "cluster_meta": meta,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
