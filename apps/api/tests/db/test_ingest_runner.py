"""Runner against the test database with the committed 51-record fixture."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from recallops.ingest.runner import RunCounts, run, window_files
from recallops.settings import REPO_ROOT

pytestmark = pytest.mark.db

FIXTURE = REPO_ROOT / "data" / "samples" / "cpsc_sample.json"


def fixture() -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
    return body


def test_first_run_inserts_and_second_run_is_unchanged(clean_db: Engine) -> None:
    _, first = run([("fixture", fixture())], clean_db)
    assert (first.fetched, first.inserted, first.failed) == (51, 51, 0)
    _, second = run([("fixture", fixture())], clean_db)
    assert (second.inserted, second.updated, second.unchanged) == (0, 0, 51)
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM recalls")).scalar_one() == 51
        statuses = conn.execute(text("SELECT status FROM feed_runs ORDER BY started_at")).scalars().all()
    assert statuses == ["done", "done"]


def test_overlapping_windows_do_not_duplicate(clean_db: Engine) -> None:
    records = fixture()
    _, counts = run([("w1", records[:30]), ("w2", records[20:])], clean_db)
    assert counts.fetched == 61
    assert (counts.inserted, counts.unchanged) == (51, 10)
    assert counts.balanced()


def test_changed_record_is_updated(clean_db: Engine) -> None:
    records = fixture()
    run([("w", records)], clean_db)
    changed = copy.deepcopy(records)
    changed[0]["Title"] = changed[0]["Title"] + " (Updated)"
    _, counts = run([("w", changed)], clean_db)
    assert (counts.updated, counts.unchanged) == (1, 50)
    with clean_db.connect() as conn:
        title = conn.execute(text("SELECT title FROM recalls WHERE source_id = '27024'")).scalar_one()
    assert title.endswith("(Updated)")


def test_bad_records_are_counted_not_dropped(clean_db: Engine) -> None:
    records = fixture()[:5]
    records.append({"RecallNumber": "", "RecallID": "", "Title": "x"})
    records.append({**records[0], "RecallNumber": "99999", "RecallDate": "not a date"})
    run_id, counts = run([("w", records)], clean_db)
    assert (counts.fetched, counts.inserted, counts.failed) == (7, 5, 2)
    assert counts.balanced()
    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT status, failed, error FROM feed_runs WHERE id = :id"), {"id": run_id}).one()
    assert row.status == "partial"
    assert row.failed == 2


def test_window_files_and_counts_balance(tmp_path: Path) -> None:
    (tmp_path / "2026-09-01_2026-09-30.json").write_text(json.dumps(fixture()[:3]))
    (tmp_path / "bad.json").write_text(json.dumps({"not": "a list"}))
    batches = window_files(tmp_path)
    label, records = next(batches)
    assert label == "2026-09-01_2026-09-30" and len(records) == 3
    with pytest.raises(ValueError, match="not a JSON list"):
        next(batches)
    assert RunCounts(fetched=3, inserted=1, unchanged=2).balanced()
    assert not RunCounts(fetched=3, inserted=1).balanced()
