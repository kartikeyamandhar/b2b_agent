"""API contract and seams against the test database, seeded with a small `make seed` (n=20)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from recallops import seed
from recallops.api.export_openapi import render
from recallops.auth.dev import DevAuthProvider, DevClaimTokenVerifier, UnsafeDevAuth
from recallops.auth.provider import AuthError
from recallops.billing.fake import AllowAllBilling
from recallops.payouts.fake import FakeLedger
from recallops.settings import REPO_ROOT, Settings
from recallops.tenancy import FirmContext

pytestmark = pytest.mark.db

DEMO = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}
REVIEWER = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "reviewer"}
OTHER = {"X-Dev-Firm-Id": str(seed.OTHER_FIRM_ID), "X-Dev-Role": "admin"}


@pytest.fixture(scope="module")
def seeded(internal_engine_test: Engine) -> dict[str, Any]:
    from sqlalchemy import text

    with internal_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        conn.execute(text("DELETE FROM rate_limits"))
        for table in (
            "agent_steps",
            "agent_runs",
            "claim_photos",
            "fraud_clusters",
            "audit_log",
            "claims",
            "recall_programs",
            "usage_meters",
            "firm_members",
            "firms",
        ):
            conn.execute(text(f"DELETE FROM {table}"))
    return seed.run(internal_engine_test, n=20)


@pytest.fixture(scope="module")
def client(seeded: dict[str, Any]) -> Iterator[TestClient]:
    from recallops.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_programs_and_counts(client: TestClient, seeded: dict[str, Any]) -> None:
    body = client.get("/v1/programs", headers=DEMO).json()
    assert [p["id"] for p in body["items"]] == [seeded["demo"]["program_id"]]
    program = body["items"][0]
    assert sum(program["claim_counts"].values()) == 20
    assert program["identification_rules"]["models"] == ["TL1302Z"]


def test_claims_queue_paginates_without_overlap(client: TestClient, seeded: dict[str, Any]) -> None:
    pid = seeded["demo"]["program_id"]
    first = client.get(f"/v1/programs/{pid}/claims?limit=7", headers=DEMO).json()
    second = client.get(f"/v1/programs/{pid}/claims?limit=7&cursor={first['next_cursor']}", headers=DEMO).json()
    ids = [c["id"] for c in first["items"]] + [c["id"] for c in second["items"]]
    assert len(ids) == len(set(ids)) == 14
    assert client.get(f"/v1/programs/{pid}/claims?cursor=garbage", headers=DEMO).status_code == 400
    flagged = client.get(f"/v1/programs/{pid}/claims?status=flagged", headers=DEMO).json()["items"]
    assert all(c["status"] == "flagged" for c in flagged)


def test_claim_detail_trace_and_photo(client: TestClient, seeded: dict[str, Any]) -> None:
    claim_id = seeded["demo"]["first_claim_id"]
    detail = client.get(f"/v1/claims/{claim_id}", headers=REVIEWER).json()
    assert detail["decision"]["decided_by"] == "rules_only"
    assert detail["audit"][0]["action"] == "claim_adjudicated"
    trace = client.get(f"/v1/claims/{claim_id}/trace", headers=REVIEWER).json()
    assert [s["node"] for s in trace["steps"]][-1] == "decide" and trace["run"]["steps"] == 7
    assert trace["caps"] == {"max_steps": 8, "max_usd": "0.02", "max_seconds": 60.0}
    photo = client.get(detail["photos"][0]["url"], headers=REVIEWER)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"


def test_cross_tenant_reads_are_404(client: TestClient, seeded: dict[str, Any]) -> None:
    claim_id, pid = seeded["demo"]["first_claim_id"], seeded["demo"]["program_id"]
    for path in (
        f"/v1/claims/{claim_id}",
        f"/v1/claims/{claim_id}/trace",
        f"/v1/programs/{pid}",
        f"/v1/programs/{pid}/claims",
    ):
        response = client.get(path, headers=OTHER)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == "application/problem+json"
    detail = client.get(f"/v1/claims/{claim_id}", headers=DEMO).json()
    assert client.get(detail["photos"][0]["url"], headers=OTHER).status_code == 404
    assert [p["id"] for p in client.get("/v1/programs", headers=OTHER).json()["items"]] == [
        seeded["other"]["program_id"]
    ]


def test_auth_failures_and_roles(client: TestClient) -> None:
    assert client.get("/v1/programs").status_code == 403
    assert client.get("/v1/programs", headers={"X-Dev-Firm-Id": "not-a-uuid"}).status_code == 401
    assert client.get("/v1/programs", headers={**DEMO, "X-Dev-Role": "owner"}).status_code == 401
    assert client.post("/v1/programs", headers=REVIEWER, json={}).status_code == 403


def test_errors_are_problem_details_and_payments_stubs_stay_501(client: TestClient, seeded: dict[str, Any]) -> None:
    response = client.post("/v1/programs", headers=DEMO, json={"recall_id": None})
    body = response.json()
    assert response.status_code == 422
    assert body["title"] == "Unprocessable Entity" and "recall_id" in body["detail"] and body["correlation_id"]
    assert response.headers["x-request-id"] == body["correlation_id"]
    stub = client.post("/internal/stripe/webhook")
    assert stub.status_code == 501 and "payments owner" in stub.json()["detail"]
    assert client.post("/v1/billing/checkout", headers=DEMO).status_code == 501
    assert client.post("/internal/feeds/pull").status_code == 401
    assert client.post("/internal/feeds/pull", headers={"X-Internal-Secret": "wrong"}).status_code == 401


def test_recall_search_usage_portal_and_health(client: TestClient, seeded: dict[str, Any]) -> None:
    found = client.get("/v1/recalls?q=laminator", headers=DEMO).json()
    assert any(r["source_id"] == "27024" for r in found["items"])
    usage = client.get("/v1/usage", headers=DEMO).json()
    assert usage["claims_adjudicated"] == 20 and usage["open_programs"] == 1
    first = seeded["demo"]["first_claim_id"]
    portal = client.get(f"/portal/dev.{seed.DEMO_FIRM_ID}.{first}").json()
    assert portal["program"]["models"] == ["TL1302Z"] and "label" in portal["program"]["required_photos"]
    assert client.get(f"/portal/dev.{seed.DEMO_FIRM_ID}.{uuid4()}").status_code == 404
    assert client.get(f"/portal/dev.{seed.OTHER_FIRM_ID}.{first}").status_code == 404  # wrong firm in the token
    assert client.get(f"/portal/dev.{first}").status_code == 404  # the M1 format is gone
    assert client.get("/portal/not-a-token").status_code == 404
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").status_code in (200, 503)  # 503 until a worker heartbeat exists


def test_committed_openapi_matches_the_app() -> None:
    committed = (REPO_ROOT / "docs" / "contracts" / "openapi.json").read_text(encoding="utf-8")
    assert json.loads(committed) == json.loads(render()), "run `make openapi` and log the contract change"


def test_dev_provider_refuses_live_keys_and_prod() -> None:
    with pytest.raises(UnsafeDevAuth):
        DevAuthProvider(Settings(stripe_secret_key="sk_live_123"), environ={})
    with pytest.raises(UnsafeDevAuth):
        DevAuthProvider(Settings(), environ={"CLERK_SECRET_KEY": "sk_live_abc"})
    with pytest.raises(UnsafeDevAuth):
        DevAuthProvider(Settings(app_env="prod"), environ={})
    provider = DevAuthProvider(Settings(), environ={})
    principal = provider.authenticate({"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "reviewer"})
    assert provider.firm_context(principal) == FirmContext(seed.DEMO_FIRM_ID, "dev-user", "reviewer")
    with pytest.raises(AuthError):
        DevClaimTokenVerifier().verify("dev.not-a-uuid")


def test_payment_fakes_allow_and_log() -> None:
    ctx = FirmContext(UUID(int=1), "u", "admin")
    ledger = FakeLedger()
    ref = ledger.create_pending(ctx, UUID(int=2), 4999)
    assert ledger.mark_onboarding(ctx, ref.payout_id, "acct_test").status == "onboarding"
    assert ledger.mark_transferred(ctx, ref.payout_id, "tr_test").status == "transferred"
    assert ledger.mark_failed(ctx, ref.payout_id, "x").status == "failed"
    assert ledger.mark_exported(ctx, ref.payout_id, "batch-1").status == "exported"
    billing = AllowAllBilling()
    assert billing.check_entitlement(ctx, "adjudicate_claim").allowed
    billing.record_usage(ctx, 3, Decimal("0"))
    assert billing.usage == [(str(ctx.firm_id), 3, Decimal("0"))]
