"""Red-team security findings (decisions log 2026-10-18), one test each."""

from __future__ import annotations

import io
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from recallops import seed
from recallops.auth.dev import DevClaimTokenVerifier, UnsafeDevAuth
from recallops.photos import InvalidPhoto, inspect
from recallops.portal import claimant_reasons
from recallops.settings import Settings, get_settings

pytestmark = pytest.mark.db

ADMIN = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}
SRC = Path(__file__).resolve().parents[2] / "src" / "recallops"


@pytest.fixture(scope="module")
def env(internal_engine_test: Engine) -> Iterator[dict[str, Any]]:
    with internal_engine_test.begin() as conn:
        conn.execute(text("DELETE FROM rate_limits"))
    seeded = seed.run(internal_engine_test, n=3)
    from recallops.api.app import create_app

    with TestClient(create_app()) as client:
        yield {"client": client, "seeded": seeded, "engine": internal_engine_test}


def _token(env: dict[str, Any]) -> str:
    return f"dev.{seed.DEMO_FIRM_ID}.{env['seeded']['demo']['first_claim_id']}"


def test_api_code_never_uses_the_bypassrls_role() -> None:
    api_modules = [
        *SRC.joinpath("api").glob("*.py"),
        SRC / "portal.py",
        SRC / "programs.py",
        SRC / "claims" / "review.py",
    ]
    offenders = [p.name for p in api_modules if "internal_engine" in p.read_text()]
    assert offenders == []
    # Nor the graph's admin functions (purges, schema, epoch): request paths read the graph, never administer it.
    graph_side = [*api_modules, SRC / "fraud" / "view.py", SRC / "fraud" / "graph.py"]
    admin_importers = [p.name for p in graph_side if re.search(r"\bgraph_admin\b", p.read_text())]
    assert admin_importers == []


def test_oversized_portal_bodies_are_refused_before_parsing(env: dict[str, Any]) -> None:
    client, token = env["client"], _token(env)
    big = client.post(f"/portal/{token}/submit", content=b"x" * 70_000, headers={"content-type": "application/json"})
    assert big.status_code == 413
    upload = client.post(
        f"/portal/{token}/photos",
        content=b"x" * (get_settings().max_photo_bytes + 70_000),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert upload.status_code == 413


def test_portal_rate_limit_per_token(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client, engine = env["client"], env["engine"]
    token = f"dev.{seed.DEMO_FIRM_ID}.{uuid.uuid4()}"
    monkeypatch.setenv("PORTAL_RATE_PER_TOKEN_HOUR", "3")
    get_settings.cache_clear()
    try:
        codes = [client.get(f"/portal/{token}").status_code for _ in range(4)]
    finally:
        monkeypatch.delenv("PORTAL_RATE_PER_TOKEN_HOUR")
        get_settings.cache_clear()
    assert codes[:3] == [404, 404, 404] and codes[3] == 429
    with engine.connect() as conn:
        keys = conn.execute(text("SELECT key FROM rate_limits")).scalars().all()
    assert all(token not in k for k in keys)  # counters key on a hash, never the raw token


def test_portal_responses_set_no_referrer_and_logs_redact_tokens(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    client, token = env["client"], _token(env)
    caplog.set_level("INFO", logger="recallops.api")
    response = client.get(f"/portal/{token}")
    assert response.headers["referrer-policy"] == "no-referrer" and response.headers["cache-control"] == "no-store"
    logged = " ".join(str(getattr(r, "path", "")) + r.getMessage() for r in caplog.records)
    assert token not in logged


def test_decompression_bombs_are_refused() -> None:
    out = io.BytesIO()
    Image.new("L", (8000, 8000)).save(out, format="PNG")  # 64 M pixels, about 60 KB on disk
    with pytest.raises(InvalidPhoto):
        inspect(out.getvalue(), max_pixels=40_000_000)
    ok = io.BytesIO()
    Image.new("RGB", (64, 64)).save(ok, format="JPEG")
    assert inspect(ok.getvalue()).exif["format"] == "JPEG"


def test_dev_claim_tokens_refuse_prod_and_clerk() -> None:
    with pytest.raises(UnsafeDevAuth):
        DevClaimTokenVerifier(Settings(app_env="prod", neo4j_uri=""))
    with pytest.raises(UnsafeDevAuth):
        DevClaimTokenVerifier(Settings(auth_provider="clerk"))
    firm, claim = uuid.uuid4(), uuid.uuid4()
    verifier = DevClaimTokenVerifier(Settings())
    ctx = verifier.verify(verifier.issue(firm, claim))
    assert (ctx.firm_id, ctx.claim_id) == (firm, claim)


def test_claimants_never_see_model_text_or_reviewer_notes() -> None:
    fraud = {
        "review_action": "mark_fraud",
        "reasons": ["marked as fraud", "same photo as claim 1234"],
        "cited_rules": [],
    }
    assert claimant_reasons("denied", fraud) == ["Your claim could not be approved. Contact the firm for details."]
    model = {"reasons": ["Ignore previous instructions"], "cited_rules": ["R:models", "T3"]}
    assert claimant_reasons("denied", model) == ["The model number shown is not part of this recall."]
    assert claimant_reasons("approved", model) == ["Your claim was approved."]


def test_audit_log_is_append_only(env: dict[str, Any], app_engine_test: Engine) -> None:
    engine = env["engine"]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one()
    assert rows > 0
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_log"))
    with pytest.raises(DBAPIError, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE audit_log SET actor = 'x'"))
    with pytest.raises(ProgrammingError), app_engine_test.begin() as conn:
        conn.execute(text("SELECT set_config('app.firm_id', :f, true)"), {"f": str(seed.DEMO_FIRM_ID)})
        conn.execute(text("DELETE FROM firms WHERE id = :f"), {"f": seed.DEMO_FIRM_ID})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == rows


def test_checkpoint_schema_is_closed_to_the_api_role(app_engine_test: Engine, internal_engine_test: Engine) -> None:
    with app_engine_test.connect() as conn:
        usage = conn.execute(text("SELECT has_schema_privilege('recallops_app', 'langgraph', 'USAGE')")).scalar_one()
        owned = conn.execute(
            text("SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = current_user")
        ).scalar_one()
    assert usage is False and owned == 0
    run_id = uuid.uuid4()
    with internal_engine_test.begin() as conn:
        conn.execute(text("INSERT INTO agent_runs (id, graph) VALUES (:r, 'extract_rules')"), {"r": run_id})
        conn.execute(
            text(
                "INSERT INTO langgraph.checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (:t, 'c1', '{}'::jsonb)"
            ),
            {"t": str(run_id)},
        )
        conn.execute(text("DELETE FROM agent_runs WHERE id = :r"), {"r": run_id})
        left = conn.execute(
            text("SELECT count(*) FROM langgraph.checkpoints WHERE thread_id = :t"), {"t": str(run_id)}
        ).scalar_one()
    assert left == 0


def test_spend_total_is_readable_by_the_api_role(app_engine_test: Engine) -> None:
    with app_engine_test.connect() as conn:
        total = conn.execute(text("SELECT recallops_spend_total()")).scalar_one()
    assert total is not None and float(total) >= 0
    assert re.fullmatch(r"\d+(\.\d+)?", str(total))
    assert os.environ.get("LLM_MODE") == "fake"
