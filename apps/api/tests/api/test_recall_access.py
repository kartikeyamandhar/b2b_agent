"""Company verification (migration 0008; decisions log 2026-10-22), with ACCESS_APPROVAL=required as in production.

The first live deploy let any signed-up organization run any recall: an organization called "3M" could open the
Primark recall, issue claim links in Primark's name, and pay refunds. Now an organization reaches nothing but its
access requests until the platform operator approves a recall for it, one organization at most holds a recall, a
firm can never approve itself, and revoking stops the console, claim links, and new payments."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from recallops import access, seed
from recallops.claims.worker import Worker
from recallops.db.session import tenant_session
from recallops.settings import Settings, get_settings
from recallops.tenancy import FirmContext
from tests import fakes
from tests.conftest import CLEAN_ORDER

pytestmark = pytest.mark.db

IMPOSTOR = uuid.UUID("00000000-0000-4000-8000-0000000000e1")
OPERATOR_ID = "user_operator"
DEMO = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}
FAKE = {"X-Dev-Firm-Id": str(IMPOSTOR), "X-Dev-Role": "admin", "X-Dev-User-Id": "user_impostor"}
OPERATOR = {"X-Dev-Firm-Id": str(IMPOSTOR), "X-Dev-Role": "admin", "X-Dev-User-Id": OPERATOR_ID}


@pytest.fixture(scope="module")
def seeded(internal_engine_test: Engine) -> dict[str, Any]:
    with internal_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    result = seed.run(internal_engine_test, n=12)
    with internal_engine_test.begin() as conn:
        conn.execute(text("INSERT INTO firms (id, name) VALUES (:f, 'Impostor Co')"), {"f": IMPOSTOR})
        programs = conn.execute(text("SELECT id, firm_id, recall_id FROM recall_programs")).all()
        spare = conn.execute(
            text("SELECT id FROM recalls WHERE id <> ALL(:used) ORDER BY recall_date DESC LIMIT 1"),
            {"used": [p.recall_id for p in programs]},
        ).scalar_one()
    demo = next(p for p in programs if p.firm_id == seed.DEMO_FIRM_ID)
    return result | {
        "programs": [p.id for p in programs],
        "laminator": demo.recall_id,
        "spare": spare,
        "demo_program": demo.id,
    }


@pytest.fixture()
def client(
    seeded: dict[str, Any], internal_engine_test: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Approval required, one operator; each test starts from the seed's approvals only."""
    monkeypatch.setenv("ACCESS_APPROVAL", "required")
    monkeypatch.setenv("PLATFORM_OPERATOR_USER_IDS", OPERATOR_ID)
    get_settings.cache_clear()
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE recall_programs SET access_id = NULL"))
        conn.execute(text("DELETE FROM recall_programs WHERE firm_id = :f"), {"f": IMPOSTOR})
        conn.execute(text("DELETE FROM recall_access"))
    seed.approve_seeded(internal_engine_test, seeded["programs"])
    from recallops.api.app import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


def _request(client: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    r = client.post(
        "/v1/access", json={k: str(v) if isinstance(v, UUID) else v for k, v in body.items()}, headers=headers
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def _decide(client: TestClient, access_id: str, decision: str) -> Any:
    return client.post(f"/v1/operator/access/{access_id}", json={"decision": decision}, headers=OPERATOR)


def test_an_unapproved_organization_reaches_only_its_access_requests(client: TestClient) -> None:
    denied = client.get("/v1/programs", headers=FAKE)
    assert denied.status_code == 403 and "not approved" in denied.json()["detail"]
    assert client.post("/v1/billing/portal", headers=FAKE).status_code == 403  # billing is gated too
    assert client.get("/v1/usage", headers=FAKE).status_code == 403
    status = client.get("/v1/access", headers=FAKE).json()
    assert status == {**status, "required": True, "approved": False, "operator": False, "requests": []}
    assert client.get("/v1/recalls?q=laminator", headers=FAKE).status_code == 200  # finding its recall is allowed
    assert client.get("/v1/programs", headers=DEMO).status_code == 200  # the seeded firm is approved


def test_a_request_starts_pending_and_a_firm_can_never_approve_itself(
    client: TestClient, seeded: dict[str, Any], app_engine_test: Engine
) -> None:
    made = _request(client, FAKE, recall_id=seeded["laminator"])
    assert made["status"] == "requested" and made["typed"] is False and "Laminator" in made["title"]
    assert client.post("/v1/access", json={"recall_id": str(seeded["laminator"])}, headers=FAKE).status_code == 409
    ctx = FirmContext(firm_id=IMPOSTOR, user_id="user_impostor", role="admin")
    with tenant_session(ctx, engine=app_engine_test) as session:
        with pytest.raises(ProgrammingError, match="permission denied"), session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO recall_access (firm_id, recall_id, status, requested_by) VALUES (:f, :r, 'approved', 'x')"
                ),
                {"f": IMPOSTOR, "r": seeded["spare"]},
            )
        with pytest.raises(ProgrammingError, match="permission denied"), session.begin_nested():
            session.execute(text("UPDATE recall_access SET status = 'approved' WHERE firm_id = :f"), {"f": IMPOSTOR})
    assert client.get("/v1/access", headers=FAKE).json()["approved"] is False


def test_only_the_operator_lists_and_decides_and_sees_who_already_holds_a_recall(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    made = _request(client, FAKE, recall_id=seeded["laminator"])
    assert client.get("/v1/operator/access", headers=FAKE).status_code == 403
    assert (
        client.post(f"/v1/operator/access/{made['id']}", json={"decision": "approved"}, headers=FAKE).status_code == 403
    )
    listed = {r["id"]: r for r in client.get("/v1/operator/access", headers=OPERATOR).json()}
    row = listed[made["id"]]
    assert row["firm_name"] == "Impostor Co" and row["status"] == "requested"
    assert row["notice_company"] == "3M Company" and row["approved_elsewhere"] == "Demo Recall Co"
    second = _decide(client, made["id"], "approved")
    assert second.status_code == 409 and "already approved" in second.json()["detail"]  # one firm per recall
    rejected = _decide(client, made["id"], "rejected")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert _decide(client, made["id"], "approved").status_code == 409  # a rejection is final
    assert client.get("/v1/programs", headers=FAKE).status_code == 403


def test_revoking_stops_the_console_claim_links_and_the_worker(
    client: TestClient, seeded: dict[str, Any], internal_engine_test: Engine
) -> None:
    claim = str(seeded["demo"]["first_claim_id"])
    assert client.get(f"/portal/dev.{seed.DEMO_FIRM_ID}.{claim}").status_code == 200
    demo_access = next(
        r for r in client.get("/v1/operator/access", headers=OPERATOR).json() if r["firm_id"] == str(seed.DEMO_FIRM_ID)
    )
    assert _decide(client, demo_access["id"], "revoked").json()["status"] == "revoked"
    assert client.get("/v1/programs", headers=DEMO).status_code == 403
    assert client.get(f"/portal/dev.{seed.DEMO_FIRM_ID}.{claim}").status_code == 404  # links already sent stop
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'submitted', lease_until = NULL WHERE id = :c"), {"c": claim})
    worker = Worker(engine=internal_engine_test, settings=get_settings(), llm=fakes.client([])[0], worker_id="w")
    assert worker.lease_claim() is None  # the only submitted claim belongs to a revoked firm: left alone
    audit = client.get(f"/v1/claims/{claim}", headers=DEMO)
    assert audit.status_code == 403


def test_programs_need_an_approved_recall_and_a_typed_notice_needs_its_own_approval(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    spare = _request(client, FAKE, recall_id=seeded["spare"])
    assert _decide(client, spare["id"], "approved").status_code == 200
    assert client.get("/v1/access", headers=FAKE).json()["approved"] is True
    taken = client.post("/v1/programs", json={"recall_id": str(seeded["laminator"])}, headers=FAKE)
    assert taken.status_code == 403  # approved for another recall, not this one
    assert client.post("/v1/programs", json={"recall_id": str(seeded["spare"])}, headers=FAKE).status_code == 201
    manual = {"title": "Acme Kettle Recall", "description": "Acme recalls kettle model K-1 sold at Target."}
    assert client.post("/v1/programs", json={"manual": manual}, headers=FAKE).status_code == 403
    typed = _request(
        client, FAKE, typed={"title": "Acme Kettle Recall", "company": "Acme", "notice": "Acme recalls..."}
    )
    assert typed["typed"] is True
    assert _decide(client, typed["id"], "approved").status_code == 200
    made = client.post("/v1/programs", json={"manual": manual, "access_id": typed["id"]}, headers=FAKE)
    assert made.status_code == 201
    again = client.post("/v1/programs", json={"manual": manual, "access_id": typed["id"]}, headers=FAKE)
    assert again.status_code == 409  # one program per typed notice


def test_production_refuses_to_run_without_verification() -> None:
    with pytest.raises(ValidationError, match="ACCESS_APPROVAL must be required in prod"):
        Settings(app_env="prod", access_approval="off", neo4j_uri="", portal_base_url="https://recallops.example/claim")


def test_domain_hints_compare_the_requester_with_the_notice() -> None:
    class R:
        title = "3M Company Recalls Scotch Thermal Laminators"
        source_text = "Contact 3M at www.scotchbrand.com or visit www.cpsc.gov for details."

    company, sites = access.notice_hints(R())  # type: ignore[arg-type]
    assert company == "3M Company" and sites == ["scotchbrand.com"]
    assert access.domain_matches("mmm.com", company, sites) is False
    assert access.domain_matches("scotchbrand.com", company, sites) is True
    assert access.domain_matches("3m.com", company, sites) is True  # the company's own name
    assert access.domain_matches(None, company, sites) is False
