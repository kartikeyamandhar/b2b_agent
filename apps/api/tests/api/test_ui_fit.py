"""Backend gaps the UI owner found while building apps/web (PR 9 notes), against a small seeded database."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import seed
from recallops.api import views
from recallops.models import Claim
from tests.conftest import CLEAN_ORDER

pytestmark = pytest.mark.db

ADMIN = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}


@pytest.fixture(scope="module")
def seeded(internal_engine_test: Engine) -> dict[str, Any]:
    with internal_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
        for table in CLEAN_ORDER:
            conn.execute(text(f"DELETE FROM {table}"))
    return seed.run(internal_engine_test, n=60)


@pytest.fixture(scope="module")
def client(seeded: dict[str, Any]) -> Iterator[TestClient]:
    from recallops.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _dev_link(claim_id: str) -> str:
    return f"/portal/dev.{seed.DEMO_FIRM_ID}.{claim_id}"


def _one(engine: Engine, sql: str) -> str:
    with engine.connect() as conn:
        value = conn.scalar(text(sql), {"f": seed.DEMO_FIRM_ID})
    assert value is not None, sql
    return str(value)


def test_the_portal_never_tells_a_claimant_their_claim_was_flagged(
    client: TestClient, internal_engine_test: Engine
) -> None:
    flagged = _one(internal_engine_test, "SELECT id FROM claims WHERE firm_id = :f AND status = 'flagged' LIMIT 1")
    view = client.get(_dev_link(flagged)).json()
    assert view["status"] == "under_review"
    assert client.get(f"/v1/claims/{flagged}", headers=ADMIN).json()["status"] == "flagged"  # the firm still sees it


def test_a_denial_names_only_the_rules_the_claim_failed(client: TestClient, internal_engine_test: Engine) -> None:
    denied = _one(
        internal_engine_test,
        "SELECT c.id FROM claims c JOIN agent_runs r ON r.claim_id = c.id JOIN agent_steps s ON s.run_id = r.id "
        "WHERE c.firm_id = :f AND c.status = 'denied' AND s.tool = 'check_model' "
        "AND s.result->>'verdict' = 'mismatch' LIMIT 1",
    )
    reasons = client.get(_dev_link(denied)).json()["decision_reasons"]
    assert "The model number shown is not part of this recall." in reasons
    assert "The purchase date is before the recalled products were sold." not in reasons


def test_reissuing_a_link_is_filed_under_the_claim(client: TestClient, internal_engine_test: Engine) -> None:
    flagged = _one(internal_engine_test, "SELECT id FROM claims WHERE firm_id = :f AND status = 'flagged' LIMIT 1")
    assert client.post(f"/v1/claims/{flagged}/link", headers=ADMIN).status_code == 200
    with internal_engine_test.connect() as conn:
        kind = conn.scalar(
            text("SELECT target_type FROM audit_log WHERE action = 'claim_link_reissued' AND target_id = :c"),
            {"c": flagged},
        )
    assert kind == "claim"


def test_a_typed_notice_keeps_its_title(client: TestClient) -> None:
    body = {
        "manual": {"title": "Acme Kettle Recall", "description": "This recall involves the Acme kettle, model AK-1."}
    }
    created = client.post("/v1/programs", json=body, headers=ADMIN)
    assert created.status_code == 201, created.text
    program_id = created.json()["id"]
    assert created.json()["title"] == "Acme Kettle Recall"
    assert client.get(f"/v1/programs/{program_id}", headers=ADMIN).json()["title"] == "Acme Kettle Recall"


def test_a_bad_cursor_is_a_listed_400(client: TestClient, seeded: dict[str, Any]) -> None:
    program_id = seeded["demo"]["program_id"]
    assert client.get(f"/v1/programs/{program_id}/claims?cursor=garbage", headers=ADMIN).status_code == 400
    listed = client.app.openapi()["paths"]["/v1/programs/{program_id}/claims"]["get"]["responses"]  # type: ignore[attr-defined]
    assert "400" in listed


def test_contract_examples_keep_their_null_fields(client: TestClient) -> None:
    schemas = client.app.openapi()["components"]["schemas"]  # type: ignore[attr-defined]
    assert schemas["Program"]["examples"][0]["closed_at"] is None
    assert schemas["Trace"]["examples"][0]["steps"][0]["thought"] is None


def test_the_decision_carries_why_a_claim_waits() -> None:
    claim = Claim(
        decision={
            "outcome": "approve",
            "reasons": ["every check passed"],
            "cited_rules": [],
            "routed_to_human": True,
            "hold_reason": "payout_held_needs_review",
            "reopened_from": "approved",
            "duplicate_found_later": ["c4"],
            "duplicates_ack": [],
            "question_for_claimant": None,
        },
        decided_by="agent",
    )
    decision = views.decision(claim)
    assert decision is not None
    assert (decision.hold_reason, decision.reopened_from, decision.duplicate_found_later) == (
        "payout_held_needs_review",
        "approved",
        ["c4"],
    )
