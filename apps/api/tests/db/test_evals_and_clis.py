"""Eval runners, sampling, Recall Bench comparison, synth CLI, and the ingestion CLI, all writing to tmp paths."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from sqlalchemy import Engine, text

from recallops.evals import adjudication, extraction, run_all, sampling
from recallops.ingest import recallbench, runner
from recallops.ingest.cpsc_api import parse_record
from recallops.settings import REPO_ROOT
from recallops.synth.__main__ import build_golden

pytestmark = pytest.mark.db
FIXTURE = REPO_ROOT / "data" / "samples" / "cpsc_sample.json"


def fixture_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return records


def test_categorize_and_sampling_are_deterministic(tmp_path: Path) -> None:
    assert sampling.categorize("Children's Bike Helmets Recalled") == "children"
    assert sampling.categorize("Scotch Thermal Laminators") == "appliance_electrical"
    assert sampling.categorize("Garden Gnome") == "other"
    (tmp_path / "w.json").write_text(FIXTURE.read_text(encoding="utf-8"))
    pool = sampling.build_pool(sampling.load_archive(tmp_path))
    assert pool.height == 51
    first, second = sampling.draw_sample(pool), sampling.draw_sample(pool)
    assert first["source_id"].to_list() == second["source_id"].to_list()
    assert first.height == 40
    assert sampling.test_subset(first["source_id"].to_list()) == sampling.test_subset(first["source_id"].to_list())
    assert len(sampling.permutation(["a", "b", "c"])) == 3


def test_recallbench_comparison(tmp_path: Path) -> None:
    records = [parse_record(r) for r in fixture_records()]
    rows = [
        {
            "recall_date": r.recall_date.isoformat(),
            "recall_number": r.source_id,
            "title": r.title,
            "products": "",
            "hazard": "",
            "remedy": "",
            "retailers": "",
            "units": "",
            "url": r.url,
        }
        for r in records
    ]
    rows[0]["title"] = "A different title"
    csv = tmp_path / "rb.csv"
    pl.from_dicts(rows).write_csv(csv)
    result, detail = recallbench.compare(
        recallbench.load_csv(csv), {r.source_id: r for r in records}, date(2026, 9, 1), date(2026, 10, 31)
    )
    assert result.sampled == 30
    assert result.missing_in_api == 0
    archive = tmp_path / "archive"
    archive.mkdir()
    shutil.copy(FIXTURE, archive / "w.json")
    assert recallbench.main(["--csv", str(csv), "--archive", str(archive)]) in (0, 2)
    assert detail.height == 30


def test_runner_cli_from_dir(clean_db: Engine, tmp_path: Path) -> None:
    shutil.copy(FIXTURE, tmp_path / "2026-09-15_2026-10-16.json")
    assert runner.main(["--source", "cpsc", "--from-dir", str(tmp_path)]) == 0
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM recalls")).scalar_one() == 51
        assert conn.execute(text("SELECT status FROM feed_runs")).scalar_one() == "done"


def test_extraction_and_adjudication_evals_write_tables(clean_db: Engine, tmp_path: Path) -> None:
    out = tmp_path / "evals" / "results"
    ext = extraction.main(out)
    assert ext["n"] == 40 and ext["graph_status"] == "not run" and ext["graph_calls"] == 0
    assert ext["baseline_all"]["headline"] > 90
    md = (out / "extraction.md").read_text()
    assert "model-labeled, unaudited" in md and "Kill criterion" in md
    adj = adjudication.main(out)
    assert adj["n"] == 300 and adj["macro_f1"] > 0.9
    assert "rules_only (tools 1.0.1" in (out / "adjudication.md").read_text()
    assert run_all.main(out) == 0


def test_synth_build_golden_into_tmp(tmp_path: Path) -> None:
    adj_dir = tmp_path / "adjudication"
    adj_dir.mkdir()
    for name in ("planted.yaml", "outcome_policy.yaml", "program_rules.json"):
        shutil.copy(REPO_ROOT / "evals" / "adjudication" / name, adj_dir / name)
    summary = build_golden(adj_dir, tmp_path / "photos")
    assert summary["claims"] == 300 and summary["programs"] == 20
    payload = json.loads((adj_dir / "golden_claims.json").read_text())
    committed = json.loads((REPO_ROOT / "evals" / "adjudication" / "golden_claims.json").read_text())
    assert payload["claims"] == committed["claims"], "generation must reproduce all 300 committed golden claims"
    labels = sorted((adj_dir / "labels").glob("*.json"))
    assert len(labels) == 300
    for path in labels:
        assert path.read_text() == (REPO_ROOT / "evals" / "adjudication" / "labels" / path.name).read_text()
