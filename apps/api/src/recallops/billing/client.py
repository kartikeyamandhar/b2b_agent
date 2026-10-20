"""The configured Stripe test-mode SDK boundary. No global SDK configuration is mutated."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import stripe
from sqlalchemy import Engine, text

from recallops.settings import Settings


class PaymentError(Exception):
    def __init__(self, detail: str, status: int = 503) -> None:
        super().__init__(detail)
        self.detail, self.status = detail, status


def _stripe_client(settings: Settings) -> stripe.StripeClient:
    key = settings.stripe_secret_key.get_secret_value()
    if key == "sk_test_fake" or not key.startswith("sk_test_"):
        raise PaymentError("Stripe test credentials are not configured")
    return stripe.StripeClient(
        key,
        stripe_version=settings.stripe_api_version,
        max_network_retries=0,
        http_client=stripe.HTTPXClient(timeout=settings.stripe_timeout_s, allow_sync_methods=True),
    )


def client(settings: Settings) -> Any:
    return _stripe_client(settings).v1


def connect_client(settings: Settings) -> Any:
    return _stripe_client(settings).v2.core


def test_object(obj: Any) -> None:
    if data(obj).get("livemode") is not False:
        raise PaymentError("only Stripe test-mode objects are accepted", 400)


@contextmanager
def firm_lock(engine: Engine, firm_id: UUID) -> Iterator[None]:
    # A session advisory lock serializes provider reads/writes without holding tenant row locks over the network.
    with engine.connect() as conn:
        acquired = conn.scalar(text("SELECT pg_try_advisory_lock(7420020, hashtext(:id))"), {"id": str(firm_id)})
        conn.commit()
        if not acquired:
            raise PaymentError("another payment operation is processing this firm; retry shortly")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(7420020, hashtext(:id))"), {"id": str(firm_id)})
            conn.commit()


def data(obj: Any) -> dict[str, Any]:
    if isinstance(obj, stripe.StripeObject):
        return dict(obj.to_dict())
    if isinstance(obj, dict):
        return obj
    raise PaymentError("unexpected Stripe object shape", 400)


def object_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return str(value["id"]) if value and data(value).get("id") else None
