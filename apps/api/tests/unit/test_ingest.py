"""Canonicalizer, units parser, record parser, and API client (no network: httpx.MockTransport)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from recallops.ingest.cpsc_api import (
    CpscClient,
    CpscRecordError,
    CpscResponseError,
    canonical_recall_number,
    parse_record,
    windows,
)
from recallops.ingest.units import parse_units, parse_units_text
from recallops.settings import REPO_ROOT
from recallops.text import build_source_text, clean, find_span, section_bounds

FIXTURE = REPO_ROOT / "data" / "samples" / "cpsc_sample.json"
NBSP, EN_DASH, TRADEMARK, SOFT_HYPHEN = chr(0xA0), chr(0x2013), chr(0x2122), chr(0xAD)


def fixture_records() -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = json.loads(Path(FIXTURE).read_text(encoding="utf-8"))
    return body


# canonicalizer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Scotch&trade; laminator", "Scotch" + TRADEMARK + " laminator"),
        ("a" + NBSP + "b", "a b"),
        ("model" + SOFT_HYPHEN + "TL1302Z", "modelTL1302Z"),
        ("<p>model <b>X1</b></p>", "model X1"),
        ("price < 5 and > 2", "price < 5 and > 2"),
        ("  two   spaces\r\n\r\n\r\n\r\nnext  ", "two spaces\n\nnext"),
        ("dash " + EN_DASH + " kept", "dash " + EN_DASH + " kept"),
        (None, ""),
    ],
)
def test_clean(raw: str | None, expected: str) -> None:
    assert clean(raw) == expected


def test_source_text_sections_and_spans() -> None:
    record = fixture_records()[0]
    text = build_source_text(record)
    bounds = section_bounds(text)
    assert list(bounds)[:4] == ["TITLE", "DESCRIPTION", "PRODUCTS", "SOLD AT"]
    sold = text[bounds["SOLD AT"][0] : bounds["SOLD AT"][1]]
    assert "Amazon.com" in sold
    span = find_span(text, "TL1302Z")
    assert span is not None and text[span[0] : span[1]] == "TL1302Z"
    assert find_span(text, "not in the notice") is None
    assert find_span(text, "") is None


def test_source_text_is_deterministic_for_whole_fixture() -> None:
    for record in fixture_records():
        assert build_source_text(record) == build_source_text(json.loads(json.dumps(record)))


# units


@pytest.mark.parametrize(
    ("text", "value", "status"),
    [
        ("About 53,634", 53634, "ok"),
        ("About 42,860 (In addition, about 340 were sold in Canada)", 42860, "ok"),
        ("About 1.2 million", 1_200_000, "ok"),
        ("Two million MamaRoo swings", 2_000_000, "ok"),
        ("About 17, 370 (In addition, about 782 units were sold in Canada)", 17370, "ok"),
        (
            "About 34,730 16-drawer dressers. (About 76,500 12-drawer dressers were previously recalled)",
            34730,
            "ok",
        ),
        (
            "About 16,800 residential elevators, sold for between $15,000 and $25,000 for a two-landing",
            16800,
            "ok",
        ),
        ("Approximately 8 million. To date, testing has identified bacteria in 2024", 8_000_000, "ok"),
        ("About 7,640 pajamas and about 300 headbands", None, "ambiguous"),
        ("Millions", None, "unparsed"),
        ("", None, "unparsed"),
    ],
)
def test_parse_units_text(text: str, value: int | None, status: str) -> None:
    parsed = parse_units_text(text)
    assert (parsed.value, parsed.status) == (value, status)


def test_parse_units_across_products() -> None:
    same = parse_units([{"NumberOfUnits": "About 500"}, {"NumberOfUnits": "About 500"}])
    assert (same.value, same.status) == (500, "ok")
    differ = parse_units([{"NumberOfUnits": "About 500"}, {"NumberOfUnits": "About 900"}])
    assert (differ.value, differ.status) == (None, "ambiguous")
    assert parse_units([]).status == "unparsed"
    assert parse_units(None).value is None


def test_fixture_units_never_parse_to_zero() -> None:
    for record in fixture_records():
        parsed = parse_record(record)
        assert parsed.units.value != 0
        if parsed.units.status == "ok":
            assert parsed.units.value is not None


# record parser


def test_parse_whole_fixture() -> None:
    records = fixture_records()
    parsed = [parse_record(r) for r in records]
    assert len(parsed) == 51
    assert len({p.source_id for p in parsed}) == 51
    assert all(p.source_id.isdigit() for p in parsed)
    first = parsed[0]
    assert first.source_id == "27024"
    assert first.recall_date == date(2026, 10, 15)
    assert first.units.value == 53634
    assert first.category == "Business and Office Machines"
    assert first.raw == records[0]
    assert len(first.raw_sha256) == 64


def test_canonical_recall_number() -> None:
    assert canonical_recall_number("23-756") == canonical_recall_number("23756") == "23756"
    assert canonical_recall_number(None) == ""


def test_parse_record_rejects_and_falls_back() -> None:
    base = fixture_records()[0]
    with pytest.raises(CpscRecordError):
        parse_record({**base, "RecallNumber": "", "RecallID": ""})
    with pytest.raises(CpscRecordError):
        parse_record({**base, "RecallDate": "10/15/2026"})
    with pytest.raises(CpscRecordError):
        parse_record({**base, "Title": "  "})
    with pytest.raises(CpscRecordError):
        parse_record("not a record")  # type: ignore[arg-type]
    assert parse_record({**base, "RecallNumber": ""}).source_id == f"rid{base['RecallID']}"
    assert parse_record({**base, "LastPublishDate": None}).last_publish_date is None


def test_windows_cover_range_without_gaps() -> None:
    spans = list(windows(date(2026, 9, 1), date(2026, 10, 16), 30))
    assert spans == [(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 10, 1), date(2026, 10, 16))]


# client


def _client(handler: Any) -> CpscClient:
    return CpscClient(httpx.Client(transport=httpx.MockTransport(handler)), "https://example.test/Recall", backoff_s=0)


def test_client_returns_records_and_sends_window_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"RecallNumber": "1"}])

    assert _client(handler).fetch_window(date(2026, 9, 1), date(2026, 9, 30)) == [{"RecallNumber": "1"}]
    assert seen[0].url.params["RecallDateStart"] == "2026-09-01"
    assert seen[0].url.params["format"] == "json"


def test_client_retries_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=[])

    assert _client(handler).fetch_window(date(2026, 1, 1), date(2026, 1, 2)) == []
    assert calls["n"] == 3


def test_client_does_not_retry_4xx() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400)

    with pytest.raises(CpscResponseError, match="HTTP 400"):
        _client(handler).fetch_window(date(2026, 1, 1), date(2026, 1, 2))
    assert calls["n"] == 1


@pytest.mark.parametrize("body", [b"<html>Access Denied</html>", b'{"error": "x"}'])
def test_client_rejects_non_list_bodies(body: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with pytest.raises(CpscResponseError):
        _client(handler).fetch_window(date(2026, 1, 1), date(2026, 1, 2))


def test_client_gives_up_after_retries_on_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(CpscResponseError, match="after retries"):
        _client(handler).fetch_window(date(2026, 1, 1), date(2026, 1, 2))
