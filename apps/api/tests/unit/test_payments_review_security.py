"""Security regressions for the backend review of PR 6: webhook authentication order, secrets, and Stripe errors."""

import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
import stripe
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from recallops.auth.dev import live_keys_present
from recallops.billing import webhooks
from recallops.billing.client import PaymentError
from recallops.settings import Settings

SNAPSHOT_SECRET, THIN_SECRET = "whsec_snapshot_synthetic", "whsec_thin_synthetic"


def _sign(payload: bytes, secret: str) -> str:
    now = str(int(time.time()))
    digest = hmac.new(secret.encode(), now.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={now},v1={digest}"


def _settings() -> Settings:
    return Settings(stripe_webhook_secret=SNAPSHOT_SECRET, stripe_connect_webhook_secret=THIN_SECRET)


def test_an_unsigned_deeply_nested_body_is_a_quiet_400_before_any_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """json.loads used to run first: 200,000 '[' raised RecursionError and answered 500."""
    db = MagicMock()
    monkeypatch.setattr(webhooks, "Session", db)
    payload = b"[" * 200_000
    with pytest.raises(PaymentError) as caught:
        webhooks.receive(MagicMock(), _settings(), payload, "t=1,v1=00")
    assert caught.value.status == 400
    db.assert_not_called()


def test_a_snapshot_event_signed_with_the_thin_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhooks, "Session", MagicMock())
    event = {"id": "evt_s", "type": "invoice.paid", "livemode": False, "data": {"object": {"id": "in_1"}}}
    payload = json.dumps(event).encode()
    with pytest.raises(PaymentError) as caught:
        webhooks.receive(MagicMock(), _settings(), payload, _sign(payload, THIN_SECRET))
    assert caught.value.status == 400


def test_a_signed_thin_event_of_an_unhandled_type_is_recorded_and_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destination ping has no api_version and no data: it used to be refused on every delivery."""
    session = MagicMock()
    session.__enter__.return_value = session
    monkeypatch.setattr(webhooks, "Session", lambda _: session)
    record, processed = MagicMock(return_value=True), MagicMock()
    monkeypatch.setattr(webhooks, "record", record)
    monkeypatch.setattr(webhooks, "processed", processed)
    event = {"id": "evt_ping", "object": "v2.core.event", "type": "v2.core.event_destination.ping", "livemode": False}
    payload = json.dumps(event).encode()
    assert webhooks.receive(MagicMock(), _settings(), payload, _sign(payload, THIN_SECRET)) == {"status": "ignored"}
    record.assert_called_once()
    processed.assert_called_once()


def test_stripe_secrets_are_masked_and_still_scanned_for_live_keys() -> None:
    settings = _settings()
    assert SNAPSHOT_SECRET not in repr(settings) and THIN_SECRET not in repr(settings)
    live: Any = Settings.model_construct(stripe_secret_key=SecretStr("sk_live_should_never_load"))
    assert "stripe_secret_key" in live_keys_present(live, environ={})


def _prod(**values: Any) -> Settings:
    return Settings.model_validate({"app_env": "prod", "neo4j_uri": "", **values})


def test_prod_payments_need_deployed_return_urls_and_prices() -> None:
    with pytest.raises(ValidationError, match="deployed UI"):
        _prod(stripe_secret_key="sk_test_configured")
    deployed = {
        "billing_return_url": "https://recallops.example/billing",
        "connect_return_url": "https://recallops.example/connect/return",
        "connect_refresh_url": "https://recallops.example/connect/refresh",
    }
    with pytest.raises(ValidationError, match="PRICE_ID"):
        _prod(stripe_secret_key="sk_test_configured", **deployed)
    ok = _prod(
        stripe_secret_key="sk_test_configured",
        stripe_program_price_id="price_program",
        stripe_claim_price_id="price_claim",
        **deployed,
    )
    assert ok.app_env == "prod"
    assert _prod().stripe_program_price_id == ""  # payments off: nothing required


@pytest.mark.parametrize(
    ("make", "detail"),
    [
        (lambda: stripe.AuthenticationError("bad key"), "operator attention"),
        (lambda: stripe.APIConnectionError("reset"), "retry"),  # type: ignore[no-untyped-call]
    ],
)
def test_permanent_stripe_errors_are_not_reported_as_retryable(make: Any, detail: str) -> None:
    from recallops.api.app import create_app

    app = create_app()

    @app.get("/stripe-boom")
    def boom() -> None:
        raise make()

    response = TestClient(app, raise_server_exceptions=False).get("/stripe-boom")
    body = response.json()
    assert response.status_code == 503 and detail in body["detail"]
    assert "bad key" not in response.text and "reset" not in response.text  # Stripe's message never reaches a client
