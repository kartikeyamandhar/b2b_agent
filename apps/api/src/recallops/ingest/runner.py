"""Ingestion runner: CPSC API windows (network) or saved window files (offline) into recalls.

python -m recallops.ingest.runner --source cpsc --since 2026-09-01 [--until 2026-10-16]
python -m recallops.ingest.runner --source cpsc --from-dir data/raw/cpsc

Idempotent: a record is inserted once, updated only when its raw sha256 changes, and otherwise counted
as unchanged. Every record runs in its own savepoint, so one bad record is counted as failed instead of
aborting the window, and fetched = inserted + updated + unchanged + failed holds for every run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from recallops.db.engine import internal_engine
from recallops.ingest.cpsc_api import CpscClient, CpscRecordError, ParsedRecall, parse_record, windows
from recallops.logs import configure, get_logger, log_event
from recallops.models import FeedRun, Recall
from recallops.settings import get_settings

log = get_logger("ingest.runner")


@dataclass
class RunCounts:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0

    def balanced(self) -> bool:
        return self.fetched == self.inserted + self.updated + self.unchanged + self.failed


def _row_values(p: ParsedRecall) -> dict[str, Any]:
    return {
        "source": p.source,
        "source_id": p.source_id,
        "source_record_id": p.source_record_id,
        "recall_date": p.recall_date,
        "last_publish_date": p.last_publish_date,
        "title": p.title,
        "description": p.description,
        "url": p.url,
        "raw": p.raw,
        "raw_sha256": p.raw_sha256,
        "source_text": p.source_text,
        "source_text_sha256": p.source_text_sha256,
        "canonicalizer_version": p.canonicalizer_version,
        "units_affected": p.units.value,
        "units_status": p.units.status,
        "units_text": p.units.text,
        "category": p.category,
        "retailers": p.retailers,
        "manufacturers": p.manufacturers,
        "importers": p.importers,
        "remedies": p.remedies,
        "upcs": p.upcs,
    }


def upsert_record(session: Session, raw: Mapping[str, Any], counts: RunCounts) -> None:
    counts.fetched += 1
    try:
        parsed = parse_record(raw)
    except CpscRecordError as exc:
        counts.failed += 1
        log_event(log, "cpsc_record_failed", level=30, error=str(exc), recall_number=raw.get("RecallNumber"))
        return
    try:
        with session.begin_nested():
            existing = session.execute(
                select(Recall.id, Recall.raw_sha256).where(
                    Recall.source == parsed.source, Recall.source_id == parsed.source_id
                )
            ).first()
            values = _row_values(parsed)
            if existing is None:
                session.add(Recall(**values))
                session.flush()
                counts.inserted += 1
            elif existing.raw_sha256 == parsed.raw_sha256:
                counts.unchanged += 1
            else:
                session.execute(
                    update(Recall).where(Recall.id == existing.id).values(**values, updated_at=datetime.now(UTC))
                )
                counts.updated += 1
    except SQLAlchemyError as exc:
        counts.failed += 1
        log_event(log, "cpsc_record_failed", level=30, error=str(exc)[:300], source_id=parsed.source_id)


def window_files(directory: Path) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    for path in sorted(directory.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, list):
            raise ValueError(f"{path} is not a JSON list")
        yield path.stem, body


def _start_run(engine: Engine, source: str) -> UUID:
    with Session(engine) as session, session.begin():
        run = FeedRun(source=source)
        session.add(run)
        session.flush()
        return run.id


def _finish_run(engine: Engine, run_id: UUID, counts: RunCounts, status: str, error: str | None) -> None:
    with Session(engine) as session, session.begin():
        session.execute(
            update(FeedRun)
            .where(FeedRun.id == run_id)
            .values(finished_at=datetime.now(UTC), status=status, error=error, **asdict(counts))
        )


def run(
    batches: Iterable[tuple[str, list[dict[str, Any]]]], engine: Engine, source: str = "cpsc"
) -> tuple[UUID, RunCounts]:
    """Load windows one transaction at a time and record the run in feed_runs."""
    counts = RunCounts()
    run_id = _start_run(engine, source)
    t_run = time.monotonic()
    log_event(log, "feed_run_started", run_id=str(run_id), source=source)
    status, error = "done", None
    try:
        for label, records in batches:
            t0 = time.monotonic()
            before = asdict(counts)
            with Session(engine) as session, session.begin():
                for raw in records:
                    upsert_record(session, raw, counts)
            delta = {k: v - before[k] for k, v in asdict(counts).items()}
            log_event(
                log,
                "feed_window_loaded",
                run_id=str(run_id),
                window=label,
                duration_ms=round((time.monotonic() - t0) * 1000),
                **delta,
            )
        if counts.failed:
            status, error = "partial", f"{counts.failed} records failed"
        if not counts.balanced():
            status, error = "failed", f"accounting mismatch: {asdict(counts)}"
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        _finish_run(engine, run_id, counts, status, error)
        log_event(
            log,
            "feed_run_finished",
            run_id=str(run_id),
            status=status,
            duration_ms=round((time.monotonic() - t_run) * 1000),
            **asdict(counts),
        )
    return run_id, counts


def api_batches(since: date, until: date) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    settings = get_settings()
    headers = {"User-Agent": settings.http_user_agent}
    with httpx.Client(timeout=settings.cpsc_timeout_s, headers=headers) as http:
        client = CpscClient(http, settings.cpsc_api_base)
        for i, (start, end) in enumerate(windows(since, until, settings.cpsc_window_days)):
            if i:
                time.sleep(settings.cpsc_pause_s)
            yield f"{start}_{end}", client.fetch_window(start, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="cpsc", choices=["cpsc"])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", type=date.fromisoformat, help="pull the live API from this date")
    group.add_argument("--from-dir", type=Path, help="load saved window files instead of calling the API")
    parser.add_argument("--until", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    configure(get_settings().log_level)
    batches = window_files(args.from_dir) if args.from_dir else api_batches(args.since, args.until or date.today())
    run_id, counts = run(batches, internal_engine(), args.source)
    print(json.dumps({"run_id": str(run_id), **asdict(counts), "balanced": counts.balanced()}))
    return 0 if counts.balanced() and counts.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
