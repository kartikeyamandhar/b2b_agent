"""M2 and M3 flows through the API and the worker, against the test database: open a program, issue links, file a
claim through the portal with real image uploads, adjudicate it with the agent (scripted model), read its trace,
hold a flagged claim for a person and resume the same run, and the guards around all of it."""

from __future__ import annotations

import io
import random
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import seed
from recallops.claims.worker import Worker
from recallops.settings import get_settings
from recallops.synth.render import draw_style, render_destruction, render_label, render_receipt
from tests import fakes
from tests.fakes import decide, destruction, label, tool_use, turn

pytestmark = pytest.mark.db

ADMIN = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin", "X-Dev-User-Id": "dev-admin"}
REVIEWER = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "reviewer", "X-Dev-User-Id": "dev-reviewer"}
OTHER = {"X-Dev-Firm-Id": str(seed.OTHER_FIRM_ID), "X-Dev-Role": "admin"}
FIELDS = {
    "name": "Avery Test",
    "email": "avery@example.com",
    "address_line1": "1 Main St",
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "model_claimed": "TL1302Z",
    "retailer_claimed": "Amazon.com",
    "purchase_date": "2025-03-14",
}


def jpeg(kind: str, seed_value: int) -> bytes:
    rng = random.Random(seed_value)
    style = draw_style(rng)
    if kind == "label":
        return render_label(rng, style, ["MODEL TL1302Z", f"SER {seed_value:08d}", "120V~ 60Hz"])
    if kind == "destruction":
        return render_destruction(rng, style, "cut_cord", True)
    return render_receipt(rng, style, "Amazon.com", "2025-03-14", 4999)


@pytest.fixture(scope="module")
def env(internal_engine_test: Engine) -> Iterator[dict[str, Any]]:
    with internal_engine_test.begin() as conn:
        for table in ("document_chunks", "documents", "payouts", "worker_heartbeats", "rate_limits"):
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("DELETE FROM langgraph.checkpoint_writes"))
        conn.execute(text("DELETE FROM langgraph.checkpoint_blobs"))
        conn.execute(text("DELETE FROM langgraph.checkpoints"))
    seeded = seed.run(internal_engine_test, n=6)
    from recallops.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "seeded": seeded, "engine": internal_engine_test}


def open_program(client: TestClient, engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        recall_id = conn.execute(text("SELECT id FROM recalls WHERE source_id = '27024'")).scalar_one()
    created = client.post("/v1/programs", headers=ADMIN, json={"recall_id": str(recall_id)})
    assert created.status_code == 201, created.text
    program = created.json()
    assert program["status"] == "draft" and program["identification_rules"]["models"] == ["TL1302Z"]
    remedy = program["remedy_rules"] | {"destruction_instruction": "cut the unplugged power cord and photograph it"}
    opened = client.patch(
        f"/v1/programs/{program['id']}", headers=ADMIN, json={"status": "open", "remedy_rules": remedy}
    )
    assert opened.status_code == 200 and opened.json()["status"] == "open"
    body: dict[str, Any] = opened.json()
    return body


def file_claim(client: TestClient, url: str, photo_seed: int, fields: dict[str, Any] | None = None) -> str:
    token = url.rsplit("/", 1)[1]
    for kind in ("label", "destruction", "receipt"):
        up = client.post(
            f"/portal/{token}/photos",
            data={"kind": kind},
            files={"file": (f"{kind}.jpg", io.BytesIO(jpeg(kind, photo_seed)), "image/jpeg")},
        )
        assert up.status_code == 200, up.text
    first = client.post(f"/portal/{token}/submit", json=fields or FIELDS)
    again = client.post(f"/portal/{token}/submit", json={**(fields or FIELDS), "name": "Someone Else"})
    assert first.json()["status"] == again.json()["status"] == "submitted"
    return token


def approve_script() -> list[Any]:
    return [
        turn(
            tool_use(1, "read_label_photo"),
            tool_use(2, "check_purchase"),
            tool_use(3, "check_duplicates"),
            tool_use(4, "check_destruction_photo"),
            text="Checking the label, purchase, duplicates, and destruction photo.",
        ),
        label("TL1302Z"),
        destruction(True),
        turn(tool_use(5, "check_model", {"model_text": "TL1302Z"})),
        decide(6, "approve", ["T5", "R:models"]),
    ]


def worker(engine: Engine, responses: list[Any]) -> tuple[Worker, Any]:
    llm, transport = fakes.client(responses, get_settings())
    return Worker(engine=engine, llm=llm, worker_id="test-worker"), transport


def test_portal_to_agent_decision_to_payout(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    issued = client.post(f"/v1/programs/{program['id']}/claims/issue", headers=ADMIN, json={"n": 2}).json()["links"]
    assert len(issued) == 2 and all(link["url"].startswith(get_settings().portal_base_url) for link in issued)
    token = file_claim(client, issued[0]["url"], 1)
    claim_id = issued[0]["claim_id"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM claims WHERE id = :c"), {"c": claim_id}).scalar_one() == 1
        assert (
            conn.execute(text("SELECT claimant->>'name' FROM claims WHERE id = :c"), {"c": claim_id}).scalar_one()
            == "Avery Test"
        )

    w, transport = worker(engine, approve_script())
    assert w.tick() is True
    assert len(transport.calls) == 5
    detail = client.get(f"/v1/claims/{claim_id}", headers=REVIEWER).json()
    assert detail["status"] == "approved" and detail["decision"]["outcome"] == "approve"
    assert detail["payout"]["status"] == "pending" and detail["payout"]["amount_cents"] == 5600
    trace = client.get(f"/v1/claims/{claim_id}/trace", headers=REVIEWER).json()
    assert trace["run"]["graph"] == "adjudicate" and trace["run"]["status"] == "done"
    assert trace["used"]["steps"] == 5 and float(trace["used"]["cost_usd"]) > 0
    nodes = [s["node"] for s in trace["steps"]]
    assert nodes[0] == "load_context" and nodes[-1] == "route" and "read_label_photo" in nodes
    assert client.get(f"/portal/{token}").json()["status"] == "approved"
    assert w.tick() is False  # nothing left
    usage = client.get("/v1/usage", headers=ADMIN).json()
    assert usage["claims_adjudicated"] >= 1


def test_flagged_claim_waits_and_the_review_resumes_the_same_run(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = client.get("/v1/programs", headers=ADMIN).json()["items"]
    program_id = next(
        p["id"]
        for p in program
        if p["status"] == "open" and p["recall_id"] and p["id"] != env["seeded"]["demo"]["program_id"]
    )
    link = client.post(f"/v1/programs/{program_id}/claims/issue", headers=ADMIN, json={"n": 1}).json()["links"][0]
    file_claim(client, link["url"], 1)  # same photos as the approved claim: a duplicate
    claim_id = link["claim_id"]
    w, transport = worker(engine, [turn(tool_use(1, "check_duplicates")), decide(2, "flag", ["T1"])])
    assert w.tick() is True
    detail = client.get(f"/v1/claims/{claim_id}", headers=REVIEWER).json()
    assert detail["status"] == "flagged" and detail["run"]["status"] == "waiting_human"
    run_id = detail["run"]["run_id"]
    steps_before = len(client.get(f"/v1/claims/{claim_id}/trace", headers=REVIEWER).json()["steps"])

    bad = client.post(f"/v1/claims/{claim_id}/actions", headers=REVIEWER, json={"action": "resume_override"})
    assert bad.status_code == 422
    queued = client.post(
        f"/v1/claims/{claim_id}/actions",
        headers=REVIEWER,
        json={"action": "mark_fraud", "message": "same label photo as another claim"},
    )
    assert queued.status_code == 200 and queued.json()["status"] == "flagged"
    twice = client.post(f"/v1/claims/{claim_id}/actions", headers=REVIEWER, json={"action": "mark_fraud"})
    assert twice.status_code == 409

    assert w.tick() is True  # the resume
    assert len(transport.calls) == 2  # nothing before the pause ran again
    detail = client.get(f"/v1/claims/{claim_id}", headers=REVIEWER).json()
    assert detail["status"] == "denied" and detail["decided_at"]
    assert detail["run"]["run_id"] == run_id and detail["run"]["status"] == "done"
    trace = client.get(f"/v1/claims/{claim_id}/trace", headers=REVIEWER).json()
    assert [s["node"] for s in trace["steps"][steps_before:]] == ["human_review"]
    assert any(a["action"] == "review_submitted" for a in detail["audit"])

    # The earlier approved claim with the same photos and address was reopened when this duplicate was flagged
    # (decision time, Postgres, no graph): back to submitted with its payout held, so the adjudicator decides it
    # again with the duplicate in view, and flags it through the interrupt. Both claims end flagged (section 12).
    earlier = next(
        c
        for c in client.get(f"/v1/programs/{program_id}/claims?limit=50", headers=REVIEWER).json()["items"]
        if c["id"] != claim_id and c["status"] == "submitted"
    )
    with engine.connect() as conn:
        held = conn.execute(
            text("SELECT status, last_error FROM payouts WHERE claim_id = :c"), {"c": earlier["id"]}
        ).one()
        reopened = conn.execute(
            text("SELECT count(*) FROM audit_log WHERE action = 'claim_reopened_duplicate' AND target_id = :c"),
            {"c": earlier["id"]},
        ).scalar_one()
    assert held.status == "held" and held.last_error == "duplicate_found_later" and reopened == 1
    w2, _ = worker(engine, [turn(tool_use(1, "check_duplicates")), decide(2, "flag", ["T1"])])
    assert w2.tick() is True
    again = client.get(f"/v1/claims/{earlier['id']}", headers=REVIEWER).json()
    assert again["status"] == "flagged" and again["run"]["status"] == "waiting_human"
    assert w2.tick() is False


def test_direct_review_of_a_rules_only_claim_and_guards(env: dict[str, Any]) -> None:
    client = env["client"]
    pid = env["seeded"]["demo"]["program_id"]
    claims = client.get(f"/v1/programs/{pid}/claims?limit=50", headers=REVIEWER).json()["items"]
    target = next(c for c in claims if c["status"] in ("approved", "denied", "needs_info", "flagged"))
    response = client.post(
        f"/v1/claims/{target['id']}/actions",
        headers=REVIEWER,
        json={"action": "request_info", "message": "Please send a clearer label photo"},
    )
    assert response.status_code == 200 and response.json()["status"] == "needs_info"
    assert (
        client.post(f"/v1/claims/{target['id']}/actions", headers=OTHER, json={"action": "mark_fraud"}).status_code
        == 404
    )
    other_program = env["seeded"]["other"]["program_id"]
    assert client.patch(f"/v1/programs/{other_program}", headers=ADMIN, json={"status": "closed"}).status_code == 404
    assert client.post(f"/v1/programs/{pid}/claims/issue", headers=REVIEWER, json={"n": 1}).status_code == 403


def test_portal_rejects_bad_uploads_and_closed_programs(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    program = open_program(client, engine)
    link = client.post(f"/v1/programs/{program['id']}/claims/issue", headers=ADMIN, json={"n": 1}).json()["links"][0]
    token = link["url"].rsplit("/", 1)[1]
    not_image = client.post(
        f"/portal/{token}/photos",
        data={"kind": "label"},
        files={"file": ("x.jpg", io.BytesIO(b"not an image"), "image/jpeg")},
    )
    assert not_image.status_code == 422
    wrong_type = client.post(
        f"/portal/{token}/photos",
        data={"kind": "label"},
        files={"file": ("x.gif", io.BytesIO(jpeg("label", 2)), "image/gif")},
    )
    assert wrong_type.status_code == 422
    wrong_kind = client.post(
        f"/portal/{token}/photos",
        data={"kind": "../../etc"},
        files={"file": ("x.jpg", io.BytesIO(jpeg("label", 2)), "image/jpeg")},
    )
    assert wrong_kind.status_code == 422
    assert client.post(f"/portal/{token}/answer", json={"message": "hi"}).status_code == 409
    view = client.get(f"/portal/{token}").json()
    assert view["status"] == "draft" and view["photos_uploaded"] == []
    assert client.get(f"/portal/dev.{UUID(int=12345)}").status_code == 404
    client.patch(f"/v1/programs/{program['id']}", headers=ADMIN, json={"status": "closed"})
    assert client.get(f"/portal/{token}").status_code == 404
    reopen = client.patch(f"/v1/programs/{program['id']}", headers=ADMIN, json={"status": "open"})
    assert reopen.status_code == 409


def test_readyz_reports_the_worker_heartbeat(env: dict[str, Any]) -> None:
    client, engine = env["client"], env["engine"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM worker_heartbeats"))
    assert client.get("/readyz").status_code == 503
    Worker(engine=engine, llm=fakes.client([])[0], worker_id="hb").heartbeat({"adjudicator": "agent"})
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["worker_heartbeat_age_s"] < 60


def test_check_payout_export_and_a_reconciled_mpr(env: dict[str, Any], tmp_path: Any) -> None:
    from datetime import UTC, datetime

    from recallops.payouts.export import export_pending

    client, engine = env["client"], env["engine"]
    program_id = next(
        p["id"]
        for p in client.get("/v1/programs", headers=ADMIN).json()["items"]
        if p["status"] == "open" and p["recall_id"] and p["id"] != env["seeded"]["demo"]["program_id"]
    )
    link = client.post(f"/v1/programs/{program_id}/claims/issue", headers=ADMIN, json={"n": 1}).json()["links"][0]
    other_person = FIELDS | {"name": "Blake Other", "email": "blake@example.org", "address_line1": "9 Oak Ave"}
    token = file_claim(client, link["url"], 4242, other_person)
    w, _ = worker(engine, approve_script())
    assert w.tick() is True
    approved = client.get(f"/v1/claims/{link['claim_id']}", headers=ADMIN).json()
    assert approved["status"] == "approved", approved["decision"]
    stripe = client.post(f"/portal/{token}/payout", json={"method": "stripe_connect"})
    assert stripe.status_code == 501 and "payments owner" in stripe.json()["detail"]
    chose = client.post(
        f"/portal/{token}/payout", json={"method": "check_export", "mailing_address": "77 Elm Rd, Peoria IL 61602"}
    )
    assert chose.status_code == 200 and chose.json()["payout_status"] == "pending_export"
    written = export_pending(engine, tmp_path)
    assert written and written[0]["rows"] >= 1
    assert "77 Elm Rd, Peoria IL 61602" in (tmp_path / "exports").rglob("*.csv").__next__().read_text()
    view = client.get(f"/portal/{token}").json()
    assert view["payout_status"] == "exported" and view["status"] == "paid"
    batches = client.get("/v1/payouts/exports", headers=ADMIN).json()["items"]
    csv_body = client.get(f"/v1/payouts/exports/{batches[0]['id']}.csv", headers=ADMIN)
    assert csv_body.status_code == 200 and "77 Elm Rd" in csv_body.text
    assert client.get(f"/v1/payouts/exports/{batches[0]['id']}.csv", headers=REVIEWER).status_code == 403
    assert client.get(f"/v1/payouts/exports/{batches[0]['id']}.csv", headers=OTHER).status_code == 404
    assert client.post(f"/portal/{token}/payout", json={"method": "check_export"}).status_code == 409

    period = datetime.now(UTC).strftime("%Y-%m")
    assert client.get(f"/v1/programs/{program_id}/mpr?period={period}", headers=ADMIN).status_code == 404
    report = client.post(f"/v1/programs/{program_id}/mpr/generate?period={period}", headers=ADMIN)
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["reconciled"] is True
    assert body["payload"]["boxes"]["corrections_this_period"] >= 1
    assert body["payload"]["boxes"]["total_units_affected"] == 53634
    again = client.get(f"/v1/programs/{program_id}/mpr?period={period}", headers=ADMIN).json()
    assert again["id"] == body["id"]
    pdf = client.get(f"/v1/programs/{program_id}/mpr/{body['id']}.pdf", headers=ADMIN)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
    assert client.post(f"/v1/programs/{program_id}/mpr/generate?period={period}", headers=REVIEWER).status_code == 403


def test_a_mailing_address_shared_with_another_claim_holds_the_payout(env: dict[str, Any]) -> None:
    """The redirect pattern check_duplicates cannot see: an approved claimant asks for the check at an address another
    claim of the program uses. The claim goes to a person and its payout is held, in the same transaction."""
    client, engine = env["client"], env["engine"]
    program_id = next(
        p["id"]
        for p in client.get("/v1/programs", headers=ADMIN).json()["items"]
        if p["status"] == "open" and p["recall_id"] and p["id"] != env["seeded"]["demo"]["program_id"]
    )
    link = client.post(f"/v1/programs/{program_id}/claims/issue", headers=ADMIN, json={"n": 1}).json()["links"][0]
    person = FIELDS | {"name": "Casey Third", "email": "casey@example.net", "address_line1": "5 Pine Ct"}
    token = file_claim(client, link["url"], 9191, person)
    w, _ = worker(engine, approve_script())
    assert w.tick() is True
    assert client.get(f"/v1/claims/{link['claim_id']}", headers=ADMIN).json()["status"] == "approved"
    held = client.post(
        f"/portal/{token}/payout",
        json={"method": "check_export", "mailing_address": "  1 Main St, Springfield IL 62701\n"},
    )
    assert held.status_code == 200 and held.json()["payout_status"] == "held"
    assert held.json()["status"] == "under_review"
    with engine.connect() as conn:
        audit = conn.execute(
            text("SELECT after FROM audit_log WHERE action = 'payout_address_changed' AND target_id = :c"),
            {"c": link["claim_id"]},
        ).scalar_one()
    assert set(audit) == {"mailing_fp"} and "Main" not in str(audit)  # a fingerprint, never the address
