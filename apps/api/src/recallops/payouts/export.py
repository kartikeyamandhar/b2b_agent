"""The export-file fallback (invariant 9; 03-payments.md section 5): every payout in pending_export becomes a row in a
CSV batch under the firm's storage prefix, and is marked exported through the ledger (audit_log per row). This
simulates the check or ACH batch a firm's bank would pay from.

python -m recallops.payouts.export [--firm <uuid>]      (make export-payouts)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from recallops.db.engine import internal_engine
from recallops.db.session import internal_session
from recallops.logs import configure, get_logger, log_event
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.settings import get_settings
from recallops.tenancy import FirmContext

log = get_logger("payouts.export")
COLUMNS = ("payout_id", "claim_id", "name", "address", "amount_cents", "currency")


def export_pending(engine: Engine, storage_dir: Path, firm_id: UUID | None = None) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.id, p.firm_id, p.claim_id, p.amount_cents, p.currency, c.claimant "
                "FROM payouts p JOIN claims c ON c.id = p.claim_id AND c.firm_id = p.firm_id "
                "WHERE p.status = 'pending_export' AND (CAST(:f AS uuid) IS NULL OR p.firm_id = CAST(:f AS uuid)) "
                "ORDER BY p.firm_id, p.created_at, p.id"
            ),
            {"f": str(firm_id) if firm_id else None},
        ).all()
    ledger = DbPayoutLedger(engine, session_for=lambda ctx, eng: internal_session(eng))
    batches: dict[UUID, list[Any]] = {}
    for row in rows:
        batches.setdefault(row.firm_id, []).append(row)
    written = []
    for firm, items in batches.items():
        batch = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        path = storage_dir / "exports" / str(firm) / f"{batch}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS)
            for r in items:
                person = r.claimant or {}
                address = (
                    person.get("mailing_address")
                    or " ".join(str(person.get(k, "")) for k in ("address_line1", "city", "state", "zip")).strip()
                )
                writer.writerow([r.id, r.claim_id, person.get("name", ""), address, r.amount_cents, r.currency])
        ctx = FirmContext(firm_id=firm, user_id="export-job", role="admin")
        for r in items:
            ledger.mark_exported(ctx, r.id, batch)
        log_event(log, "payout_export_written", firm_id=str(firm), rows=len(items), batch=batch)
        written.append({"firm_id": str(firm), "batch": batch, "rows": len(items), "path": str(path)})
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
