"""One public claim link per product (plan v3; invariant 10 amended): the link shows the product's public facts and
starts claims, each with its own private link; replacing the link or closing the product stops it; a forged, stale, or
draft product's link is a 404; starting claims is JSON-only and rate-limited per address and per link; a claim needs
its label photo before it is submitted. Also the account routes a company uses before approval: /v1/plans, /v1/me,
PATCH /v1/firm, and onboarding."""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import seed
from recallops.settings import get_settings
from tests.api.test_m2_flows import ADMIN, FIELDS, REVIEWER, jpeg, open_program

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def env(internal_engine_test: Engine) -> Iterator[dict[str, Any]]:
    seeded = seed.run(internal_engine_test, n=6)
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE firms SET plan = 'enterprise' WHERE id = :f"), {"f": seed.DEMO_FIRM_ID})
    from recallops.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "seeded": seeded, "engine": internal_engine_test}


@pytest.fixture(autouse=True)
def _fresh_limits(internal_engine_test: Engine) -> None:
    with internal_engine_test.begin() as conn:
        conn.execute(text("DELETE FROM rate_limits"))


def _path(url: str) -> str:
    return "/portal/intake/" + url.rsplit("/", 1)[1]


def _start(client: TestClient, url: str) -> dict[str, Any]:
    started = client.post(_path(url), json={})
    assert started.status_code == 201, started.text
    return dict(started.json())


def test_a_products_one_link_shows_the_product_and_starts_private_claims(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    assert program["takes_new_claims"] is True and program["intake_url"]
    assert program["intake_url"].startswith(get_settings().intake_base())
    view = client.get(_path(program["intake_url"]))
    assert view.status_code == 200
    assert view.headers["cache-control"] == "no-store" and view.headers["referrer-policy"] == "no-referrer"
    body = view.json()
    assert body["company_name"] == "Demo Recall Co" and body["models"] == ["TL1302Z"]
    assert body["required_photos"][0] == "label" and "destruction" in body["required_photos"]
    assert "claim_id" not in body and "firm_id" not in body  # public facts only

    form = client.post(_path(program["intake_url"]), content=b"x=1", headers={"content-type": "text/plain"})
    assert form.status_code == 415  # no simple cross-site form can start claims
    first, second = _start(client, program["intake_url"]), _start(client, program["intake_url"])
    assert first["claim_id"] != second["claim_id"] and first["claim_token"] != second["claim_token"]
    assert first["claim_url"] == f"{get_settings().portal_base_url.rstrip('/')}/{first['claim_token']}"
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, program_id FROM claims WHERE id = ANY(:c)"),
            {"c": [first["claim_id"], second["claim_id"]]},
        ).all()
    assert {(r.status, str(r.program_id)) for r in rows} == {("draft", program["id"])}
    queue = client.get(f"/v1/programs/{program['id']}/claims", headers=ADMIN).json()["items"]
    started = client.get(f"/v1/programs/{program['id']}/claims?status=draft", headers=ADMIN).json()["items"]
    assert queue == [] and {c["id"] for c in started} == {first["claim_id"], second["claim_id"]}  # not in the queue

    token = first["claim_token"]
    portal = client.get(f"/portal/{token}").json()
    assert portal["program"]["company_name"] == "Demo Recall Co"
    early = client.post(f"/portal/{token}/submit", json=FIELDS)
    assert early.status_code == 409 and "label" in early.json()["detail"]  # every claim brings its label photo
    up = client.post(
        f"/portal/{token}/photos",
        data={"kind": "label"},
        files={"file": ("label.jpg", io.BytesIO(jpeg("label", 41)), "image/jpeg")},
    )
    assert up.status_code == 200, up.text
    assert client.post(f"/portal/{token}/submit", json=FIELDS).json()["status"] == "submitted"


def test_replacing_the_link_stops_the_old_one_and_closing_stops_every_link(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    old = program["intake_url"]
    started = _start(client, old)
    assert client.post(f"/v1/programs/{program['id']}/intake/replace", headers=REVIEWER).status_code == 403
    replaced = client.post(f"/v1/programs/{program['id']}/intake/replace", headers=ADMIN)
    assert replaced.status_code == 200, replaced.text
    new = replaced.json()["intake_url"]
    assert new and new != old
    assert client.get(_path(old)).status_code == 404 and client.post(_path(old), json={}).status_code == 404
    assert client.get(_path(new)).status_code == 200
    assert client.get(f"/portal/{started['claim_token']}").status_code == 200  # a started claim keeps its own link
    with engine.connect() as conn:
        audit = conn.execute(
            text("SELECT before, after FROM audit_log WHERE action = 'intake_link_replaced' AND target_id = :p"),
            {"p": program["id"]},
        ).one()
    assert audit.after["version"] == audit.before["version"] + 1

    closed = client.patch(f"/v1/programs/{program['id']}", headers=ADMIN, json={"status": "closed"})
    assert closed.status_code == 200 and closed.json()["intake_url"] is None
    assert client.get(_path(new)).status_code == 404
    again = client.post(f"/v1/programs/{program['id']}/intake/replace", headers=ADMIN)
    assert again.status_code == 409


def test_forged_stale_and_draft_links_are_404(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    firm, pid = seed.DEMO_FIRM_ID, program["id"]
    assert client.get(f"/portal/intake/intake.{firm}.{pid}.1").status_code == 200
    for forged in (
        f"intake.{firm}.{pid}.2",  # a version that was never issued
        f"intake.{seed.OTHER_FIRM_ID}.{pid}.1",  # another firm's id around this product
        f"intake.{firm}.{uuid.uuid4()}.1",  # no such product
        f"dev.{firm}.{pid}",  # a claim token's shape
        "x" * 91,
    ):
        assert client.get(f"/portal/intake/{forged}").status_code == 404, forged
    with engine.begin() as conn:
        recall_id = conn.execute(text("SELECT id FROM recalls WHERE source_id <> '27024' LIMIT 1")).scalar_one()
    draft = client.post("/v1/programs", headers=ADMIN, json={"recall_id": str(recall_id)}).json()
    assert draft["status"] == "draft" and draft["intake_url"] is None and draft["takes_new_claims"] is False
    assert client.get(f"/portal/intake/intake.{firm}.{draft['id']}.1").status_code == 404
    client.patch(f"/v1/programs/{draft['id']}", headers=ADMIN, json={"status": "closed"})


def test_starting_claims_is_limited_per_address_and_per_link(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    url = program["intake_url"]
    limit = get_settings().intake_rate_per_ip_hour
    for _ in range(limit):
        _start(client, url)
    over = client.post(_path(url), json={})
    assert over.status_code == 429 and int(over.headers["retry-after"]) > 0
    assert client.get(_path(url)).status_code == 200  # reading the link is a portal read, not a start

    monkeypatch.setenv("INTAKE_RATE_PER_IP_HOUR", "1000")
    monkeypatch.setenv("INTAKE_RATE_PER_PROGRAM_HOUR", str(limit + 2))
    get_settings.cache_clear()
    try:
        _start(client, url)
        _start(client, url)
        assert client.post(_path(url), json={}).status_code == 429  # the link's own hourly cap
    finally:
        get_settings.cache_clear()


def test_the_account_routes_work_before_approval(env: dict[str, Any], internal_engine_test: Engine) -> None:
    client = env["client"]
    plans = client.get("/v1/plans")
    assert plans.status_code == 200
    by_id = {p["id"]: p for p in plans.json()}
    assert (
        by_id["free"]["product_limit"] == get_settings().free_active_products
        and by_id["growth"]["product_limit"] is None
    )
    assert all(p["refund_options"] == ["stripe_connect", "check_export"] for p in by_id.values())

    me = client.get("/v1/me", headers=ADMIN).json()
    assert me["operator"] is False
    assert me["firm"] | {"name": "Demo Recall Co", "onboarded": True, "effective_plan": "enterprise"} == me["firm"]
    assert me["firm"]["product_limit"] is None

    new_firm = uuid.uuid4()  # the audit log is append-only: each run brings its own company
    new = {"X-Dev-Firm-Id": str(new_firm), "X-Dev-Role": "admin", "X-Dev-User-Id": "user_new"}
    with internal_engine_test.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'New company')"), {"f": new_firm})
    fresh = client.get("/v1/me", headers=new).json()["firm"]
    assert fresh["onboarded"] is False and fresh["plan"] == "free" and fresh["product_limit"] == 1
    assert client.post("/v1/onboarding", headers=new, json={"name": "  \u200b ", "plan": "free"}).status_code == 422
    grown = client.post("/v1/onboarding", headers=new, json={"name": "Kettle  Works", "plan": "growth"})
    assert grown.status_code == 200, grown.text
    # Without Stripe test keys the Checkout cannot start: the company is set up on Free and Billing offers it again.
    assert grown.json()["firm"] | {"name": "Kettle Works", "onboarded": True, "plan": "free"} == grown.json()["firm"]
    renamed = client.patch("/v1/firm", headers=new, json={"name": "Kettle Works Inc."})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Kettle Works Inc."
    reviewer = {**new, "X-Dev-Role": "reviewer"}
    assert client.patch("/v1/firm", headers=reviewer, json={"name": "Mine now"}).status_code == 403
    with internal_engine_test.connect() as conn:
        actions = (
            conn.execute(text("SELECT action FROM audit_log WHERE firm_id = :f ORDER BY at, id"), {"f": new_firm})
            .scalars()
            .all()
        )
    assert actions == ["company_onboarded", "company_renamed"]


def test_billing_sessions_are_limited_per_company(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Billing is open before approval, so a new account must not be able to call Stripe in a loop."""
    client = env["client"]
    monkeypatch.setenv("BILLING_RATE_PER_FIRM_HOUR", "2")
    get_settings.cache_clear()
    try:
        first = [client.post("/v1/billing/portal", headers=ADMIN).status_code for _ in range(2)]
        assert 429 not in first  # without Stripe test keys these fail on configuration, after the limit
        over = client.post("/v1/billing/checkout", headers=ADMIN)
        assert over.status_code == 429 and int(over.headers["retry-after"]) > 0
        assert (
            client.post("/v1/billing/portal", headers={**ADMIN, "X-Dev-Firm-Id": str(seed.OTHER_FIRM_ID)}).status_code
            != 429
        )
    finally:
        get_settings.cache_clear()


def test_refused_starts_never_spend_a_links_budget(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-link limit was bumped before the request was checked, so 60 cross-site text/plain POSTs (415s) locked a
    product's only link for an hour (plan v3 review). Only starts that pass every check count now."""
    client, engine = env["client"], env["engine"]
    url = open_program(client, engine)["intake_url"]
    monkeypatch.setenv("INTAKE_RATE_PER_IP_HOUR", "1000")
    monkeypatch.setenv("INTAKE_RATE_PER_PROGRAM_HOUR", "2")
    get_settings.cache_clear()
    try:
        for _ in range(5):
            assert client.post(_path(url), content=b"x", headers={"content-type": "text/plain"}).status_code == 415
        assert client.post(_path(url)[:-1] + "9", json={}).status_code == 404  # a forged version: not counted
        _start(client, url)
        _start(client, url)
        over = client.post(_path(url), json={})
        assert over.status_code == 429 and int(over.headers["retry-after"]) > 0
    finally:
        get_settings.cache_clear()


def test_the_archive_search_finds_a_recall_by_its_number(env: dict[str, Any]) -> None:
    client = env["client"]
    for q in ("27024", "27-024"):
        found = client.get(f"/v1/recalls?q={q}", headers=ADMIN).json()
        assert found["items"] and found["items"][0]["source_id"] == "27024", q
    assert client.get("/v1/recalls?q=laminator", headers=ADMIN).json()["items"][0]["source_id"] == "27024"
