"""CPSC recall API client and record parser.

Parsing is strict about identity and dates and lenient about free text: a record that cannot be parsed
raises CpscRecordError, which the runner counts as failed instead of dropping silently.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from recallops.ingest.units import UnitsParse, parse_units
from recallops.logs import get_logger, log_event
from recallops.text import CANONICALIZER_VERSION, build_source_text, clean, sha256_text

log = get_logger("ingest.cpsc")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class CpscRecordError(ValueError):
    """A record that cannot be turned into a recalls row."""


class CpscResponseError(RuntimeError):
    """A window response that is not a JSON list of records."""


class ParsedRecall(BaseModel):
    source: str = "cpsc"
    source_id: str
    source_record_id: str
    recall_date: date
    last_publish_date: date | None
    title: str
    description: str
    url: str
    raw: dict[str, Any]
    raw_sha256: str
    source_text: str
    source_text_sha256: str
    canonicalizer_version: str
    units: UnitsParse
    category: str | None
    retailers: list[str]
    manufacturers: list[str]
    importers: list[str]
    remedies: list[str]
    upcs: list[str]


def canonical_recall_number(value: str | None) -> str:
    """Digits only, so '23-756' (MPR sheet) and '23756' (API) join."""
    return re.sub(r"\D", "", value or "")


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise CpscRecordError(f"{field} missing or malformed: {value!r}")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise CpscRecordError(f"{field} not ISO: {value!r}") from exc


def _names(items: Any, key: str = "Name") -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, Mapping):
            text = clean(str(item.get(key) or ""))
            if text:
                out.append(text)
    return out


def raw_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_record(record: Mapping[str, Any]) -> ParsedRecall:
    if not isinstance(record, Mapping):
        raise CpscRecordError(f"record is not an object: {type(record).__name__}")
    source_id = canonical_recall_number(record.get("RecallNumber"))
    record_id = str(record.get("RecallID") or "").strip()
    if not source_id:
        if not record_id:
            raise CpscRecordError("record has neither RecallNumber nor RecallID")
        source_id = f"rid{record_id}"
    title = clean(record.get("Title"))
    if not title:
        raise CpscRecordError(f"record {source_id} has no title")
    products: Sequence[Mapping[str, Any]] = [
        p for p in record.get("Products") or [] if isinstance(p, Mapping)
    ]
    category = clean(str(products[0].get("Type") or "")) if products else ""
    last_publish = record.get("LastPublishDate")
    source_text = build_source_text(record)
    return ParsedRecall(
        source_id=source_id,
        source_record_id=record_id,
        recall_date=_date(record.get("RecallDate"), "RecallDate"),
        last_publish_date=_date(last_publish, "LastPublishDate") if last_publish else None,
        title=title,
        description=clean(record.get("Description")),
        url=str(record.get("URL") or ""),
        raw=dict(record),
        raw_sha256=raw_sha256(record),
        source_text=source_text,
        source_text_sha256=sha256_text(source_text),
        canonicalizer_version=CANONICALIZER_VERSION,
        units=parse_units(products),
        category=category or None,
        retailers=_names(record.get("Retailers")),
        manufacturers=_names(record.get("Manufacturers")),
        importers=_names(record.get("Importers")),
        remedies=_names(record.get("Remedies")),
        upcs=_names(record.get("ProductUPCs"), "UPC"),
    )


def windows(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Inclusive windows of `days` days covering start..end."""
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)


class CpscClient:
    def __init__(self, client: httpx.Client, base_url: str, retries: int = 3, backoff_s: float = 2.0) -> None:
        self._client = client
        self._base = base_url
        self._retries = retries
        self._backoff = backoff_s

    def fetch_window(self, start: date, end: date) -> list[dict[str, Any]]:
        params = {"format": "json", "RecallDateStart": start.isoformat(), "RecallDateEnd": end.isoformat()}
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            t0 = time.monotonic()
            try:
                response = self._client.get(self._base, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if response.status_code in RETRYABLE_STATUS:
                    last_error = CpscResponseError(f"HTTP {response.status_code}")
                elif response.status_code != 200:
                    raise CpscResponseError(f"HTTP {response.status_code} for {start}..{end}")
                else:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise CpscResponseError(f"non-JSON body for {start}..{end}") from exc
                    if not isinstance(body, list):
                        raise CpscResponseError(
                            f"body is {type(body).__name__}, not a list, for {start}..{end}"
                        )
                    log_event(
                        log,
                        "cpsc_window_fetched",
                        start=str(start),
                        end=str(end),
                        records=len(body),
                        duration_ms=round((time.monotonic() - t0) * 1000),
                    )
                    return body
            if attempt < self._retries:
                time.sleep(self._backoff * (2**attempt))
        raise CpscResponseError(f"window {start}..{end} failed after retries: {last_error}")
