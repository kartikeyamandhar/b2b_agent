"""Resumable sandbox acceptance: run this module's CLI, then explicitly opt into its pytest checks.

The flags record a separately obtained human authorization; they do not grant it. All artifacts are local
and ignored. No provider cleanup happens implicitly, so interrupted tests can reconcile the same operations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
import stripe
import uvicorn
from dotenv import dotenv_values
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import make_url
from tests.db.test_ledger_retrieval_worker import _firm_program_claim

from recallops.billing.client import PaymentError, client, connect_client, data, firm_lock
from recallops.billing.client import test_object as require_test_object
from recallops.billing.jobs import sync_quantity
from recallops.billing.models import BillingAccount
from recallops.billing.policy import effective_plan
from recallops.billing.service import reconcile_subscription
from recallops.claims.worker import Worker
from recallops.db.engine import build_engine
from recallops.db.session import tenant_session
from recallops.models import Firm, Payout
from recallops.payouts.db_ledger import DbPayoutLedger
from recallops.payouts.stripe_connect import process_payout, ready
from recallops.settings import REPO_ROOT, Settings, get_settings
from recallops.tenancy import FirmContext

DATABASE = "recallops_payments_acceptance"
BASE_URL = "http://127.0.0.1:18000"
ROOT = REPO_ROOT / ".tools" / "payments-acceptance"
pytestmark = [
    pytest.mark.stripe_test,
    pytest.mark.skipif(
        os.environ.get("RUN_STRIPE_ACCEPTANCE") != "1", reason="requires explicit sandbox write approval"
    ),
]


class AcceptanceState(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    firm_id: UUID
    program_id: UUID
    queued_claim_id: UUID
    other_firm_id: UUID
    other_claim_id: UUID
    payout_ids: list[UUID] = Field(default_factory=list)
    payout_claim_ids: list[UUID] = Field(default_factory=list)
    funding_started_at: datetime | None = None
    funding_intent: str | None = None
    reversal_id: str | None = None
    subscription_id: str | None = None
    customer_id: str | None = None
    checkout_session_id: str | None = None
    clock_id: str | None = None
    clock_customer: str | None = None
    clock_subscription: str | None = None
    clock_firm_id: UUID | None = None
    clock_invoice: str | None = None
    quantity_program_id: UUID | None = None


def local_settings(directory: Path | None = None) -> Settings:
    values = dotenv_values(REPO_ROOT / ".env")
    secrets = dotenv_values(directory / "endpoints.env") if directory and (directory / "endpoints.env").exists() else {}
    urls = {}
    for name, default in (
        ("database_url", "postgresql://recallops_app:app-dev-only@127.0.0.1:55432/recallops"),
        ("database_url_internal", "postgresql://recallops_internal:internal-dev-only@127.0.0.1:55432/recallops"),
        ("database_url_owner", "postgresql://recallops_owner:owner-dev-only@127.0.0.1:55432/recallops"),
    ):
        url = make_url(values.get(name.upper()) or default)
        if url.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("acceptance databases must be local")
        urls[name] = url.set(database=DATABASE).render_as_string(hide_password=False)
    return Settings(
        database_url=urls["database_url"],
        database_url_internal=urls["database_url_internal"],
        database_url_owner=urls["database_url_owner"],
        app_env="test",
        auth_provider="dev",
        claim_token_provider="dev",
        llm_mode="fake",
        adjudicator="rules_only",
        stripe_secret_key=values.get("STRIPE_SECRET_KEY") or "sk_test_fake",
        stripe_program_price_id=values.get("STRIPE_PROGRAM_PRICE_ID") or "",
        stripe_claim_price_id=values.get("STRIPE_CLAIM_PRICE_ID") or "",
        stripe_portal_configuration_id=values.get("STRIPE_PORTAL_CONFIGURATION_ID") or "",
        stripe_webhook_secret=secrets.get("STRIPE_WEBHOOK_SECRET") or "whsec_fake",
        stripe_connect_webhook_secret=secrets.get("STRIPE_CONNECT_WEBHOOK_SECRET") or "whsec_fake",
        billing_return_url=BASE_URL + "/dev/console#payouts",
        connect_return_url=BASE_URL + "/dev/console#connect-return",
        connect_refresh_url=BASE_URL + "/dev/console#connect-refresh",
        storage_dir=ROOT / "storage",
        neo4j_namespace="test_payments_acceptance",
        neo4j_uri="bolt://127.0.0.1:57687",
        worker_poll_s=2,
    )


def configure_environment(settings: Settings) -> None:
    for name in (
        "database_url",
        "database_url_internal",
        "database_url_owner",
        "stripe_secret_key",
        "stripe_program_price_id",
        "stripe_claim_price_id",
        "stripe_portal_configuration_id",
        "stripe_webhook_secret",
        "stripe_connect_webhook_secret",
        "app_env",
        "auth_provider",
        "claim_token_provider",
        "llm_mode",
        "adjudicator",
        "billing_return_url",
        "connect_return_url",
        "connect_refresh_url",
        "storage_dir",
        "neo4j_namespace",
        "neo4j_uri",
        "worker_poll_s",
    ):
        os.environ[name.upper()] = str(getattr(settings, name))
    get_settings.cache_clear()


class Acceptance:
    def __init__(self, run_id: str) -> None:
        self.directory = ROOT / str(UUID(run_id))
        self.state = AcceptanceState.model_validate_json((self.directory / "run.json").read_text(encoding="utf-8"))
        self.settings = local_settings(self.directory)
        self.app = build_engine(self.settings.database_url)
        self.internal = build_engine(self.settings.database_url_internal)
        self.ctx = FirmContext(self.state.firm_id, "payments-acceptance", "admin")
        self.sdk = client(self.settings)
        self.core = connect_client(self.settings)

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "run.json").write_text(self.state.model_dump_json(indent=2), encoding="utf-8")

    def observe(self, event: str, **fields: Any) -> None:
        row = {"at": datetime.now(UTC).isoformat(), "event": event, "run_id": str(self.state.run_id), **fields}
        line = json.dumps(row, default=str)
        with (self.directory / "evidence.jsonl").open("a", encoding="utf-8") as out:
            out.write(line + "\n")
        print(line, flush=True)

    def request(
        self, method: str, path: str, body: object | None = None, firm_id: UUID | None = None
    ) -> httpx.Response:
        response = httpx.request(
            method,
            BASE_URL + path,
            json=body,
            timeout=30,
            headers={
                "X-Dev-Firm-Id": str(firm_id or self.ctx.firm_id),
                "X-Dev-Role": "admin",
                "X-Dev-User-Id": "payments-acceptance",
            },
        )
        response.raise_for_status()
        return response

    def check_subscription(self) -> None:
        with tenant_session(self.ctx, self.app) as session:
            account = session.get(BillingAccount, self.ctx.firm_id)
            firm = session.get(Firm, self.ctx.firm_id)
            assert firm and account and account.subscription_id, (
                "complete hosted Checkout and receive its real webhook first"
            )
            assert effective_plan(session, firm) == "growth"
            assert session.scalar(
                text("SELECT count(*) FROM billing_invoices WHERE firm_id=:f AND subscription_id=:s AND status='paid'"),
                {"f": self.ctx.firm_id, "s": account.subscription_id},
            )
            self.state.subscription_id, self.state.customer_id = account.subscription_id, firm.stripe_customer_id
        self.save()
        with self.internal.connect() as conn:
            result = conn.execute(
                text("SELECT status FROM claims WHERE id=:c"), {"c": self.state.queued_claim_id}
            ).scalar_one()
            event = conn.execute(
                text(
                    "SELECT id,received_at,processed_at FROM stripe_events WHERE type IN ('checkout.session.completed','invoice.paid') "
                    "AND payload->'data'->'object'->>'customer'=:customer AND processed_at IS NOT NULL ORDER BY received_at DESC LIMIT 1"
                ),
                {"customer": self.state.customer_id},
            ).one()
        assert result not in {"submitted", "draft"}, "worker has not resumed the blocked claim"
        self.observe(
            "checkout_webhook_and_worker_resumption",
            stripe_event=event.id,
            received=event.received_at,
            processed=event.processed_at,
            claim_status=result,
        )

    def replay(self) -> None:
        with self.internal.connect() as conn:
            events = conn.execute(
                text(
                    "SELECT id,type,payload FROM stripe_events WHERE processed_at IS NOT NULL "
                    "AND (payload->'data'->'object'->>'customer'=:customer OR payload->'related_object'->>'id' IN "
                    "(SELECT connect_account_id FROM payouts WHERE firm_id=:firm) OR payload->'data'->'object'->>'destination' IN "
                    "(SELECT connect_account_id FROM payouts WHERE firm_id=:firm))"
                ),
                {"customer": self.state.customer_id, "firm": self.ctx.firm_id},
            ).all()
        assert events, "no genuinely forwarded events are available"
        for row in events:
            raw = json.dumps(row.payload).encode()
            secret = (
                self.settings.stripe_connect_webhook_secret
                if row.payload.get("object") == "v2.core.event"
                else self.settings.stripe_webhook_secret
            )
            signature = stripe.WebhookSignature.generate_signature_header(raw.decode(), secret)
            with self.internal.connect() as conn:
                before = conn.scalar(text("SELECT count(*) FROM audit_log WHERE firm_id=:f"), {"f": self.ctx.firm_id})
            response = httpx.post(
                BASE_URL + "/internal/stripe/webhook", content=raw, headers={"Stripe-Signature": signature}, timeout=30
            )
            assert response.status_code == 200 and response.json()["status"] == "replayed"
            with self.internal.connect() as conn:
                assert (
                    conn.scalar(text("SELECT count(*) FROM audit_log WHERE firm_id=:f"), {"f": self.ctx.firm_id})
                    == before
                )
            self.observe(
                "forwarded_event_replay",
                stripe_event=row.id,
                event_type=row.type,
                replay_signature="locally regenerated",
            )

    def prepare_payouts(self) -> None:
        assert self.state.subscription_id, "verify the paid subscription first"
        while len(self.state.payout_ids) < 3:
            claim_id = uuid5(self.state.run_id, f"payout-{len(self.state.payout_ids)}")
            with self.internal.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO claims(id,firm_id,program_id,token_hash,status,refund_cents,remedy,claimant) "
                        "VALUES (:c,:f,:p,:t,'approved',4999,'refund',jsonb_build_object('name','Avery Test','email','avery@example.com')) ON CONFLICT DO NOTHING"
                    ),
                    {"c": claim_id, "f": self.ctx.firm_id, "p": self.state.program_id, "t": str(claim_id)},
                )
            payout = DbPayoutLedger(self.app).create_pending(self.ctx, claim_id, 4999)
            self.state.payout_ids.append(payout.payout_id)
            self.state.payout_claim_ids.append(claim_id)
            self.save()
        for index, claim_id in enumerate(self.state.payout_claim_ids):
            token = f"dev.{self.ctx.firm_id}.{claim_id}"
            with tenant_session(self.ctx, self.app) as session:
                session.execute(
                    text("UPDATE claims SET token_hash=:hash WHERE id=:id"),
                    {"hash": hashlib.sha256(token.encode()).hexdigest(), "id": claim_id},
                )
            response = self.request("POST", f"/portal/{token}/payout", {"method": "stripe_connect"})
            url = response.json()["connect_onboarding_url"]
            # A private, ignored handoff file is intentional; bearer URLs never enter evidence or committed docs.
            (self.directory / f"onboarding-{index}.url").write_text(url, encoding="utf-8")
            self.observe("onboarding_link_created", payout_id=self.state.payout_ids[index], index=index)

    def fund_once(self) -> None:
        if self.state.funding_intent:
            return
        balance = self.sdk.balance.retrieve()
        require_test_object(balance)
        if sum(x.amount for x in balance.available if x.currency == "usd") >= 14997:
            return
        if self.state.funding_started_at and datetime.now(UTC) - self.state.funding_started_at >= timedelta(hours=23):
            raise PaymentError("old funding outcome must be reconciled manually")
        self.state.funding_started_at = self.state.funding_started_at or datetime.now(UTC)
        self.save()
        payment = self.sdk.payment_intents.create(
            {
                "amount": 100000,
                "currency": "usd",
                "confirm": True,
                "payment_method": "pm_card_bypassPending",
                "return_url": BASE_URL + "/dev/console",
                "metadata": {"recallops_acceptance_run": str(self.state.run_id)},
            },
            options={"idempotency_key": f"acceptance-funding-dynamic:{self.state.run_id}"},
        )
        require_test_object(payment)
        assert payment.status == "succeeded"
        self.state.funding_intent = str(payment.id)
        self.save()
        self.observe("simulated_funding", payment_intent=payment.id, cents=100000)

    def quantity(self) -> None:
        self.check_subscription()
        if self.state.quantity_program_id is None:
            result = self.request(
                "POST",
                "/v1/programs",
                {
                    "manual": {
                        "title": f"Acceptance {self.state.run_id}",
                        "description": "Synthetic program for quantity verification.",
                    }
                },
            ).json()
            self.state.quantity_program_id = UUID(result["id"])
            self.save()
        for status, expected in (("open", 2), ("closed", 1)):
            self.request("PATCH", f"/v1/programs/{self.state.quantity_program_id}", {"status": status})
            for attempt in range(15):
                try:
                    with firm_lock(self.app, self.ctx.firm_id):
                        sync_quantity(self.app, self.settings, self.ctx, self.sdk)
                    break
                except PaymentError:
                    if attempt == 14:
                        raise
                    time.sleep(1)
            subscription = self.sdk.subscriptions.retrieve(self.state.subscription_id)
            item = next(x for x in subscription["items"]["data"] if x.price.id == self.settings.stripe_program_price_id)
            assert item.quantity == expected
            self.observe(
                "program_quantity_converged", status=status, quantity=item.quantity, subscription=subscription.id
            )
        preview = self.sdk.invoices.create_preview(
            {"customer": self.state.customer_id, "subscription": self.state.subscription_id}
        )
        prorations = [
            data(line)
            for line in preview.lines.auto_paging_iter()
            if (data(line).get("parent") or {}).get("subscription_item_details", {}).get("proration")
        ]
        assert prorations, "provider invoice preview did not record program quantity prorations"
        self.observe(
            "program_proration_preview",
            line_count=len(prorations),
            net_cents=sum(line["amount"] for line in prorations),
        )

    def clock_setup(self) -> None:
        assert datetime.now(UTC) - self.state.created_at < timedelta(hours=23), "expired acceptance write window"
        if self.state.clock_firm_id is None:
            ctx, _, _ = _firm_program_claim(self.internal)
            self.state.clock_firm_id = ctx.firm_id
            self.save()
        ctx = FirmContext(self.state.clock_firm_id, "payments-acceptance", "admin")
        if not self.state.clock_id:
            clock = self.sdk.test_helpers.test_clocks.create(
                {"frozen_time": int(datetime.now(UTC).timestamp()), "name": f"RecallOps {self.state.run_id}"},
                options={"idempotency_key": f"acceptance-clock:{self.state.run_id}"},
            )
            self.state.clock_id = clock.id
            self.save()
        if not self.state.clock_customer:
            customer = self.sdk.customers.create(
                {
                    "test_clock": self.state.clock_id,
                    "name": "Synthetic clock firm",
                    "email": "clock@example.com",
                    "metadata": {"recallops_acceptance_run": str(self.state.run_id), "firm_id": str(ctx.firm_id)},
                },
                options={"idempotency_key": f"acceptance-clock-customer:{self.state.run_id}"},
            )
            require_test_object(customer)
            self.state.clock_customer = customer.id
            self.save()
        with tenant_session(ctx, self.app) as session:
            firm = session.get(Firm, ctx.firm_id)
            assert firm
            firm.stripe_customer_id = self.state.clock_customer
            account = session.get(BillingAccount, ctx.firm_id)
            if account is None:
                account = BillingAccount(
                    firm_id=ctx.firm_id, checkout_attempt=uuid5(self.state.run_id, "clock-checkout")
                )
                session.add(account)
            attempt = str(account.checkout_attempt)
        if not self.state.clock_subscription:
            pm = self.sdk.payment_methods.attach(
                "pm_card_visa",
                {"customer": self.state.clock_customer},
                options={"idempotency_key": f"acceptance-clock-card:{self.state.run_id}"},
            )
            sub = self.sdk.subscriptions.create(
                {
                    "customer": self.state.clock_customer,
                    "default_payment_method": pm.id,
                    "items": [
                        {"price": self.settings.stripe_program_price_id, "quantity": 1},
                        {"price": self.settings.stripe_claim_price_id},
                    ],
                    "metadata": {
                        "firm_id": str(ctx.firm_id),
                        "checkout_attempt": attempt,
                        "recallops_acceptance_run": str(self.state.run_id),
                    },
                },
                options={"idempotency_key": f"acceptance-clock-subscription:{self.state.run_id}"},
            )
            require_test_object(sub)
            self.state.clock_subscription = sub.id
            self.save()
        self.reconcile_clock()
        self.observe(
            "test_clock_subscription_ready", clock=self.state.clock_id, subscription=self.state.clock_subscription
        )

    def reconcile_clock(self) -> str:
        assert self.state.clock_firm_id and self.state.clock_subscription
        ctx = FirmContext(self.state.clock_firm_id, "payments-acceptance", "admin")
        for attempt in range(20):
            try:
                with firm_lock(self.app, ctx.firm_id), tenant_session(ctx, self.app) as session:
                    reconcile_subscription(session, ctx, self.sdk, self.settings, self.state.clock_subscription)
                    session.flush()
                    firm = session.get(Firm, ctx.firm_id)
                    assert firm
                    return effective_plan(session, firm)
            except PaymentError:
                if attempt == 19:
                    raise
                time.sleep(1)
        raise AssertionError("unreachable")

    def clock_advance(self, timestamp: int) -> None:
        clock = self.sdk.test_helpers.test_clocks.retrieve(self.state.clock_id)
        if clock.frozen_time < timestamp and clock.status == "ready":
            self.sdk.test_helpers.test_clocks.advance(self.state.clock_id, {"frozen_time": timestamp})
        for _ in range(60):
            clock = self.sdk.test_helpers.test_clocks.retrieve(self.state.clock_id)
            if clock.status == "ready":
                return
            time.sleep(1)
        raise AssertionError("Stripe test clock is still advancing; resume after it becomes ready")

    def delinquency(self) -> None:
        self.clock_setup()
        sub = self.sdk.subscriptions.retrieve(self.state.clock_subscription)
        pm = self.sdk.payment_methods.attach(
            "pm_card_chargeCustomerFail",
            {"customer": self.state.clock_customer},
            options={"idempotency_key": f"acceptance-clock-decline:{self.state.run_id}"},
        )
        self.sdk.subscriptions.update(sub.id, {"default_payment_method": pm.id})
        if self.state.clock_invoice is None:
            self.clock_advance(max(x.current_period_end for x in sub["items"]["data"]) + 60)
            current = self.sdk.subscriptions.retrieve(sub.id)
            self.state.clock_invoice = str(current.latest_invoice)
            self.save()
        invoice = self.sdk.invoices.retrieve(self.state.clock_invoice)
        if invoice.status == "draft":
            invoice = self.sdk.invoices.finalize_invoice(invoice.id, {"auto_advance": False})
        if invoice.attempt_count == 0:
            prior = invoice.attempt_count
            with suppress(stripe.CardError):
                self.sdk.invoices.pay(
                    invoice.id, {}, options={"idempotency_key": f"acceptance-clock-failure:{self.state.run_id}:{prior}"}
                )
            invoice = self.sdk.invoices.retrieve(invoice.id)
            assert invoice.attempt_count > prior, "provider did not record another collection attempt"
            expected = "free" if invoice.attempt_count >= 2 else "growth"
            assert self.reconcile_clock() == expected
            self.observe(
                "collection_failure_access",
                invoice=invoice.id,
                attempt_count=invoice.attempt_count,
                effective_plan=expected,
            )
        if invoice.attempt_count < 2:
            invoice = self.sdk.invoices.update(invoice.id, {"auto_advance": True})
            assert self.reconcile_clock() == "growth"
            for _ in range(8):
                clock = self.sdk.test_helpers.test_clocks.retrieve(self.state.clock_id)
                target = invoice.next_payment_attempt or clock.frozen_time + 86400
                self.clock_advance(max(target + 60, clock.frozen_time + 60))
                invoice = self.sdk.invoices.retrieve(invoice.id)
                if invoice.attempt_count >= 2:
                    break
            assert invoice.attempt_count >= 2, "scheduled provider retry has not occurred"
            assert self.reconcile_clock() == "free"
            self.observe(
                "collection_failure_access",
                invoice=invoice.id,
                attempt_count=invoice.attempt_count,
                effective_plan="free",
            )
        good = self.sdk.payment_methods.attach(
            "pm_card_visa",
            {"customer": self.state.clock_customer},
            options={"idempotency_key": f"acceptance-clock-recovery-card:{self.state.run_id}"},
        )
        self.sdk.subscriptions.update(sub.id, {"default_payment_method": good.id})
        self.sdk.invoices.pay(
            invoice.id,
            {"payment_method": good.id},
            options={"idempotency_key": f"acceptance-clock-recovery:{self.state.run_id}"},
        )
        assert self.reconcile_clock() == "growth"
        self.observe("collection_recovered", invoice=invoice.id, effective_plan="growth")

    def reverse_once(self, index: int) -> None:
        assert index in range(3)
        payout_id = self.state.payout_ids[index]
        with tenant_session(self.ctx, self.app) as session:
            row = session.get(Payout, payout_id)
            assert row and row.status == "transferred" and row.amount_cents == 4999
            transfer_id = row.stripe_transfer_id
        transfer = self.sdk.transfers.retrieve(transfer_id)
        require_test_object(transfer)
        if self.state.reversal_id is None:
            assert transfer.amount_reversed == 0, "reconcile an existing reversal before another request"
            assert datetime.now(UTC) - self.state.created_at < timedelta(hours=23), "expired acceptance write window"
            reversal = self.sdk.transfers.reversals.create(
                transfer_id,
                {"amount": 1000, "metadata": {"recallops_acceptance_run": str(self.state.run_id)}},
                options={"idempotency_key": f"acceptance-reversal:{self.state.run_id}"},
            )
            self.state.reversal_id = reversal.id
            self.save()
        for _ in range(30):
            with tenant_session(self.ctx, self.app) as session:
                row = session.get(Payout, payout_id)
                assert row and row.status == "transferred" and not row.export_batch
                if row.stripe_reversed_cents == 1000:
                    self.observe(
                        "partial_reversal_recorded", payout_id=payout_id, cents=1000, reversal=self.state.reversal_id
                    )
                    return
            time.sleep(1)
        raise AssertionError("genuine reversal webhook has not updated the ledger")

    def cancellation(self) -> None:
        assert self.state.clock_subscription
        sub = self.sdk.subscriptions.update(self.state.clock_subscription, {"cancel_at_period_end": True})
        assert self.reconcile_clock() == "growth"
        self.observe("scheduled_cancellation_retains_access", subscription=sub.id, effective_plan="growth")
        self.clock_advance(max(x.current_period_end for x in sub["items"]["data"]) + 60)
        assert self.reconcile_clock() == "free"
        assert self.sdk.subscriptions.retrieve(sub.id).status == "canceled"
        self.observe("effective_cancellation_removes_access", subscription=sub.id, effective_plan="free")

    def export_fallback(self) -> None:
        response = self.request("POST", "/v1/payouts/exports").json() or {}
        batch = response.get("batch") or response
        if batch.get("id"):
            batch_id = batch["id"]
        else:
            with tenant_session(self.ctx, self.app) as session:
                payout = session.get(Payout, self.state.payout_ids[0])
                assert payout and payout.export_batch
                batch_id = str(payout.export_batch)
        body = self.request("GET", f"/v1/payouts/exports/{batch_id}.csv").content
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        assert len(rows) == 1 and rows[0]["payout_id"] == str(self.state.payout_ids[0])
        assert int(rows[0]["amount_cents"]) == 4999
        self.observe(
            "confirmed_failure_export_downloaded",
            batch=batch_id,
            rows=1,
            cents=4999,
            sha256=hashlib.sha256(body).hexdigest(),
        )

    def free_export(self) -> None:
        ctx = FirmContext(self.state.other_firm_id, "payments-acceptance", "admin")
        claim_id = self.state.other_claim_id
        token = f"dev.{ctx.firm_id}.{claim_id}"
        with tenant_session(ctx, self.app) as session:
            firm = session.get(Firm, ctx.firm_id)
            assert firm and effective_plan(session, firm) == "free"
            existing = session.scalar(
                text("SELECT status FROM payouts WHERE firm_id=:f AND claim_id=:c"), {"f": ctx.firm_id, "c": claim_id}
            )
            if existing is None:
                session.execute(
                    text(
                        "UPDATE claims SET status='approved',refund_cents=4999,remedy='refund',token_hash=:t,claimant=jsonb_build_object('name','Check Test','email','check@example.com') WHERE id=:c"
                    ),
                    {"t": hashlib.sha256(token.encode()).hexdigest(), "c": claim_id},
                )
        payout = DbPayoutLedger(self.app).create_pending(ctx, claim_id, 4999, method="check_export")
        if existing is None:
            self.request(
                "POST",
                f"/portal/{token}/payout",
                {"method": "check_export", "mailing_address": "123 Test Street, Los Angeles CA 90095"},
                ctx.firm_id,
            )
        self.request("POST", "/v1/payouts/exports", firm_id=ctx.firm_id)
        with tenant_session(ctx, self.app) as session:
            row = session.get(Payout, payout.payout_id)
            assert (
                row
                and row.status == "exported"
                and row.export_batch
                and not row.stripe_transfer_id
                and not row.connect_account_id
            )
            batch = row.export_batch
        body = self.request("GET", f"/v1/payouts/exports/{batch}.csv", firm_id=ctx.firm_id).content
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        assert len(rows) == 1 and rows[0]["payout_id"] == str(payout.payout_id) and int(rows[0]["amount_cents"]) == 4999
        self.observe(
            "free_plan_check_export_downloaded", payout_id=payout.payout_id, batch=batch, cents=4999, stripe_calls=0
        )

    def payout(self, index: int, crash: str | None = None) -> None:
        if index not in range(3) or len(self.state.payout_ids) != 3:
            raise ValueError("only the three registered synthetic refunds may be processed")
        payout_id = self.state.payout_ids[index]
        with tenant_session(self.ctx, self.app) as session:
            row = session.get(Payout, payout_id)
            assert row and row.firm_id == self.ctx.firm_id and row.amount_cents == 4999
            account_id = row.connect_account_id
            initial_dispatch = row.status == "onboarding"
        if initial_dispatch:
            balance = self.sdk.balance.retrieve()
            require_test_object(balance)
            assert sum(x.amount for x in balance.available if x.currency == "usd") >= 4999, (
                "run approved funding before dispatch"
            )
        account = self.core.accounts.retrieve(account_id, {"include": ["configuration.recipient"]})
        require_test_object(account)
        assert ready(account), "complete this synthetic account's hosted onboarding first"
        if crash:
            create = self.sdk.transfers.create

            def interrupted(params: Any, **options: Any) -> Any:
                if crash == "before_send":
                    self.observe("child_crash_before_transfer", payout_id=payout_id)
                    os._exit(71)
                result = create(params, **options)
                self.observe("child_crash_after_transfer", payout_id=payout_id, stripe_transfer=result.id)
                os._exit(72)

            self.sdk.transfers.create = interrupted
        # Injection is confined to the child test process; production has no fault switches.
        from unittest.mock import patch

        with (
            patch("recallops.payouts.stripe_connect.client", return_value=self.sdk),
            patch("recallops.payouts.stripe_connect.connect_client", return_value=self.core),
        ):
            result = process_payout(self.app, self.settings, self.ctx, payout_id)
        self.observe("payout_process_result", payout_id=payout_id, result=result)


def initialize() -> Acceptance:
    settings = local_settings()
    engine = build_engine(settings.database_url_internal)
    with build_engine(settings.database_url_owner).connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0006_payments"
    ctx, program, claim = _firm_program_claim(engine, status="submitted")
    other, _, other_claim = _firm_program_claim(engine, status="submitted")
    state = AcceptanceState(
        firm_id=ctx.firm_id,
        program_id=program,
        queued_claim_id=claim,
        other_firm_id=other.firm_id,
        other_claim_id=other_claim,
    )
    directory = ROOT / str(state.run_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "run.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE firms SET name=:n WHERE id=:f"), {"n": f"Acceptance {state.run_id}", "f": ctx.firm_id}
        )
        conn.execute(
            text(
                "INSERT INTO usage_meters(firm_id,period_start,claims_adjudicated) VALUES (:f,date_trunc('month',now())::date,100)"
            ),
            {"f": ctx.firm_id},
        )
    run = Acceptance(str(state.run_id))
    run.observe("isolated_acceptance_initialized", firm_id=ctx.firm_id, program_id=program, database=DATABASE)
    return run


@pytest.fixture
def acceptance() -> Acceptance:
    run_id = os.environ.get("STRIPE_ACCEPTANCE_RUN_ID")
    assert run_id, "set STRIPE_ACCEPTANCE_RUN_ID from the initialization command"
    return Acceptance(run_id)


def test_checkout_webhook_and_worker_resume(acceptance: Acceptance) -> None:
    acceptance.check_subscription()


def test_actual_forwarded_events_are_idempotent(acceptance: Acceptance) -> None:
    acceptance.check_subscription()
    acceptance.replay()


def test_three_refunds_have_one_delivery_each(acceptance: Acceptance) -> None:
    run = acceptance
    assert len(run.state.payout_ids) == 3
    for payout_id in run.state.payout_ids:
        with tenant_session(run.ctx, run.app) as session:
            payout = session.get(Payout, payout_id)
            assert payout and payout.status in {"transferred", "exported"}
            transfer_id, destination, batch = payout.stripe_transfer_id, payout.connect_account_id, payout.export_batch
        matches = [
            t
            for t in run.sdk.transfers.list({"destination": destination, "limit": 100}).auto_paging_iter()
            if data(t).get("metadata", {}).get("payout_id") == str(payout_id)
        ]
        if batch:
            assert not matches and not transfer_id
            continue
        assert len(matches) == 1 and matches[0].id == transfer_id and matches[0].amount == 4999
        detail = run.request("GET", f"/v1/claims/{payout.claim_id}").json()
        assert detail["payout"]["stripe_transfer_id"] == transfer_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "init",
            "listen",
            "serve",
            "worker",
            "checkout",
            "onboarding",
            "fund",
            "payout",
            "quantity",
            "reverse",
            "clock-setup",
            "delinquency",
            "cancellation",
            "export",
            "free-export",
            "verify",
        ],
    )
    parser.add_argument("--run", default=os.environ.get("STRIPE_ACCEPTANCE_RUN_ID"))
    parser.add_argument(
        "--allow-writes", action="store_true", help="record separate human approval; never itself authorization"
    )
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--crash", choices=["before_send", "after_success"])
    args = parser.parse_args()
    if (
        args.action
        in {
            "init",
            "checkout",
            "onboarding",
            "fund",
            "payout",
            "quantity",
            "reverse",
            "clock-setup",
            "delinquency",
            "cancellation",
            "export",
            "free-export",
        }
        and not args.allow_writes
    ):
        parser.error("this stage requires separately approved writes and --allow-writes")
    run = initialize() if args.action == "init" else Acceptance(args.run)
    configure_environment(run.settings)
    if args.action == "listen":
        stripe_cli = os.environ.get("STRIPE_CLI_PATH") or shutil.which("stripe")
        if not stripe_cli:
            parser.error("install Stripe CLI or set STRIPE_CLI_PATH")
        selections = {
            "STRIPE_WEBHOOK_SECRET": "checkout.session.completed,customer.subscription.created,customer.subscription.updated,customer.subscription.deleted,invoice.paid,invoice.payment_failed,transfer.created,transfer.reversed",
            "STRIPE_CONNECT_WEBHOOK_SECRET": "v2.core.account.updated,v2.core.account[configuration.recipient].updated,v2.core.account[configuration.recipient].capability_status_updated,v2.core.account[requirements].updated",
        }
        endpoint_secrets: dict[str, str] = {}
        lock = threading.Lock()
        children = []

        def consume(process: subprocess.Popen[str], name: str) -> None:
            assert process.stdout
            for line in process.stdout:
                match = re.search(r"whsec_[A-Za-z0-9]+", line)
                if match:
                    with lock:
                        endpoint_secrets[name] = match.group()
                        (run.directory / "endpoints.env").write_text(
                            "\n".join(f"{k}={v}" for k, v in endpoint_secrets.items()) + "\n", encoding="utf-8"
                        )
                    run.observe("stripe_listener_ready", secret_name=name)
                elif "evt_" in line or "error" in line.lower():
                    run.observe("stripe_forwarding", listener=name, message=line.strip())

        try:
            for name, events in selections.items():
                env = {**os.environ, "STRIPE_API_KEY": run.settings.stripe_secret_key}
                child = subprocess.Popen(
                    [
                        stripe_cli,
                        "listen",
                        "--skip-update",
                        "--events",
                        events,
                        "--forward-to",
                        BASE_URL + "/internal/stripe/webhook",
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                children.append(child)
                threading.Thread(target=consume, args=(child, name), daemon=True).start()
            while all(child.poll() is None for child in children):
                time.sleep(1)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
    elif args.action == "serve":
        from recallops.api.app import create_app

        uvicorn.run(create_app(), host="127.0.0.1", port=18000, access_log=False)
    elif args.action == "worker":
        worker = Worker(engine=run.internal, settings=run.settings, worker_id="payments-acceptance")
        worker.run_forever()
    elif args.action == "checkout":
        result = run.request("POST", "/v1/billing/checkout").json()
        (run.directory / "checkout.url").write_text(result["url"], encoding="utf-8")
        with tenant_session(run.ctx, run.app) as session:
            account = session.get(BillingAccount, run.ctx.firm_id)
            assert account
            run.state.checkout_session_id = account.checkout_session_id
        run.save()
        run.observe("hosted_checkout_ready", session=run.state.checkout_session_id)
    elif args.action == "onboarding":
        run.prepare_payouts()
    elif args.action == "fund":
        run.fund_once()
    elif args.action == "quantity":
        run.quantity()
    elif args.action == "reverse":
        run.reverse_once(args.index)
    elif args.action == "clock-setup":
        run.clock_setup()
    elif args.action == "delinquency":
        run.delinquency()
    elif args.action == "cancellation":
        run.cancellation()
    elif args.action == "export":
        run.export_fallback()
    elif args.action == "free-export":
        run.free_export()
    elif args.action == "payout":
        run.payout(args.index, args.crash)
    elif args.action == "verify":
        run.check_subscription()
        run.replay()


if __name__ == "__main__":
    main()
