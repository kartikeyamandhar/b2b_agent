"""Provision test products/prices and a restricted Customer Portal; funding requires --fund-key."""

import argparse
import json
from typing import Any

import stripe

from recallops.billing.client import client, data, test_object
from recallops.settings import get_settings


def bootstrap(sdk: Any) -> dict[str, str]:
    settings = get_settings()
    try:
        product = sdk.products.retrieve("recallops-growth")
    except stripe.InvalidRequestError as exc:
        if exc.code != "resource_missing":
            raise
        product = sdk.products.create(
            {"id": "recallops-growth", "name": "RecallOps Growth", "metadata": {"recallops": "growth-v1"}},
            options={"idempotency_key": "recallops-product-growth-v1"},
        )
    test_object(product)
    if data(product).get("metadata", {}).get("recallops") != "growth-v1":
        raise ValueError("the existing product ID is not owned by this bootstrap")
    meters = [
        m
        for m in sdk.billing.meters.list({"limit": 100}).auto_paging_iter()
        if m.event_name == settings.stripe_meter_event_name and m.status == "active"
    ]
    meter = (
        meters[0]
        if meters
        else sdk.billing.meters.create(
            {
                "display_name": "RecallOps adjudicated claims",
                "event_name": settings.stripe_meter_event_name,
                "default_aggregation": {"formula": "sum"},
                "customer_mapping": {"type": "by_id", "event_payload_key": "stripe_customer_id"},
                "value_settings": {"event_payload_key": "value"},
            },
            options={"idempotency_key": "recallops-claims-meter-v1"},
        )
    )
    test_object(meter)
    meter_data = data(meter)
    if (
        meter_data.get("default_aggregation", {}).get("formula") != "sum"
        or meter_data.get("customer_mapping", {}).get("event_payload_key") != "stripe_customer_id"
        or meter_data.get("value_settings", {}).get("event_payload_key") != "value"
    ):
        raise ValueError("existing meter does not sum the configured claim/customer payload")
    result: dict[str, str] = {}
    prices: dict[str, dict[str, Any]] = {
        "STRIPE_PROGRAM_PRICE_ID": {
            "unit_amount": settings.growth_program_price_cents,
            "recurring": {"interval": "month"},
            "lookup_key": f"recallops-program-{settings.growth_program_price_cents}",
        },
        "STRIPE_CLAIM_PRICE_ID": {
            "billing_scheme": "tiered",
            "tiers_mode": "graduated",
            "tiers": [
                {"up_to": settings.growth_included_claims, "unit_amount": 0},
                {"up_to": "inf", "unit_amount": settings.growth_claim_price_cents},
            ],
            "recurring": {"interval": "month", "usage_type": "metered", "meter": meter.id},
            "lookup_key": f"recallops-claims-{settings.growth_included_claims}-{settings.growth_claim_price_cents}",
        },
    }
    for name, params in prices.items():
        existing = list(sdk.prices.list({"lookup_keys": [params["lookup_key"]]}).auto_paging_iter())
        price = (
            existing[0]
            if existing
            else sdk.prices.create(
                {"product": product.id, "currency": "usd", **params},
                options={"idempotency_key": str(params["lookup_key"])},
            )
        )
        test_object(price)
        if str(price.product) != str(product.id) or price.currency != "usd" or not price.active:
            raise ValueError("existing test price does not match the expected product/currency")
        checked = data(sdk.prices.retrieve(str(price.id), {"expand": ["tiers"]}))
        if checked.get("recurring", {}).get("interval") != "month":
            raise ValueError("test price must recur monthly")
        if name == "STRIPE_PROGRAM_PRICE_ID":
            if checked.get("unit_amount") != settings.growth_program_price_cents:
                raise ValueError("program price amount differs from Settings")
        else:
            tiers = checked.get("tiers") or []
            if (
                checked.get("tiers_mode") != "graduated"
                or checked.get("recurring", {}).get("meter") != meter.id
                or len(tiers) != 2
                or tiers[0].get("up_to") != settings.growth_included_claims
                or tiers[0].get("unit_amount") != 0
                or tiers[1].get("up_to") is not None
                or tiers[1].get("unit_amount") != settings.growth_claim_price_cents
            ):
                raise ValueError("claim price tiers or meter differ from Settings")
        result[name] = str(price.id)
    configs = [
        c
        for c in sdk.billing_portal.configurations.list({"limit": 100}).auto_paging_iter()
        if data(c).get("metadata", {}).get("recallops") == "v1" and c.active
    ]
    config = (
        configs[0]
        if configs
        else sdk.billing_portal.configurations.create(
            {
                "metadata": {"recallops": "v1"},
                "business_profile": {"headline": "RecallOps test billing"},
                "features": {
                    "payment_method_update": {"enabled": True},
                    "invoice_history": {"enabled": True},
                    "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
                    "subscription_update": {"enabled": False},
                },
            },
            options={"idempotency_key": "recallops-portal-v1"},
        )
    )
    features = data(config).get("features", {})
    if (
        features.get("subscription_update", {}).get("enabled")
        or not features.get("payment_method_update", {}).get("enabled")
        or not features.get("subscription_cancel", {}).get("enabled")
        or features.get("subscription_cancel", {}).get("mode") != "at_period_end"
    ):
        raise ValueError("existing Customer Portal configuration permits incompatible billing changes")
    result["STRIPE_PORTAL_CONFIGURATION_ID"] = str(config.id)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fund-key", help="explicit unique test-funding operation identity; reruns reuse it")
    args = parser.parse_args()
    settings = get_settings()
    sdk = client(settings)
    result = bootstrap(sdk)
    if args.fund_key:
        payment = sdk.payment_intents.create(
            {
                "amount": 100000,
                "currency": "usd",
                "confirm": True,
                "payment_method": "pm_card_bypassPending",
                "return_url": settings.billing_return_url,
            },
            options={"idempotency_key": f"test-funding:{args.fund_key}"},
        )
        test_object(payment)
        result["test_funding_payment_intent"] = str(payment.id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
