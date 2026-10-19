"""The export batch fallback (invariant 9; 03-payments.md section 5): every payout in pending_export whose claim is
approved becomes a row of one CSV batch per firm, simulating the check or ACH file a firm's bank would pay from.

One transaction per firm (red team, decisions log 2026-10-18): the rows are claimed with FOR UPDATE SKIP LOCKED (a
concurrent run skips them instead of paying them twice), the CSV is rendered in memory and stored in export_batches,
and the same transaction marks every payout exported with the batch id (audit row each) and its claim paid. A
committed batch therefore always has its file, on any disk, and a crash before the commit leaves every row payable
by the next run. A copy under STORAGE_DIR is a convenience cache only. Cells are formula-safe: a value starting with
=, +, -, @, tab, or carriage return is prefixed with an apostrophe, so a spreadsheet never evaluates claimant text.

python -m recallops.payouts.export [--firm <uuid>]      (make export-payouts)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, select, text

from recallops.db.engine import internal_engine
from recallops.db.session import internal_session, set_firm, tenant_session
from recallops.logs import configure, get_logger, log_event
from recallops.models import ExportBatch, Payout
from recallops.payouts.db_ledger import mark_claim_paid, transition
from recallops.settings import get_settings
from recallops.tenancy import FirmContext

log = get_logger("payouts.export")
COLUMNS = ("payout_id", "claim_id", "name", "address", "amount_cents", "currency")
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    text_value = "" if value is None else str(value)
    return "'" + text_value if text_value.startswith(FORMULA_PREFIXES) else text_value


def render_csv(rows: Sequence[Any]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        person = r.claimant or {}
        address = person.get("mailing_address") or " ".join(
            str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip")
        )
        writer.writerow(
            [
                safe_cell(v)
                for v in (r.id, r.claim_id, person.get("name", ""), address.strip(), r.amount_cents, r.currency)
            ]
        )
    return buffer.getvalue().encode("utf-8")


def export_firm(
    engine: Engine, firm_id: UUID, actor: str = "export-job", tenant: bool = False
) -> dict[str, Any] | None:
    """tenant=True runs as the API's role under RLS (an admin's POST /v1/payouts/exports); the job uses internal."""
    ctx = FirmContext(firm_id=firm_id, user_id=actor, role="admin")
    with tenant_session(ctx, engine) if tenant else internal_session(engine) as session:
        set_firm(session, firm_id)
        rows = session.execute(
            text(
                "SELECT p.id, p.claim_id, p.amount_cents, p.currency, c.claimant FROM claims c "
                "JOIN payouts p ON p.claim_id = c.id AND p.firm_id = c.firm_id "
                "WHERE c.firm_id = :f AND c.status = 'approved' AND p.status = 'pending_export' "
                "ORDER BY p.created_at, p.id FOR UPDATE OF c, p SKIP LOCKED"
            ),
            {"f": firm_id},
        ).all()
        if not rows:
            return None
        body = render_csv(rows)
        batch = ExportBatch(
            id=uuid.uuid4(),
            firm_id=firm_id,
            created_by=actor,
            row_count=len(rows),
            total_cents=sum(int(r.amount_cents) for r in rows),
            sha256=hashlib.sha256(body).hexdigest(),
            csv=body,
        )
        session.add(batch)
        session.flush()
        for r in rows:
            payout = session.scalars(select(Payout).where(Payout.id == r.id, Payout.firm_id == firm_id)).one()
            transition(session, ctx, payout, "exported", export_batch=batch.id)
            mark_claim_paid(session, ctx, r.claim_id)
        summary = {
            "firm_id": str(firm_id),
            "batch": str(batch.id),
            "rows": len(rows),
            "total_cents": batch.total_cents,
            "sha256": batch.sha256,
        }
    log_event(log, "payout_export_written", firm_id=str(firm_id), rows=len(rows), batch=summary["batch"])
    return {**summary, "csv": body}


def export_pending(
    engine: Engine, storage_dir: Path | None = None, firm_id: UUID | None = None
) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        firms = (
            conn.execute(
                text(
                    "SELECT DISTINCT p.firm_id FROM payouts p WHERE p.status = 'pending_export' "
                    "AND (CAST(:f AS uuid) IS NULL OR p.firm_id = CAST(:f AS uuid)) ORDER BY p.firm_id"
                ),
                {"f": str(firm_id) if firm_id else None},
            )
            .scalars()
            .all()
        )
    written = []
    for firm in firms:
        result = export_firm(engine, firm)
        if result is None:
            continue
        body = result.pop("csv")
        if storage_dir is not None:  # cache only: the batch row is the record
            try:
                path = storage_dir / "exports" / str(firm) / f"{result['batch']}.csv"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                path.chmod(0o600)
                result["path"] = str(path)
            except OSError as exc:
                log_event(log, "payout_export_cache_failed", level=30, firm_id=str(firm), error=str(exc)[:200])
        written.append(result)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--firm", type=UUID, default=None)
    args = parser.parse_args(argv)
    settings = get_settings()
    configure(settings.log_level)
    print(json.dumps(export_pending(internal_engine(), settings.storage_dir, args.firm), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
