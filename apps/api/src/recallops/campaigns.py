"""Notification campaign tooling (CLAUDE.md M5): owner registrations, matching, outreach drafts, and a simulated send.

A firm uploads the product owners it already knows (registration cards, warranty lists) as CSV. Each row is matched
against the program's identification rules with the same pure-code checks the adjudicator uses (model, serial range,
date code), so the campaign and the claims agree on what is affected. A matched owner gets one portal claim link and
one draft per channel; the draft quotes the recall notice's HAZARD and REMEDY sections verbatim, because recall
notices are regulated text a firm must not paraphrase (no model call). Sending is simulated: nothing leaves the
system (no email, mail, or SMS provider exists), and the MPR's notified_* boxes count what was marked sent.

Synthetic data only (CLAUDE.md non-goals): the upload refuses a row whose email is not at example.com, example.net,
or example.org, or whose phone is outside 555-0100 to 555-0199.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import polars as pl
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recallops.api.schemas import clean_text
from recallops.claims.tools import ClaimFacts, check_date_code, check_model, check_serial
from recallops.logs import get_logger, log_event
from recallops.models import AuditLog, Notification, Recall, RecallProgram, Registration
from recallops.rules.schemas import IdentificationRules
from recallops.tenancy import FirmContext
from recallops.text import section_bounds

log = get_logger("campaigns")

COLUMNS = ("external_ref", "name", "email", "phone", "address", "model", "serial", "date_code", "purchase_date")
CHANNELS = ("email", "mail", "phone_text")
SYNTHETIC_EMAIL = re.compile(r"^[^@\s]+@example\.(com|net|org)$", re.IGNORECASE)
SYNTHETIC_PHONE = re.compile(r"555-?01\d\d$")
MAX_FIELD = 300


class UploadRejected(ValueError):
    pass


@dataclass
class Parsed:
    rows: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)


def parse_registrations(raw: bytes, max_rows: int) -> Parsed:
    """All columns read as text (no type inference), unknown columns ignored, row-level errors reported."""
    try:
        frame = pl.read_csv(io.BytesIO(raw), infer_schema=False, n_rows=max_rows + 1, truncate_ragged_lines=True)
    except (pl.exceptions.PolarsError, UnicodeDecodeError) as exc:
        raise UploadRejected(f"not a readable CSV: {type(exc).__name__}") from exc
    if "external_ref" not in frame.columns:
        raise UploadRejected("the CSV needs an external_ref column")
    if frame.height > max_rows:
        raise UploadRejected(f"more than {max_rows} rows; split the file")
    out = Parsed()
    seen: set[str] = set()
    for i, record in enumerate(frame.select([c for c in COLUMNS if c in frame.columns]).iter_rows(named=True)):
        row = {k: clean_text(record.get(k)) for k in COLUMNS}
        row = {k: (v[:MAX_FIELD] if isinstance(v, str) else v) for k, v in row.items()}
        problem = _row_problem(row, seen)
        if problem:
            out.rejected.append({"row": i + 2, "reason": problem})  # +2: header line, 1-based
            continue
        seen.add(row["external_ref"])
        out.rows.append(row)
    return out


def _row_problem(row: dict[str, Any], seen: set[str]) -> str | None:
    if not row["external_ref"]:
        return "external_ref is empty"
    if len(row["external_ref"]) > 80:
        return "external_ref is longer than 80 characters"
    if row["external_ref"] in seen:
        return "external_ref repeats an earlier row"
    if row["email"] and not SYNTHETIC_EMAIL.match(row["email"]):
        return "email is not synthetic (example.com, example.net, example.org only)"
    if row["phone"] and not SYNTHETIC_PHONE.search(re.sub(r"[\s().]", "", row["phone"])):
        return "phone is not synthetic (555-0100 to 555-0199 only)"
    if not row["email"] and not row["phone"]:
        return "no synthetic marker (an example.com, example.net, or example.org email, or a 555-01xx phone)"
    if row["purchase_date"]:
        try:
            row["purchase_date"] = date.fromisoformat(row["purchase_date"])
        except ValueError:
            return "purchase_date is not YYYY-MM-DD"
    return None


def upsert_registrations(session: Session, ctx: FirmContext, program_id: UUID, rows: list[dict[str, Any]]) -> int:
    for row in rows:
        values = {**row, "firm_id": ctx.firm_id, "program_id": program_id, "matched": None, "match_reasons": []}
        stmt = insert(Registration).values(**values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["program_id", "external_ref"],
                set_={k: stmt.excluded[k] for k in (*COLUMNS[1:], "matched", "match_reasons")}
                | {"updated_at": func.now()},
            )
        )
    _audit(session, ctx, "registrations_uploaded", program_id, {"rows": len(rows)})
    return len(rows)


def _audit(session: Session, ctx: FirmContext, action: str, program_id: UUID, after: dict[str, Any]) -> None:
    session.add(
        AuditLog(
            firm_id=ctx.firm_id,
            actor=ctx.user_id,
            action=action,
            target_type="program",
            target_id=str(program_id),
            before=None,
            after=after,
        )
    )


def match(session: Session, ctx: FirmContext, program: RecallProgram) -> dict[str, int]:
    """A registration matches when its model matches a recalled model and neither its serial nor its date code is
    outside a stated range. Without a model it cannot be confirmed (unmatched, reason recorded)."""
    rules = IdentificationRules.model_validate(program.identification_rules or {})
    counts = {"matched": 0, "unmatched": 0}
    regs = session.scalars(
        select(Registration).where(Registration.firm_id == ctx.firm_id, Registration.program_id == program.id)
    ).all()
    for r in regs:
        facts = ClaimFacts(
            claim_id=r.id,
            model_claimed=r.model,
            serial_claimed=r.serial,
            date_code_claimed=r.date_code,
            purchase_date=r.purchase_date,
        )
        model, serial, code = check_model(facts, rules), check_serial(facts, rules), check_date_code(facts, rules)
        reasons = [f"model {model.verdict}", f"serial {serial.verdict}", f"date code {code.verdict}"]
        in_range = "in_range" in (serial.verdict, code.verdict)
        identified = model.verdict == "match" or (model.verdict == "skipped" and in_range)
        ok = identified and "out_of_range" not in (serial.verdict, code.verdict)
        r.matched, r.match_reasons = ok, reasons
        counts["matched" if ok else "unmatched"] += 1
    _audit(session, ctx, "registrations_matched", program.id, counts)
    log_event(log, "campaign_matched", firm_id=str(ctx.firm_id), program_id=str(program.id), **counts)
    return counts


def _section(source_text: str, name: str) -> str:
    bounds = section_bounds(source_text).get(name)
    return source_text[bounds[0] : bounds[1]].strip() if bounds else ""


LINK_PLACEHOLDER = "{claim_link}"


def render(recall: Recall | None, reg: Registration, channel: str, link: str = LINK_PLACEHOLDER) -> tuple[str, str]:
    """Stored drafts carry the placeholder, never the link: the link holds the claim token, which the system keeps
    only as a hash (invariant 10). A real sender substitutes a link issued at send time with
    POST /v1/claims/{claim_id}/link (programs.reissue_link), which also replaces the link discarded here."""
    title = recall.title if recall else "Product safety recall"
    hazard = _section(recall.source_text, "HAZARD") if recall else ""
    remedy = _section(recall.source_text, "REMEDY") if recall else ""
    greeting = f"Dear {reg.name}," if reg.name else "Dear product owner,"
    if channel == "phone_text":
        return (
            title[:120],
            f"Safety recall: {title[:90]}. Your registered {reg.model or 'product'} is affected. "
            f"Stop using it and file your claim: {link}",
        )
    body = "\n\n".join(
        part
        for part in (
            greeting,
            f"Our records show you registered {reg.model or 'a product'} covered by this recall: {title}.",
            f"Hazard (from the recall notice): {hazard}" if hazard else "",
            f"Remedy (from the recall notice): {remedy}" if remedy else "",
            f"File your claim at {link}. The link is yours alone; it needs no account.",
        )
        if part
    )
    return f"Safety recall: {title}"[:200], body


def draft(
    session: Session, ctx: FirmContext, program: RecallProgram, channels: list[str], issue: Any
) -> dict[str, int]:
    """issue(n) -> [(claim_id, url)] issues portal links (programs.issue_links). Idempotent per registration and
    channel: an existing draft or sent notice is kept."""
    recall = session.get(Recall, program.recall_id) if program.recall_id else None
    regs = session.scalars(
        select(Registration)
        .where(
            Registration.firm_id == ctx.firm_id, Registration.program_id == program.id, Registration.matched.is_(True)
        )
        .order_by(Registration.external_ref)
    ).all()
    needing = [r for r in regs if r.claim_id is None]
    for r, (claim_id, _url) in zip(needing, issue(len(needing)) if needing else [], strict=True):
        r.claim_id = claim_id  # the claim exists with its token hash; the raw link is not kept
    counts = dict.fromkeys(channels, 0)
    for r in regs:
        for channel in channels:
            if (channel == "email" and not r.email) or (channel == "mail" and not r.address):
                continue
            if channel == "phone_text" and not r.phone:
                continue
            subject, body = render(recall, r, channel)
            done = session.execute(
                insert(Notification)
                .values(
                    firm_id=ctx.firm_id,
                    program_id=program.id,
                    registration_id=r.id,
                    channel=channel,
                    subject=subject,
                    body=body,
                )
                .on_conflict_do_nothing(index_elements=["registration_id", "channel"])
                .returning(Notification.id)
            ).first()
            counts[channel] += 1 if done else 0
    _audit(session, ctx, "campaign_drafted", program.id, counts)
    return counts


def send(session: Session, ctx: FirmContext, program_id: UUID, channel: str | None) -> dict[str, int]:
    stmt = (
        update(Notification)
        .where(Notification.firm_id == ctx.firm_id, Notification.program_id == program_id)
        .where(Notification.status == "draft")
        .values(status="sent", sent_at=datetime.now(UTC))
        .returning(Notification.channel)
    )
    if channel:
        stmt = stmt.where(Notification.channel == channel)
    sent = [str(c) for c in session.execute(stmt).scalars()]
    counts = {c: sent.count(c) for c in CHANNELS if c in sent}
    _audit(session, ctx, "campaign_sent", program_id, {"simulated": True, **counts})
    log_event(log, "campaign_sent", firm_id=str(ctx.firm_id), program_id=str(program_id), simulated=True, **counts)
    return counts


def summary(session: Session, ctx: FirmContext, program_id: UUID) -> dict[str, Any]:
    regs = session.execute(
        text(
            "SELECT count(*) AS total, count(*) FILTER (WHERE matched) AS matched, "
            "count(*) FILTER (WHERE matched IS FALSE) AS unmatched, count(claim_id) AS links "
            "FROM registrations WHERE firm_id = :f AND program_id = :p"
        ),
        {"f": ctx.firm_id, "p": program_id},
    ).one()
    by = session.execute(
        text(
            "SELECT channel, status, count(*) FROM notifications WHERE firm_id = :f AND program_id = :p "
            "GROUP BY channel, status"
        ),
        {"f": ctx.firm_id, "p": program_id},
    ).all()
    sample = session.scalars(
        select(Notification)
        .where(Notification.firm_id == ctx.firm_id, Notification.program_id == program_id)
        .order_by(Notification.drafted_at, Notification.id)
        .limit(3)
    ).all()
    return {
        "registrations": int(regs.total),
        "matched": int(regs.matched),
        "unmatched": int(regs.unmatched),
        "claim_links": int(regs.links),
        "notifications": {f"{c}:{s}": int(n) for c, s, n in by},
        "sample": [{"channel": n.channel, "status": n.status, "subject": n.subject, "body": n.body} for n in sample],
    }
