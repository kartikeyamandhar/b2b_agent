"""Company verification (migrations 0008 and 0009; decisions log 2026-10-22), with ACCESS_APPROVAL=required as in
production.

A company reaches its account, access, and billing routes, and nothing else, until the platform operator approves a
recall for it; one company at most holds a recall, and a company can never approve itself. Approving opens the
company's product at once (plan v3), revoking closes it, and on Free a company runs one product."""

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
        # Revoking closes a product (plan v3): each test starts with the seeded products open again.
        conn.execute(
            text("UPDATE recall_programs SET status = 'open', closed_at = NULL WHERE id = ANY(:p)"),
            {"p": seeded["programs"]},
        )
        conn.execute(text("UPDATE firms SET plan = 'free', onboarded_at = now() WHERE id = :f"), {"f": IMPOSTOR})
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


def _program(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    items = client.get("/v1/programs", headers=headers).json()["items"]
    assert len(items) == 1, items
    return dict(items[0])


def _intake(url: str) -> str:
    return "/portal/intake/" + url.rsplit("/", 1)[1]


def test_an_unapproved_company_reaches_only_its_account_access_and_billing(client: TestClient) -> None:
    denied = client.get("/v1/programs", headers=FAKE)
    assert denied.status_code == 403 and "not approved" in denied.json()["detail"]
    usage = client.get("/v1/usage", headers=FAKE)  # the plan and paying for it work before approval (plan v3)
    assert usage.status_code == 200 and usage.json()["limits"] == {"active_products": 1}
    assert "not approved" not in client.post("/v1/billing/portal", headers=FAKE).text
    status = client.get("/v1/access", headers=FAKE).json()
    assert status == {**status, "required": True, "approved": False, "operator": False, "requests": []}
    assert client.get("/v1/recalls?q=laminator", headers=FAKE).status_code == 200  # finding its recall is allowed
    assert client.get("/v1/programs", headers=DEMO).status_code == 200  # the seeded firm is approved


def test_a_request_starts_pending_and_a_firm_can_never_approve_itself(
    client: TestClient, seeded: dict[str, Any], app_engine_test: Engine
) -> None:
    made = _request(client, FAKE, recall_id=seeded["laminator"])
    assert made["status"] == "requested" and made["typed"] is False and "Laminator" in made["title"]
    again = client.post("/v1/access", json={"recall_id": str(seeded["laminator"])}, headers=FAKE)
    assert again.status_code == 402  # free covers one product, and one request is already waiting
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
    assert row["firm_plan"] == "free" and row["firm_active_products"] == 0
    second = _decide(client, made["id"], "approved")
    assert second.status_code == 409 and "already approved" in second.json()["detail"]  # one firm per recall
    rejected = _decide(client, made["id"], "rejected")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert _decide(client, made["id"], "approved").status_code == 409  # a rejection is final
    assert client.get("/v1/programs", headers=FAKE).status_code == 403


def test_revoking_pauses_the_product_and_approving_again_resumes_the_same_link(
    client: TestClient, seeded: dict[str, Any], internal_engine_test: Engine
) -> None:
    """Revoking stops the console, every link, and the worker without closing the product: closing is final, and a
    re-approval used to make a second product with a new link, stranding the first one's claimants (plan v3 review)."""
    claim = str(seeded["demo"]["first_claim_id"])
    claim_link = f"/portal/dev.{seed.DEMO_FIRM_ID}.{claim}"
    product_link = "/portal/intake/" + _program(client, DEMO)["intake_url"].rsplit("/", 1)[1]
    assert client.get(claim_link).status_code == 200 and client.get(product_link).status_code == 200
    demo_access = next(
        r for r in client.get("/v1/operator/access", headers=OPERATOR).json() if r["firm_id"] == str(seed.DEMO_FIRM_ID)
    )
    revoked = _decide(client, demo_access["id"], "revoked").json()
    assert revoked["status"] == "revoked" and revoked["product"] is None
    with internal_engine_test.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM recall_programs WHERE id = :p"), {"p": seeded["demo_program"]}
        ).scalar_one()
    assert status == "open"  # paused by the approval check, not closed
    assert client.get("/v1/programs", headers=DEMO).status_code == 403
    assert client.get(claim_link).status_code == 404 and client.get(product_link).status_code == 404
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'submitted', lease_until = NULL WHERE id = :c"), {"c": claim})
    worker = Worker(engine=internal_engine_test, settings=get_settings(), llm=fakes.client([])[0], worker_id="w")
    assert worker.lease_claim() is None  # the only submitted claim belongs to a revoked firm: left alone

    again = _request(client, DEMO, recall_id=seeded["laminator"])
    product = _decide(client, again["id"], "approved").json()["product"]
    assert product == {"id": str(seeded["demo_program"]), "status": "open", "error": None}  # the same product
    assert client.get(claim_link).status_code == 200 and client.get(product_link).status_code == 200  # same links
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE claims SET status = 'approved' WHERE id = :c"), {"c": claim})


def test_the_operator_sees_each_companys_products_and_an_approved_company_keeps_its_name(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    listed = client.get("/v1/operator/access", headers=OPERATOR).json()
    demo = next(r for r in listed if r["firm_id"] == str(seed.DEMO_FIRM_ID))
    assert demo["firm_active_products"] == 1  # read through the owner policy (it counted 0 under forced RLS)
    renamed = client.patch("/v1/firm", json={"name": "Walmart Refund Center"}, headers=DEMO)
    assert renamed.status_code == 409 and "operator" in renamed.json()["detail"]
    assert client.patch("/v1/firm", json={"name": "Impostor Holdings"}, headers=FAKE).status_code == 200  # unapproved
    client.patch("/v1/firm", json={"name": "Impostor Co"}, headers=FAKE)


def test_approval_opens_the_product_with_its_one_claim_link(client: TestClient, seeded: dict[str, Any]) -> None:
    made = _request(client, FAKE, recall_id=seeded["spare"])
    approved = _decide(client, made["id"], "approved")
    assert approved.status_code == 200, approved.text
    product = approved.json()["product"]
    assert product["status"] in ("open", "draft") and product["id"]
    program = _program(client, FAKE)
    assert program["id"] == product["id"]
    if program["needs_refund_amount"]:  # a refund notice with no price waits for the amount (plan v3)
        assert product == {"id": program["id"], "status": "draft", "error": "missing_refund_amount"}
        assert program["intake_url"] is None
        return
    assert program["status"] == "open" and program["takes_new_claims"] and program["intake_url"]
    view = client.get(_intake(program["intake_url"]))
    assert view.status_code == 200 and view.json()["company_name"] == "Impostor Co"
    status = client.get("/v1/access", headers=FAKE).json()
    assert status["approved"] is True and status["requests"][0]["program_status"] == "open"


def test_free_covers_one_product_and_growth_lifts_it(
    client: TestClient, seeded: dict[str, Any], internal_engine_test: Engine
) -> None:
    made = _request(client, FAKE, recall_id=seeded["spare"])
    assert _decide(client, made["id"], "approved").status_code == 200
    typed = {"typed": {"title": "Acme Kettle Recall", "company": "Acme", "notice": "Acme recalls kettle K-1."}}
    second = client.post("/v1/access", json=typed, headers=FAKE)
    assert second.status_code == 402
    assert second.json()["detail"] == "Free covers one product. Upgrade to Growth to add another."  # a sentence
    with internal_engine_test.begin() as conn:  # an unlimited plan (enterprise is never billed)
        conn.execute(text("UPDATE firms SET plan = 'enterprise' WHERE id = :f"), {"f": IMPOSTOR})
    assert client.post("/v1/access", json=typed, headers=FAKE).status_code == 201


def test_a_company_finishes_onboarding_before_asking(
    client: TestClient, seeded: dict[str, Any], internal_engine_test: Engine
) -> None:
    with internal_engine_test.begin() as conn:
        conn.execute(text("UPDATE firms SET onboarded_at = NULL WHERE id = :f"), {"f": IMPOSTOR})
    early = client.post("/v1/access", json={"recall_id": str(seeded["spare"])}, headers=FAKE)
    assert early.status_code == 409 and "setting up your company" in early.json()["detail"]
    named = client.post("/v1/onboarding", json={"name": "Impostor Co\u202e", "plan": "free"}, headers=FAKE)
    assert named.status_code == 200 and named.json()["checkout_url"] is None
    assert named.json()["firm"]["name"] == "Impostor Co"  # format characters cannot reorder a name on screen
    assert client.post("/v1/onboarding", json={"name": "Again", "plan": "free"}, headers=FAKE).status_code == 409
    assert client.post("/v1/access", json={"recall_id": str(seeded["spare"])}, headers=FAKE).status_code == 201


def test_a_typed_notice_opens_its_own_product_on_approval(client: TestClient, internal_engine_test: Engine) -> None:
    notice = "Acme recalls about 1,200 Kettle model K-1 sold at Target from January 2025 for about $40. Stop using."
    typed = _request(client, FAKE, typed={"title": "Acme Kettle Recall", "company": "Acme", "notice": notice})
    assert typed["typed"] is True and typed["status"] == "requested"
    product = _decide(client, typed["id"], "approved").json()["product"]
    assert product["id"] is not None
    program = _program(client, FAKE)
    assert program["title"] == "Acme Kettle Recall" and program["id"] == product["id"]
    manual = {"title": "Acme Kettle Recall", "description": notice}
    again = client.post("/v1/programs", json={"manual": manual, "access_id": typed["id"]}, headers=FAKE)
    assert again.status_code == 409  # one product per typed notice


def test_approving_twice_makes_one_product(
    client: TestClient, seeded: dict[str, Any], internal_engine_test: Engine
) -> None:
    made = _request(client, FAKE, recall_id=seeded["spare"])
    assert _decide(client, made["id"], "approved").status_code == 200
    assert _decide(client, made["id"], "approved").status_code == 409  # the double click
    with internal_engine_test.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM recall_programs WHERE firm_id = :f"), {"f": IMPOSTOR}).scalar_one()
    assert n == 1


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
