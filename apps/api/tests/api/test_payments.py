"""HTTP payment contracts without a database or any Stripe network access."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from recallops.api.app import create_app
from recallops.auth.deps import current_firm
from recallops.billing import service
from recallops.billing.client import PaymentError
from recallops.tenancy import FirmContext


@pytest.fixture
def payments_client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[current_firm] = lambda: FirmContext(uuid4(), "synthetic-admin", "admin")
    with TestClient(app) as client:
        yield client


def test_checkout_and_portal_keep_bodyless_url_contract(
    payments_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "checkout", lambda *_: "https://checkout.stripe.com/synthetic")
    monkeypatch.setattr(service, "portal", lambda *_: "https://billing.stripe.com/synthetic")
    assert payments_client.post("/v1/billing/checkout").json() == {"url": "https://checkout.stripe.com/synthetic"}
    assert payments_client.post("/v1/billing/portal").json() == {"url": "https://billing.stripe.com/synthetic"}


def test_payment_boundary_failure_is_a_sanitized_problem(
    payments_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_: object) -> str:
        raise PaymentError("test prices are not configured")

    monkeypatch.setattr(service, "checkout", unavailable)
    response = payments_client.post("/v1/billing/checkout")
    assert response.status_code == 503 and response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == "test prices are not configured"
    assert response.json()["correlation_id"] == response.headers["x-request-id"]


def test_reviewer_cannot_open_checkout() -> None:
    app = create_app()
    app.dependency_overrides[current_firm] = lambda: FirmContext(uuid4(), "synthetic-reviewer", "reviewer")
    with TestClient(app) as client:
        assert client.post("/v1/billing/checkout").status_code == 403
        assert client.post("/v1/billing/portal").status_code == 403


def test_webhook_is_public_but_requires_a_signature(payments_client: TestClient) -> None:
    response = payments_client.post("/internal/stripe/webhook", content=b"{}")
    assert response.status_code == 400 and "signature" in response.json()["detail"]


def test_committed_openapi_matches_the_app() -> None:
    import json

    from recallops.api.export_openapi import render
    from recallops.settings import REPO_ROOT

    committed = json.loads((REPO_ROOT / "docs/contracts/openapi.json").read_text(encoding="utf-8"))
    assert committed == json.loads(render())
