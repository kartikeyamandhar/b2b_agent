"""The API with AUTH_PROVIDER=clerk and CLAIM_TOKEN_PROVIDER=hmac against the test database (backend review of PR 8).
Session tokens are signed by a key generated here and the JWKS client is a stub, so nothing reaches Clerk."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import seed
from recallops.auth import clerk as clerk_module
from recallops.auth import deps
from recallops.auth.clerk import ClerkAuthProvider, ClerkPrincipal
from recallops.auth.hmac_tokens import HmacClaimTokenVerifier
from recallops.settings import Settings, get_settings
from tests.conftest import CLEAN_ORDER

pytestmark = pytest.mark.db

ISSUER = "https://test-instance.clerk.accounts.dev"
ORIGIN = "http://localhost:3000"
PORTAL = "http://localhost:3000/claim"
KID = "ins_e2e"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Clock:
    """The links' clock: a token's expiry is in whole seconds, so tests move time instead of sleeping (a slow CI
    runner crossed a second boundary between an issue and a reissue that a sleep-free test assumed were together)."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


CLOCK = Clock()


class _SigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class StubJwks:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        if jwt.get_unverified_header(token).get("kid") != KID:
            raise jwt.PyJWKClientError("unknown kid")
        return _SigningKey(KEY.public_key())


def session(org: str | None, role: str = "admin", user: str = "user_e2e") -> dict[str, str]:
    now = int(time.time())
    claims: dict[str, Any] = {"sub": user, "iss": ISSUER, "azp": ORIGIN, "iat": now, "nbf": now - 5, "exp": now + 60}
    if org is not None:
        claims |= {"v": 2, "o": {"id": org, "rol": role, "slg": org}}
    return {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": KID})}


def _clear() -> None:
    get_settings.cache_clear()
    deps.build_provider.cache_clear()
    deps.build_claim_verifier.cache_clear()


@pytest.fixture(scope="module")
def world(internal_engine_test: Engine, app_engine_test: Engine) -> Iterator[dict[str, Any]]:
    real_init, real_links = ClerkAuthProvider.__init__, HmacClaimTokenVerifier.__init__

    def stub_keys(self: ClerkAuthProvider, settings: Settings, jwks_client: Any = None, engine: Any = None) -> None:
        real_init(self, settings, jwks_client=StubJwks(), engine=engine)

    def test_clock(self: HmacClaimTokenVerifier, settings: Settings, clock: Any = None) -> None:
        real_links(self, settings, clock=CLOCK)

    with pytest.MonkeyPatch.context() as mp:
        for key, value in {
            "AUTH_PROVIDER": "clerk",
            "CLAIM_TOKEN_PROVIDER": "hmac",
            "CLAIM_TOKEN_SECRET": base64.b64encode(os.urandom(32)).decode(),
            "CLERK_ISSUER": ISSUER,
            "CLERK_JWKS_URL": "",
            "CLERK_ALLOWED_ORIGINS": ORIGIN,
            "PORTAL_BASE_URL": PORTAL,
            "SEED_CLERK_ORG_ID": "org_demo",
        }.items():
            mp.setenv(key, value)
        mp.setattr(clerk_module.ClerkAuthProvider, "__init__", stub_keys)
        mp.setattr(HmacClaimTokenVerifier, "__init__", test_clock)
        _clear()
        try:
            with internal_engine_test.begin() as conn:
                conn.execute(text("SELECT set_config('app.audit_purge', 'on', true)"))
                for table in CLEAN_ORDER:
                    conn.execute(text(f"DELETE FROM {table}"))
            # The admin signs in before seeding, which creates an empty firm for the organization.
            early = ClerkAuthProvider(get_settings(), engine=app_engine_test).firm_context(
                ClerkPrincipal(user_id="user_e2e", firm_id=None, role="admin", org_id="org_demo", org_slug="demo")
            )
            seeded = seed.run(internal_engine_test, n=3)
            with internal_engine_test.begin() as conn:
                conn.execute(
                    text("UPDATE firms SET clerk_org_id = 'org_other' WHERE id = :f"), {"f": seed.OTHER_FIRM_ID}
                )
            from recallops.api.app import create_app

            with TestClient(create_app()) as client:
                yield {"client": client, "seeded": seeded, "early_firm": early.firm_id, "db": internal_engine_test}
        finally:
            _clear()
    _clear()


def test_the_seed_takes_over_the_organization_from_an_earlier_sign_in(world: dict[str, Any]) -> None:
    with world["db"].connect() as conn:
        bound = dict(conn.execute(text("SELECT id, clerk_org_id FROM firms")).all())
    assert bound[seed.DEMO_FIRM_ID] == "org_demo"
    assert bound[world["early_firm"]] is None  # left unbound, never deleted


def test_sessions_reach_only_their_own_firm(world: dict[str, Any]) -> None:
    c, claim = world["client"], world["seeded"]["demo"]["first_claim_id"]
    assert c.get(f"/v1/claims/{claim}", headers=session("org_demo")).status_code == 200
    assert c.get(f"/v1/claims/{claim}", headers=session("org_other")).status_code == 404
    assert c.get(f"/v1/claims/{claim}", headers=session("org_new")).status_code == 404  # a brand-new firm
    assert c.get("/v1/programs").status_code == 401
    dev_headers = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}
    assert c.get("/v1/programs", headers=dev_headers).status_code == 401  # the dev seam is off
    member = c.get("/v1/programs", headers=session("org_demo", "org:member"))
    assert member.status_code == 403 and "org:admin or org:reviewer" in member.json()["detail"]


def _issue(c: TestClient, program_id: str) -> tuple[str, str]:
    response = c.post(f"/v1/programs/{program_id}/claims/issue", json={"n": 1}, headers=session("org_demo"))
    assert response.status_code == 200, response.text
    link = response.json()["links"][0]
    return link["claim_id"], link["url"]


def test_signed_links_open_once_issued_and_refuse_tampering(world: dict[str, Any]) -> None:
    c, program_id = world["client"], world["seeded"]["demo"]["program_id"]
    reviewer = session("org_demo", "reviewer", user="user_reviewer")
    assert c.post(f"/v1/programs/{program_id}/claims/issue", json={"n": 1}, headers=reviewer).status_code == 403
    other = session("org_other")
    assert c.post(f"/v1/programs/{program_id}/claims/issue", json={"n": 1}, headers=other).status_code == 404
    _, url = _issue(c, program_id)
    assert url.startswith(PORTAL + "/")
    token = url.removeprefix(PORTAL + "/")
    assert len(token) == 96 and "/" not in token
    assert c.get(f"/portal/{token}").status_code == 200
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert c.get(f"/portal/{tampered}").status_code == 404


def test_a_reissued_link_replaces_the_old_one(world: dict[str, Any]) -> None:
    c, program_id = world["client"], world["seeded"]["demo"]["program_id"]
    claim_id, first = _issue(c, program_id)
    old = first.removeprefix(PORTAL + "/")
    assert c.post(f"/v1/claims/{claim_id}/link", headers=session("org_demo", "reviewer")).status_code == 403
    assert c.post(f"/v1/claims/{claim_id}/link", headers=session("org_other")).status_code == 404
    assert c.post(f"/v1/claims/{uuid4()}/link", headers=session("org_demo")).status_code == 404
    same = c.post(f"/v1/claims/{claim_id}/link", headers=session("org_demo"))  # the issuing second: same token
    CLOCK.now += 5
    response = c.post(f"/v1/claims/{claim_id}/link", headers=session("org_demo"))
    assert same.status_code == response.status_code == 200 and response.json()["claim_id"] == claim_id
    new = response.json()["url"].removeprefix(PORTAL + "/")
    assert new != old
    assert c.get(f"/portal/{old}").status_code == 404
    assert c.get(f"/portal/{new}").status_code == 200
    with world["db"].connect() as conn:
        audited = conn.scalars(
            text(
                "SELECT after->>'rotated' FROM audit_log WHERE action = 'claim_link_reissued' AND target_id = :c "
                "ORDER BY at"
            ),
            {"c": claim_id},
        ).all()
    assert audited == ["false", "true"]  # the same second, then a real rotation


def test_closing_the_program_kills_links_and_refuses_reissue(world: dict[str, Any]) -> None:
    """Runs last in this module: it closes the demo program."""
    c, program_id = world["client"], world["seeded"]["demo"]["program_id"]
    claim_id, url = _issue(c, program_id)
    closed = c.patch(f"/v1/programs/{program_id}", json={"status": "closed"}, headers=session("org_demo"))
    assert closed.status_code == 200, closed.text
    assert c.get(url.replace(PORTAL, "/portal")).status_code == 404
    refused = c.post(f"/v1/claims/{claim_id}/link", headers=session("org_demo"))
    assert refused.status_code == 409 and "closed" in refused.json()["detail"]
