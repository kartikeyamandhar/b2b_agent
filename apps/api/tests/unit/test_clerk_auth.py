"""Clerk session JWT verification (04-login.md sections 3 and 8) and one account = one company (plan v3). No network:
the JWKS client is a stub over a key generated here, and firm resolution is stubbed (tests/db/test_clerk_firms.py
covers it against Postgres)."""

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
from recallops.auth.provider import AuthError, AuthUnavailable, OperatorHasNoFirm
from recallops.settings import Settings

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
    def __init__(self, settings: Settings, existing: uuid.UUID | None = None) -> None:
        super().__init__(settings, jwks_client=StubJwks())
        self.existing = existing
        self.finds = 0
        self.resolved: list[str] = []

    def _find(self, user_id: str) -> uuid.UUID | None:
        self.finds += 1
        return self.existing

    def _resolve(self, user_id: str) -> uuid.UUID:
        self.resolved.append(user_id)
        return FIRM


def settings(**over: Any) -> Settings:
    return Settings(
        **{
            "auth_provider": "clerk",
            "claim_token_provider": "hmac",
            "clerk_issuer": ISSUER,
            "clerk_jwks_url": "",  # derived from the issuer, whatever the local .env holds
            "clerk_allowed_origins": ORIGIN,
            "platform_operator_user_ids": "",
            "platform_operator_emails": "",
            **over,
        }
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


def test_a_valid_token_makes_the_account_its_companys_admin(provider: StubbedClerk) -> None:
    principal = provider.authenticate(bearer(token()))
    assert isinstance(principal, ClerkPrincipal)
    assert (principal.user_id, principal.role) == ("user_abc", "admin")
    ctx = provider.firm_context(principal)
    assert (ctx.firm_id, ctx.user_id, ctx.role) == (FIRM, "user_abc", "admin")
    assert provider.resolved == ["user_abc"]


def test_organization_claims_are_ignored(provider: StubbedClerk) -> None:
    """No organizations (owner decision 2026-10-22): a token that still carries them acts for its account alone."""
    for payload in (claims(o={"id": "org_9", "rol": "org:member"}), claims(o=None, org_id="org_9", org_role="x")):
        principal = provider.authenticate(bearer(token(payload)))
        assert principal.role == "admin"
        assert provider.firm_context(principal).firm_id == FIRM


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


def _record(asked: list[str], user: str, answer: str) -> str:
    asked.append(user)
    return answer


def test_an_existing_company_is_found_without_asking_clerk_and_then_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr("recallops.auth.clerk.operator_status", lambda _s, u: _record(asked, u, "company"))
    known = StubbedClerk(settings(platform_operator_emails="ops@example.com"), existing=FIRM)
    principal = known.authenticate(bearer(token()))
    for _ in range(3):
        assert known.firm_context(principal).firm_id == FIRM
    assert (known.finds, known.resolved, asked) == (1, [], [])  # one lookup, then the cache; Clerk never asked


def test_a_new_account_is_checked_against_the_operator_once_then_gets_its_company(
    provider: StubbedClerk, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr("recallops.auth.clerk.operator_status", lambda _s, u: _record(asked, u, "company"))
    principal = provider.authenticate(bearer(token()))
    provider.firm_context(principal)
    provider.firm_context(principal)
    assert (asked, provider.resolved) == (["user_abc"], ["user_abc"])


def test_the_operator_never_gets_a_company(monkeypatch: pytest.MonkeyPatch) -> None:
    by_id = StubbedClerk(settings(platform_operator_user_ids="user_abc"))
    with pytest.raises(OperatorHasNoFirm):
        by_id.firm_context(by_id.authenticate(bearer(token())))
    monkeypatch.setattr("recallops.auth.clerk.operator_status", lambda _s, _u: "operator")
    by_email = StubbedClerk(settings())
    with pytest.raises(OperatorHasNoFirm):
        by_email.firm_context(by_email.authenticate(bearer(token())))
    assert by_id.resolved == by_email.resolved == []


def test_an_unknown_operator_answer_creates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clerk could not be asked: a new account would otherwise become a company for good, and if it is the operator's
    that is permanent (plan v3 red team). Sign-in answers 503 and creates nothing."""
    monkeypatch.setattr("recallops.auth.clerk.operator_status", lambda _s, _u: "unknown")
    stub = StubbedClerk(settings())
    with pytest.raises(AuthUnavailable):
        stub.firm_context(stub.authenticate(bearer(token())))
    assert stub.resolved == []


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
    member_claims = bearer(token(claims(o={"id": "org_123", "rol": "org:member"})))
    assert client.get("/any", headers=admin).status_code == 200
    assert client.get("/admin", headers=admin).status_code == 200
    assert client.get("/admin", headers=member_claims).status_code == 200  # organization roles no longer matter
    assert client.get("/any").status_code == 401
    assert client.get("/any", headers=bearer("not.a.jwt")).status_code == 401


def test_the_operator_and_an_unknown_answer_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()

    @app.get("/any")
    def any_member(ctx: deps.FirmDep) -> dict[str, str]:
        return {"role": ctx.role}

    operator = StubbedClerk(settings(platform_operator_user_ids="user_abc"))
    monkeypatch.setattr(deps, "build_provider", lambda: operator)
    refused = TestClient(app).get("/any", headers=bearer(token()))
    assert refused.status_code == 403 and "operator" in refused.json()["detail"]
    monkeypatch.setattr("recallops.auth.clerk.operator_status", lambda _s, _u: "unknown")
    unknown = StubbedClerk(settings())
    monkeypatch.setattr(deps, "build_provider", lambda: unknown)
    down = TestClient(app).get("/any", headers=bearer(token()))
    assert down.status_code == 503 and down.json()["detail"] == "sign-in verification unavailable"
    assert unknown.resolved == []


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
