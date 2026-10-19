"""M5 campaign tooling through the API (test database): synthetic registrations, matching with the adjudicator's
pure-code checks, drafts that quote the notice and carry a claim link, the simulated send, and the MPR notified box."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import seed

pytestmark = pytest.mark.db

ADMIN = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin", "X-Dev-User-Id": "dev-admin"}
REVIEWER = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "reviewer", "X-Dev-User-Id": "dev-reviewer"}
OTHER = {"X-Dev-Firm-Id": str(seed.OTHER_FIRM_ID), "X-Dev-Role": "admin"}
CSV = """external_ref,name,email,phone,address,model,serial,unknown_column
R1,Avery Fen,avery@example.com,(312) 555-0101,1 Bel St Springfield IL 62701,TL1302Z,,x
R2,=HYPERLINK("x"),riley@example.org,(312) 555-0102,2 Cor Ave Springfield IL,TL1302Z,,x
R3,Casey Dan,casey@example.net,,3 Dan Rd Springfield IL,TL1302Z,,x
R4,Morgan Lin,morgan@example.com,(312) 555-0104,4 Lin Ln Springfield IL,TL9000Q,,x
R5,Real Person,real.person@gmail.com,,5 Real St,TL1302Z,,x
R6,Bad Phone,bad@example.com,(312) 867-5309,6 Main St,TL1302Z,,x
R1,Repeat,repeat@example.com,,7 Main St,TL1302Z,,x
R8,Card Only,,,8 Elm St Springfield IL,TL1302Z,,x
R99999999999999999999999999999999999999999999999999999999999999999999999999999999,Long Ref,long@example.com,,9 Elm St,TL1302Z,,x
"""


@pytest.fixture(scope="module")
def env(internal_engine_test: Engine) -> Iterator[dict[str, Any]]:
    seeded = seed.run(internal_engine_test, n=6)
    from recallops.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "program": seeded["demo"]["program_id"], "engine": internal_engine_test}


def test_a_campaign_from_registrations_to_the_mpr(env: dict[str, Any]) -> None:
    client, pid = env["client"], env["program"]
    files = {"file": ("owners.csv", CSV.encode(), "text/csv")}
    assert client.post(f"/v1/programs/{pid}/registrations", headers=REVIEWER, files=files).status_code == 403
    assert client.post(f"/v1/programs/{pid}/registrations", headers=OTHER, files=files).status_code == 404
    up = client.post(f"/v1/programs/{pid}/registrations", headers=ADMIN, files=files)
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["accepted"] == 4 and body["rejected_total"] == 5
    reasons = {r["row"]: r["reason"] for r in body["rejected"]}
    assert "not synthetic" in reasons[6] and "not synthetic" in reasons[7] and "repeats" in reasons[8]
    assert "no synthetic marker" in reasons[9] and "longer than 80" in reasons[10]

    matched = client.post(f"/v1/programs/{pid}/campaign/match", headers=ADMIN).json()["counts"]
    assert matched == {"matched": 3, "unmatched": 1}

    drafts = client.post(
        f"/v1/programs/{pid}/campaign/drafts", headers=ADMIN, json={"channels": ["email", "phone_text"]}
    )
    assert drafts.status_code == 200 and drafts.json()["counts"] == {"email": 3, "phone_text": 2}
    again = client.post(f"/v1/programs/{pid}/campaign/drafts", headers=ADMIN, json={"channels": ["email"]})
    assert again.json()["counts"] == {"email": 0}  # idempotent per owner and channel
    summary = client.get(f"/v1/programs/{pid}/campaign", headers=REVIEWER).json()
    assert summary["claim_links"] == 3 and summary["notifications"]["email:draft"] == 3 and summary["simulated"]
    email = next(s for s in summary["sample"] if s["channel"] == "email")
    assert "Hazard (from the recall notice):" in email["body"] and "{claim_link}" in email["body"]
    assert "dev." not in email["body"]  # the claim token is never stored in a draft (invariant 10)

    sent = client.post(f"/v1/programs/{pid}/campaign/send", headers=ADMIN, json={"channel": "email"}).json()["counts"]
    assert sent == {"email": 3}
    period = datetime.now(UTC).strftime("%Y-%m")
    report = client.post(f"/v1/programs/{pid}/mpr/generate?period={period}", headers=ADMIN).json()
    assert report["reconciled"] and report["payload"]["computed"]["notified_email"] == 3
    assert report["payload"]["computed"]["notified_phone_text"] == 0  # drafted, not sent
    with env["engine"].connect() as conn:
        drafts_left = conn.execute(
            text("SELECT count(*) FROM notifications WHERE program_id = :p AND status = 'draft'"), {"p": pid}
        ).scalar_one()
    assert drafts_left == 2


def test_an_oversized_registrations_upload_is_refused_before_parsing(env: dict[str, Any]) -> None:
    from recallops.settings import get_settings

    client, pid = env["client"], env["program"]
    big = b"external_ref\n" + b"R" * (get_settings().registrations_max_bytes + 70_000)
    response = client.post(
        f"/v1/programs/{pid}/registrations", headers=ADMIN, files={"file": ("x.csv", big, "text/csv")}
    )
    assert response.status_code == 413
