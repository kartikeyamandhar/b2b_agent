"""The API with AUTH_PROVIDER=clerk and CLAIM_TOKEN_PROVIDER=hmac against the test database (backend review of PR 8).
Session tokens are signed by a key generated here and the JWKS client is a stub, so nothing reaches Clerk."""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from recallops import access, seed
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


def session(user: str = "user_e2e") -> dict[str, str]:
    """A Clerk session for one account, which is one company (plan v3: no organizations)."""
    now = int(time.time())
    claims: dict[str, Any] = {"sub": user, "iss": ISSUER, "azp": ORIGIN, "iat": now, "nbf": now - 5, "exp": now + 60}
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
            "SEED_CLERK_USER_ID": "user_e2e",
            "PLATFORM_OPERATOR_USER_IDS": "",
            "PLATFORM_OPERATOR_EMAILS": "",
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
            # The account signs in before seeding, which creates an empty company for it.
            early = ClerkAuthProvider(get_settings(), engine=app_engine_test).firm_context(
                ClerkPrincipal(user_id="user_e2e", firm_id=None, role="admin")
            )
            seeded = seed.run(internal_engine_test, n=3)
            with internal_engine_test.begin() as conn:
                conn.execute(
                    text("UPDATE firms SET clerk_user_id = 'user_other' WHERE id = :f"), {"f": seed.OTHER_FIRM_ID}
                )
            from recallops.api.app import create_app

            with TestClient(create_app()) as client:
                yield {"client": client, "seeded": seeded, "early_firm": early.firm_id, "db": internal_engine_test}
        finally:
            _clear()
    _clear()


def test_the_seed_takes_over_the_account_from_an_earlier_sign_in(world: dict[str, Any]) -> None:
    with world["db"].connect() as conn:
        bound = dict(conn.execute(text("SELECT id, clerk_user_id FROM firms")).all())
    assert bound[seed.DEMO_FIRM_ID] == "user_e2e"
    assert bound[world["early_firm"]] is None  # left unbound, never deleted


def test_sessions_reach_only_their_own_firm(world: dict[str, Any]) -> None:
    c, claim = world["client"], world["seeded"]["demo"]["first_claim_id"]
    assert c.get(f"/v1/claims/{claim}", headers=session()).status_code == 200
    assert c.get(f"/v1/claims/{claim}", headers=session("user_other")).status_code == 404
    assert c.get(f"/v1/claims/{claim}", headers=session("user_new")).status_code == 404  # a brand-new firm
    assert c.get("/v1/programs").status_code == 401
    dev_headers = {"X-Dev-Firm-Id": str(seed.DEMO_FIRM_ID), "X-Dev-Role": "admin"}
    assert c.get("/v1/programs", headers=dev_headers).status_code == 401  # the dev seam is off


def _issue(c: TestClient, program_id: str) -> tuple[str, str]:
    response = c.post(f"/v1/programs/{program_id}/claims/issue", json={"n": 1}, headers=session())
    assert response.status_code == 200, response.text
    link = response.json()["links"][0]
    return link["claim_id"], link["url"]


def test_signed_links_open_once_issued_and_refuse_tampering(world: dict[str, Any]) -> None:
    c, program_id = world["client"], world["seeded"]["demo"]["program_id"]
    other = session("user_other")
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
    assert c.post(f"/v1/claims/{claim_id}/link", headers=session("user_other")).status_code == 404
    assert c.post(f"/v1/claims/{uuid4()}/link", headers=session()).status_code == 404
    same = c.post(f"/v1/claims/{claim_id}/link", headers=session())  # the issuing second: same token
    CLOCK.now += 5
    response = c.post(f"/v1/claims/{claim_id}/link", headers=session())
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
    closed = c.patch(f"/v1/programs/{program_id}", json={"status": "closed"}, headers=session())
    assert closed.status_code == 200, closed.text
    assert c.get(url.replace(PORTAL, "/portal")).status_code == 404
    refused = c.post(f"/v1/claims/{claim_id}/link", headers=session())
    assert refused.status_code == 409 and "closed" in refused.json()["detail"]


def test_the_operator_by_email_has_no_company_and_a_clerk_outage_creates_none(world: dict[str, Any]) -> None:
    """Plan v3, end to end with Clerk session tokens: the account with the listed verified address is the operator and
    never a company; any other new account becomes its own company, gated until approved; while Clerk cannot be asked,
    a new account is answered 503 and nothing is created, and existing companies keep working without Clerk."""
    users = {"user_ops": "Kartikeya.Mandhar777@gmail.com", "user_e2e": "admin@demo.example"}
    calls: list[str] = []
    down = [False]

    def clerk_users(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if down[0]:
            raise httpx.ConnectTimeout("unreachable", request=request)
        user = request.url.path.rsplit("/", 1)[1]
        address = users.get(user, f"{user}@company.example")
        verified = {"id": "idn_1", "email_address": address, "verification": {"status": "verified"}}
        return httpx.Response(200, json={"primary_email_address_id": "idn_1", "email_addresses": [verified]})

    c, db = world["client"], world["db"]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ACCESS_APPROVAL", "required")
        mp.setenv("PLATFORM_OPERATOR_EMAILS", "kartikeyamandhar777@gmail.com")
        mp.setenv("CLERK_SECRET_KEY", "sk_test_e2e")
        mp.setattr("recallops.access._clerk_http", httpx.Client(transport=httpx.MockTransport(clerk_users)))
        access._clerk_cache.clear()
        get_settings.cache_clear()
        deps.build_provider.cache_clear()
        try:
            me = c.get("/v1/me", headers=session("user_ops")).json()
            assert me["operator"] is True and me["firm"] is None
            assert c.get("/v1/operator/access", headers=session("user_ops")).status_code == 200
            console = c.get("/v1/programs", headers=session("user_ops"))
            assert console.status_code == 403 and "operator" in console.json()["detail"]
            assert c.get("/v1/operator/access", headers=session("user_nobody")).status_code == 403
            stranger = c.get("/v1/me", headers=session("user_stranger")).json()
            assert stranger["operator"] is False and stranger["firm"]["onboarded"] is False
            assert stranger["firm"]["approved"] is False and stranger["firm"]["name"] == "New company"
            assert c.get("/v1/programs", headers=session("user_stranger")).status_code == 403  # gated
            assert c.get("/v1/programs", headers=session()).status_code == 200  # approved by the seed

            access._clerk_cache.clear()
            deps.build_provider.cache_clear()
            down[0] = True
            assert c.get("/v1/me", headers=session("user_fresh")).status_code == 503
            assert c.get("/v1/programs", headers=session("user_fresh")).status_code == 503
            assert c.get("/v1/operator/access", headers=session("user_ops")).status_code == 403  # fails closed
            assert c.get("/v1/me", headers=session()).status_code == 200  # an existing company needs no Clerk
            with db.connect() as conn:
                created = conn.execute(
                    text("SELECT count(*) FROM firms WHERE clerk_user_id IN ('user_ops', 'user_fresh')")
                ).scalar_one()
            assert created == 0
        finally:
            access._clerk_cache.clear()
    get_settings.cache_clear()
    deps.build_provider.cache_clear()
