"""Explicitly opted-in provider checks; no resource creation or money movement."""

import os

import pytest
from dotenv import dotenv_values

from recallops.billing.client import client, connect_client, data
from recallops.payouts.stripe_connect import ready
from recallops.settings import REPO_ROOT, Settings

pytestmark = [
    pytest.mark.stripe_test,
    pytest.mark.skipif(os.environ.get("RUN_STRIPE_TESTS") != "1", reason="requires explicit Stripe TEST approval"),
]


def test_configured_test_prices_and_portal_restrictions_match_the_application() -> None:
    values = dotenv_values(REPO_ROOT / ".env")
    settings = Settings(stripe_secret_key=values.get("STRIPE_SECRET_KEY") or "sk_test_fake")
    sdk = client(settings)
    assert data(sdk.balance.retrieve())["livemode"] is False
    program = sdk.prices.retrieve(values["STRIPE_PROGRAM_PRICE_ID"])
    claims = sdk.prices.retrieve(values["STRIPE_CLAIM_PRICE_ID"], {"expand": ["tiers"]})
    assert not program.livemode and program.active and program.unit_amount == settings.growth_program_price_cents
    assert not claims.livemode and claims.active and claims.tiers_mode == "graduated"
    assert claims.tiers[0].up_to == settings.growth_included_claims and claims.tiers[0].unit_amount == 0
    assert claims.tiers[1].unit_amount == settings.growth_claim_price_cents
    portal = sdk.billing_portal.configurations.retrieve(values["STRIPE_PORTAL_CONFIGURATION_ID"])
    assert not portal.livemode and portal.features.payment_method_update.enabled
    assert not portal.features.subscription_update.enabled
    assert portal.features.subscription_cancel.enabled and portal.features.subscription_cancel.mode == "at_period_end"


def test_accounts_v2_recipient_is_ready_in_test_mode() -> None:
    account_id = os.environ.get("STRIPE_TEST_ACCOUNT_ID")
    if not account_id:
        pytest.skip("STRIPE_TEST_ACCOUNT_ID must identify a completed synthetic test account")
    settings = Settings(stripe_secret_key=dotenv_values(REPO_ROOT / ".env").get("STRIPE_SECRET_KEY") or "sk_test_fake")
    account = connect_client(settings).accounts.retrieve(account_id, {"include": ["configuration.recipient"]})
    assert data(account)["livemode"] is False and account.object == "v2.core.account"
    assert ready(account)


def test_verified_1001_claim_fixture_has_one_25_cent_overage() -> None:
    customer = os.environ.get("STRIPE_TEST_METER_CUSTOMER")
    subscription = os.environ.get("STRIPE_TEST_METER_SUBSCRIPTION")
    if not customer or not subscription:
        pytest.skip("set the customer/subscription of the persisted 1,001 eligible-claim fixture")
    values = dotenv_values(REPO_ROOT / ".env")
    sdk = client(Settings(stripe_secret_key=values.get("STRIPE_SECRET_KEY") or "sk_test_fake"))
    invoice = sdk.invoices.create_preview({"customer": customer, "subscription": subscription})
    assert not invoice.livemode
    lines = [
        line
        for line in invoice.lines.auto_paging_iter()
        if data(line).get("pricing", {}).get("price_details", {}).get("price") == values["STRIPE_CLAIM_PRICE_ID"]
    ]
    assert sum(line.quantity for line in lines) == 1001
    assert sum(line.amount for line in lines) == 25
