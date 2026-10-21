"""Clerk session JWT verification (04-login.md sections 3 and 8). No network: the JWKS client is a stub over a key
generated here, and firm resolution is stubbed (tests/db/test_clerk_firms.py covers it against Postgres)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.utils import base64url_decode, base64url_encode

from recallops.auth import deps
from recallops.auth.clerk import ClerkAuthProvider, ClerkPrincipal
from recallops.auth.provider import AuthError, NoFirmError
from recallops.settings import Settings
from recallops.tenancy import Role

ISSUER = "https://test-instance.clerk.accounts.dev"
ORIGIN = "http://localhost:3000"
KID = "ins_test_key"
FIRM = uuid.UUID("00000000-0000-4000-8000-0000000000a1")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _SigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class StubJwks:
    """Behaves like jwt.PyJWKClient: one known kid, PyJWKClientError for any other."""

    def __init__(self) -> None:
        self.calls = 0

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        self.calls += 1
        kid = jwt.get_unverified_header(token).get("kid")
        if kid != KID:
            raise jwt.PyJWKClientError(f'Unable to find a signing key that matches: "{kid}"')
        return _SigningKey(KEY.public_key())


class StubbedClerk(ClerkAuthProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings, jwks_client=StubJwks())
        self.resolved: list[tuple[str, str, Role]] = []

    def _resolve(self, org_id: str, org_slug: str | None, user_id: str, role: Role) -> uuid.UUID:
        self.resolved.append((org_id, user_id, role))
        return FIRM


def settings() -> Settings:
    return Settings(
        auth_provider="clerk",
        claim_token_provider="hmac",
        clerk_issuer=ISSUER,
        clerk_jwks_url="",  # derived from the issuer, whatever the local .env holds
        clerk_allowed_origins=ORIGIN,
    )


def claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {
        "sub": "user_abc",
        "iss": ISSUER,
        "azp": ORIGIN,
        "iat": now,
        "nbf": now - 5,
        "exp": now + 60,
        "sid": "sess_1",
        "v": 2,
        "o": {"id": "org_123", "rol": "admin", "slg": "acme-recalls"},
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def token(payload: dict[str, Any] | None = None, key: Any = KEY, kid: str = KID, alg: str = "RS256") -> str:
    return jwt.encode(payload if payload is not None else claims(), key, algorithm=alg, headers={"kid": kid})


def bearer(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def provider() -> StubbedClerk:
    return StubbedClerk(settings())


def test_a_valid_token_names_the_user_org_and_role(provider: StubbedClerk) -> None:
    principal = provider.authenticate(bearer(token()))
    assert isinstance(principal, ClerkPrincipal)
    assert (principal.user_id, principal.org_id, principal.role, principal.org_slug) == (
        "user_abc",
        "org_123",
        "admin",
        "acme-recalls",
    )
    ctx = provider.firm_context(principal)
    assert (ctx.firm_id, ctx.user_id, ctx.role) == (FIRM, "user_abc", "admin")


def test_version_one_org_claims_and_the_org_prefix_are_read(provider: StubbedClerk) -> None:
    v1 = claims(o=None, org_id="org_9", org_role="org:reviewer", org_slug="nine")
    principal = provider.authenticate(bearer(token(v1)))
    assert isinstance(principal, ClerkPrincipal)
    assert (principal.org_id, principal.role) == ("org_9", "reviewer")


@pytest.mark.parametrize(
    "case",
    ["expired", "not_yet_valid", "wrong_issuer", "wrong_azp", "unknown_kid", "tampered", "wrong_key", "hs256", "none"],
)
def test_bad_tokens_are_refused(provider: StubbedClerk, case: str) -> None:
    now = int(time.time())
    if case == "expired":
        tok = token(claims(exp=now - 60, nbf=now - 120, iat=now - 120))
    elif case == "not_yet_valid":
        tok = token(claims(nbf=now + 120))
    elif case == "wrong_issuer":
        tok = token(claims(iss="https://evil.clerk.accounts.dev"))
    elif case == "wrong_azp":
        tok = token(claims(azp="https://evil.example"))
    elif case == "unknown_kid":
        tok = token(kid="ins_other")
    elif case == "tampered":
        head, body, sig = token().split(".")
        forged = base64url_encode(base64url_decode(body).replace(b'"admin"', b'"admim"')).decode()
        tok = f"{head}.{forged}.{sig}"
    elif case == "wrong_key":
        tok = token(key=OTHER_KEY)
    elif case == "hs256":
        tok = token(key="a-shared-secret-that-is-long-enough-for-hs256", alg="HS256")
    else:
        tok = token(key=None, alg="none")
    with pytest.raises(AuthError):
        provider.authenticate(bearer(tok))


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}])
def test_a_missing_bearer_is_refused(provider: StubbedClerk, headers: dict[str, str]) -> None:
    with pytest.raises(AuthError):
        provider.authenticate(headers)


def test_required_claims_must_be_present(provider: StubbedClerk) -> None:
    for missing in ("exp", "nbf", "sub"):
        with pytest.raises(AuthError):
            provider.authenticate(bearer(token(claims(**{missing: None}))))


def test_no_organization_or_an_unknown_role_means_no_firm(provider: StubbedClerk) -> None:
    for payload in (claims(o=None), claims(o={"id": "org_123", "rol": "owner"})):
        principal = provider.authenticate(bearer(token(payload)))
        with pytest.raises(NoFirmError):
            provider.firm_context(principal)
    assert provider.resolved == []


def test_the_firm_is_cached_per_org_user_and_role(provider: StubbedClerk) -> None:
    principal = provider.authenticate(bearer(token()))
    provider.firm_context(principal)
    provider.firm_context(principal)
    assert len(provider.resolved) == 1
    reviewer = provider.authenticate(bearer(token(claims(o={"id": "org_123", "rol": "org:reviewer"}))))
    provider.firm_context(reviewer)
    assert provider.resolved[-1] == ("org_123", "user_abc", "reviewer")


def test_the_provider_needs_an_issuer() -> None:
    with pytest.raises(ValueError, match="CLERK_ISSUER"):
        ClerkAuthProvider(Settings(auth_provider="clerk", clerk_issuer=""), jwks_client=StubJwks())


@pytest.fixture()
def client(provider: StubbedClerk, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(deps, "build_provider", lambda: provider)
    app = FastAPI()

    @app.get("/any")
    def any_member(ctx: deps.FirmDep) -> dict[str, str]:
        return {"role": ctx.role}

    @app.get("/admin")
    def admin_only(ctx: deps.AdminDep) -> dict[str, str]:
        return {"role": ctx.role}

    return TestClient(app)


def test_role_guard_matrix_over_http(client: TestClient) -> None:
    admin = bearer(token())
    reviewer = bearer(token(claims(o={"id": "org_123", "rol": "org:reviewer"})))
    no_org = bearer(token(claims(o=None)))
    assert client.get("/any", headers=admin).status_code == 200
    assert client.get("/admin", headers=admin).status_code == 200
    assert client.get("/any", headers=reviewer).status_code == 200
    assert client.get("/admin", headers=reviewer).status_code == 403
    assert client.get("/any", headers=no_org).status_code == 403
    assert client.get("/any").status_code == 401
    assert client.get("/any", headers=bearer("not.a.jwt")).status_code == 401


class DownJwks:
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url, err: timed out")


def test_a_keys_outage_answers_503_not_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 tells the UI to sign the user out; a Clerk JWKS outage is ours to retry (backend review of PR 8)."""
    down = ClerkAuthProvider(settings(), jwks_client=DownJwks())
    monkeypatch.setattr(deps, "build_provider", lambda: down)
    app = FastAPI()

    @app.get("/any")
    def any_member(ctx: deps.FirmDep) -> dict[str, str]:
        return {"role": ctx.role}

    response = TestClient(app).get("/any", headers=bearer(token()))
    assert response.status_code == 503
    assert response.json()["detail"] == "sign-in verification unavailable"
    assert "timed out" not in response.text


def test_a_member_role_is_told_why_and_never_resolves_a_firm(client: TestClient, provider: StubbedClerk) -> None:
    member = bearer(token(claims(o={"id": "org_123", "rol": "org:member"})))
    no_org = bearer(token(claims(o=None)))
    member_answer, no_org_answer = client.get("/any", headers=member), client.get("/any", headers=no_org)
    assert member_answer.status_code == no_org_answer.status_code == 403
    assert "org:admin or org:reviewer" in member_answer.json()["detail"]
    assert "no active organization" in no_org_answer.json()["detail"]
    assert provider.resolved == []
