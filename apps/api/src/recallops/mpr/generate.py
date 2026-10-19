"""Monthly progress report (01-backend.md section 8): SQL aggregation over claims, payouts, and the audit log for one
program and one calendar month (UTC), into exactly the CPSC form's boxes.

Definitions (the independent recount in mpr/recount.py restates them in its own words and must agree to the unit):
- A correction is a remedy delivered, counted once, in the month of its first delivery event: a refund payout that
  reached transferred or exported (its audit row), or a repair or replacement claim a reviewer marked delivered (the
  remedy_delivered audit row on the claim).
- contacts_email: claims whose first submission (claims.first_submitted_at, set once by a trigger) falls in the month;
  portal submissions count as email contacts, and a needs_info answer is not a new contact.
- total_units_affected comes from the program's rules first (a firm can correct it), then the recall record.
- units_with_consumers = total units minus units held by the manufacturer, distributors, and retailers, minus
  cumulative corrections.
- Boxes the system cannot know (incidents, notifications sent outside it, web hits) are manual inputs with documented
  defaults. The payload keeps computed and manual values apart; reconciliation checks the computed ones only, and a
  manual override of a contacts box is listed in the notes.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
REFUND_DELIVERED = ("payout_transferred", "payout_exported")
REMEDY_DELIVERED = "remedy_delivered"
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
OVERRIDABLE_COMPUTED = ("contacts_email",)
COMPUTED = (
    "total_units_affected",
    "corrections_this_period",
    "cumulative_corrections",
    "units_with_consumers",
    "contacts_email",
)
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


class BadPeriod(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @classmethod
    def parse(cls, period: str) -> Period:
        if not re.match(PERIOD_PATTERN, period or ""):
            raise BadPeriod(f"period must be YYYY-MM with a month from 01 to 12, got {period!r}")
        year, month = (int(x) for x in period.split("-"))
        return cls(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))

    @property
    def label(self) -> str:
        return self.start.strftime("%Y-%m")

    @property
    def start_ts(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=UTC)

    @property
    def end_ts(self) -> datetime:
        return datetime.combine(self.end, time.max, tzinfo=UTC)


def counts(session: Session, firm_id: UUID, program_id: UUID, period: Period) -> dict[str, int]:
    """The computed boxes, in SQL (aggregation belongs in the database: invariant 11). Each delivered refund or remedy
    counts once, at its first delivery event, however many audit rows a replay might have written."""
    params = {
        "f": firm_id,
        "p": program_id,
        "s": period.start_ts,
        "e": period.end_ts,
        "refund": list(REFUND_DELIVERED),
        "remedy": REMEDY_DELIVERED,
    }
    delivered = session.execute(
        text(
            "WITH events AS ("
            " SELECT 'payout:' || pay.id::text AS item, a.at FROM audit_log a"
            " JOIN payouts pay ON pay.id::text = a.target_id AND pay.firm_id = a.firm_id"
            " JOIN claims c ON c.id = pay.claim_id AND c.firm_id = pay.firm_id"
            " WHERE a.firm_id = :f AND c.program_id = :p AND a.target_type = 'payout' AND a.action = ANY(:refund)"
            " UNION ALL"
            " SELECT 'claim:' || c.id::text AS item, a.at FROM audit_log a"
            " JOIN claims c ON c.id::text = a.target_id AND c.firm_id = a.firm_id"
            " WHERE a.firm_id = :f AND c.program_id = :p AND a.target_type = 'claim' AND a.action = :remedy"
            "), first AS (SELECT item, min(at) AS at FROM events GROUP BY item)"
            " SELECT count(*) FILTER (WHERE at BETWEEN :s AND :e) AS this_period,"
            " count(*) FILTER (WHERE at <= :e) AS cumulative FROM first"
        ),
        params,
    ).one()
    contacts = session.execute(
        text(
            "SELECT count(*) FROM claims WHERE firm_id = :f AND program_id = :p AND status <> 'draft' "
            "AND first_submitted_at BETWEEN :s AND :e"
        ),
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
    if row.rules_units not in (None, ""):
        return int(row.rules_units)
    return int(row.units_affected or 0)


def build_payload(
    session: Session, firm_id: UUID, program_id: UUID, period: Period, manual: dict[str, Any] | None = None
) -> dict[str, Any]:
    entered = {k: v for k, v in (manual or {}).items() if k in MANUAL_DEFAULTS or k in OVERRIDABLE_COMPUTED}
    computed: dict[str, Any] = {"total_units_affected": total_units(session, firm_id, program_id)}
    computed.update(counts(session, firm_id, program_id, period))
    boxes: dict[str, Any] = {**MANUAL_DEFAULTS, **{k: v for k, v in entered.items() if k in MANUAL_DEFAULTS}}
    held = boxes["units_with_manufacturer"] + boxes["units_with_distributor"] + boxes["units_with_retailers"]
    computed["units_with_consumers"] = max(
        0, computed["total_units_affected"] - held - computed["cumulative_corrections"]
    )
    boxes.update(computed)
    overridden = [k for k in OVERRIDABLE_COMPUTED if k in entered]
    boxes.update({k: entered[k] for k in overridden})
    notes = [
        "Corrections count each refund payout that reached transferred or exported, and each repair or replacement "
        "marked delivered, once, in the month of its first delivery.",
        "Portal submissions count as email contacts in the month of first submission.",
        "Manual boxes use their documented defaults unless entered.",
        "units_with_consumers = total units minus units held by manufacturer, distributor, and retailers, minus "
        "cumulative corrections.",
    ]
    notes += [f"{k} was entered by hand ({entered[k]}); the computed value is {computed[k]}." for k in overridden]
    return {
        "period": period.label,
        "boxes": {k: boxes[k] for k in BOX_ORDER},
        "computed": {k: computed[k] for k in COMPUTED},
        "manual": entered,
        "notes": notes,
    }
