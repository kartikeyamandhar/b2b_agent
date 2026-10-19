"""Monthly progress report (01-backend.md section 8): SQL aggregation over claims and payouts for one program and
one calendar month, into exactly the CPSC form's boxes.

Definitions (also used by the independent recount in evals/reporting, which must agree to the unit):
- A correction is a remedy delivered: a refund payout that reached transferred or exported, counted in the month of
  that transition (its audit_log row). Repairs and replacements have no delivery marker yet, so they count only
  when a firm user records one (none do in this project); the report says so in its notes.
- contacts_email: claims submitted through the portal in the month (portal submissions count as email contacts).
- Boxes the system cannot know (units with distributors, incidents, notifications by channel, web hits) are manual
  inputs with documented defaults; units_with_consumers = total units minus the other holders.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

DELIVERED = ("payout_transferred", "payout_exported")
MANUAL_DEFAULTS: dict[str, Any] = {
    "units_with_manufacturer": 0,
    "units_with_distributor": 0,
    "units_with_retailers": 0,
    "incidents_before": 0,
    "incidents_after": 0,
    "injuries_before": 0,
    "injuries_after": 0,
    "deaths_before": 0,
    "deaths_after": 0,
    "notified_phone_text": 0,
    "notified_email": 0,
    "notified_mail": 0,
    "contacts_phone": 0,
    "contacts_mail": 0,
    "contacts_registration_card": 0,
    "homepage_posting": True,
    "homepage_posting_explanation": None,
    "website_hits": 0,
    "social_posts": {},
    "marketplace_monitoring": {},
}
BOX_ORDER = (
    "total_units_affected",
    "corrections_this_period",
    "cumulative_corrections",
    "units_with_manufacturer",
    "units_with_distributor",
    "units_with_retailers",
    "units_with_consumers",
    "incidents_before",
    "incidents_after",
    "injuries_before",
    "injuries_after",
    "deaths_before",
    "deaths_after",
    "notified_phone_text",
    "notified_email",
    "notified_mail",
    "contacts_phone",
    "contacts_email",
    "contacts_mail",
    "contacts_registration_card",
    "homepage_posting",
    "homepage_posting_explanation",
    "website_hits",
    "social_posts",
    "marketplace_monitoring",
)


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @classmethod
    def parse(cls, period: str) -> Period:
        year, month = (int(x) for x in period.split("-"))
        return cls(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))

    @property
    def start_ts(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=UTC)

    @property
    def end_ts(self) -> datetime:
        return datetime.combine(self.end, time.max, tzinfo=UTC)


def counts(session: Session, firm_id: UUID, program_id: UUID, period: Period) -> dict[str, int]:
    """The computed boxes, in SQL (aggregation belongs in the database: invariant 11)."""
    params = {"f": firm_id, "p": program_id, "s": period.start_ts, "e": period.end_ts}
    delivered = session.execute(
        text(
            "SELECT count(*) FILTER (WHERE a.at BETWEEN :s AND :e) AS this_period, "
            "count(*) FILTER (WHERE a.at <= :e) AS cumulative "
            "FROM audit_log a JOIN payouts pay ON pay.id::text = a.target_id AND pay.firm_id = a.firm_id "
            "JOIN claims c ON c.id = pay.claim_id AND c.firm_id = pay.firm_id "
            "WHERE a.firm_id = :f AND c.program_id = :p AND a.target_type = 'payout' "
            "AND a.action IN ('payout_transferred', 'payout_exported')"
        ),
        params,
    ).one()
    contacts = session.execute(
        text("SELECT count(*) FROM claims WHERE firm_id = :f AND program_id = :p AND submitted_at BETWEEN :s AND :e"),
        params,
    ).scalar_one()
    return {
        "corrections_this_period": int(delivered.this_period),
        "cumulative_corrections": int(delivered.cumulative),
        "contacts_email": int(contacts),
    }


def total_units(session: Session, firm_id: UUID, program_id: UUID) -> int:
    row = session.execute(
        text(
            "SELECT r.units_affected, p.identification_rules->>'units_affected' AS rules_units "
            "FROM recall_programs p LEFT JOIN recalls r ON r.id = p.recall_id WHERE p.id = :p AND p.firm_id = :f"
        ),
        {"p": program_id, "f": firm_id},
    ).one()
    return int(row.units_affected or row.rules_units or 0)


def build_payload(
    session: Session, firm_id: UUID, program_id: UUID, period: Period, manual: dict[str, Any] | None = None
) -> dict[str, Any]:
    boxes: dict[str, Any] = {**MANUAL_DEFAULTS, **(manual or {})}
    boxes["total_units_affected"] = total_units(session, firm_id, program_id)
    boxes.update(counts(session, firm_id, program_id, period))
    held = boxes["units_with_manufacturer"] + boxes["units_with_distributor"] + boxes["units_with_retailers"]
    boxes["units_with_consumers"] = max(0, boxes["total_units_affected"] - held - boxes["cumulative_corrections"])
    return {
        "period": period.start.strftime("%Y-%m"),
        "boxes": {k: boxes[k] for k in BOX_ORDER},
        "notes": [
            "Corrections count refund payouts that reached transferred or exported in the month of that transition.",
            "Repairs and replacements count only when a delivery is recorded; none are recorded in this project.",
            "Portal submissions count as email contacts. Manual boxes use their documented defaults unless set.",
            "units_with_consumers = total units minus units held by manufacturer, distributor, and retailers, "
            "minus cumulative corrections.",
        ],
    }
