"""Regressions for the backend review of PR 8: login settings, the claim-link secret, and the deploy blueprint."""

from __future__ import annotations

import base64
from typing import Any

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from recallops.api.app import create_app  # imported with the default settings; the test builds a second app
from recallops.auth import deps
from recallops.auth.hmac_tokens import HmacClaimTokenVerifier, UnsafeClaimTokenSecret
from recallops.settings import EXAMPLE_CLAIM_TOKEN_SECRET, REPO_ROOT, Settings, get_settings

ISSUER = "https://test-instance.clerk.accounts.dev"
DEPLOYED = {"portal_base_url": "https://recallops.example/claim"}


def _clerk(**values: Any) -> Settings:
    base = {"auth_provider": "clerk", "clerk_issuer": ISSUER, "clerk_jwks_url": ""}
    return Settings.model_validate({**base, **values})


def _prod(**values: Any) -> Settings:
    return Settings.model_validate({"app_env": "prod", "neo4j_uri": "", **DEPLOYED, **values})


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("localhost:3000/claim", "http\\(s\\) URL with a host"),
        ("ftp://recallops.example/claim", "http\\(s\\) URL with a host"),
        ("https://user:pw@recallops.example/claim", "credentials"),
        ("https://recallops.example/claim?x=1", "query"),
        ("https://recallops.example/claim#top", "fragment"),
    ],
)
def test_a_portal_base_url_that_would_garble_or_leak_links_is_refused(url: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(portal_base_url=url)


@pytest.mark.parametrize("url", ["http://recallops.example/claim", "https://localhost:3000/claim"])
def test_prod_links_point_at_the_deployed_https_site(url: str) -> None:
    with pytest.raises(ValidationError, match="deployed https site"):
        _prod(portal_base_url=url)
    assert _prod().portal_base_url == DEPLOYED["portal_base_url"]


def test_the_keys_url_is_derived_from_the_issuer_and_must_share_its_host() -> None:
    assert _clerk().clerk_keys_url() == f"{ISSUER}/.well-known/jwks.json"
    assert _clerk(clerk_jwks_url=f"{ISSUER}/v1/jwks").clerk_keys_url() == f"{ISSUER}/v1/jwks"
    with pytest.raises(ValidationError, match="CLERK_ISSUER host"):
        _clerk(clerk_jwks_url="https://attacker.example/.well-known/jwks.json")
    with pytest.raises(ValidationError, match="CLERK_JWKS_URL must be an https URL"):
        _clerk(clerk_jwks_url="http://test-instance.clerk.accounts.dev/.well-known/jwks.json")
    with pytest.raises(ValidationError, match="CLERK_ISSUER must be an https URL"):
        _clerk(clerk_issuer="http://test-instance.clerk.accounts.dev")
    assert (
        _clerk(clerk_issuer="http://localhost:8800").clerk_keys_url() == "http://localhost:8800/.well-known/jwks.json"
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"clerk_issuer": "https://example.clerk.accounts.dev"}, "real Clerk instance"),
        ({"clerk_issuer": "http://localhost:8800"}, "real Clerk instance"),
        ({"clerk_allowed_origins": ""}, "CLERK_ALLOWED_ORIGINS"),
        ({"clerk_allowed_origins": "https://recallops.example,http://localhost:3000"}, "CLERK_ALLOWED_ORIGINS"),
    ],
)
def test_prod_clerk_settings_must_be_real(values: dict[str, Any], message: str) -> None:
    clerk = {"auth_provider": "clerk", "clerk_issuer": ISSUER, "clerk_jwks_url": ""}
    with pytest.raises(ValidationError, match=message):
        _prod(**{**clerk, "clerk_allowed_origins": "https://recallops.example", **values})
    assert _prod(**clerk, clerk_allowed_origins="https://recallops.example").auth_provider == "clerk"


def test_login_errors_and_repr_never_show_the_secrets() -> None:
    secret = base64.b64encode(b"s" * 32).decode()
    settings = Settings(claim_token_secret=SecretStr(secret), clerk_secret_key=SecretStr("sk_test_never_printed"))
    assert secret not in repr(settings) and "sk_test_never_printed" not in repr(settings)
    with pytest.raises(ValidationError) as caught:
        Settings(claim_token_ttl_days=91, portal_base_url="https://user:hunter2@x.example/claim")
    assert "hunter2" not in str(caught.value)


def test_links_cannot_outlive_invariant_10() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 90"):
        Settings(claim_token_ttl_days=91)


def _variants(canonical: str) -> list[str]:
    """Spellings of the same key: the last character before '=' carries 2 unused low bits."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    head, last = canonical[:-2], alphabet.index(canonical[-2])
    return [head + alphabet[(last & ~3) | low] + "=" for low in range(4)]


def test_every_spelling_of_the_example_secret_is_refused() -> None:
    spellings = _variants(EXAMPLE_CLAIM_TOKEN_SECRET)
    assert len(set(spellings)) == 4 and {base64.b64decode(s) for s in spellings} == {
        base64.b64decode(EXAMPLE_CLAIM_TOKEN_SECRET)
    }
    for spelling in spellings:
        with pytest.raises(UnsafeClaimTokenSecret, match="example value"):
            HmacClaimTokenVerifier(Settings(claim_token_secret=SecretStr(spelling)))


def test_the_blueprint_keeps_tokens_out_of_logs_and_asks_for_the_portal_url() -> None:
    blueprint = yaml.safe_load((REPO_ROOT / "infra" / "render.yaml").read_text(encoding="utf-8"))
    web = next(s for s in blueprint["services"] if s["type"] == "web")
    assert "--no-access-log" in web["startCommand"]  # a portal path is a bearer token
    for service in blueprint["services"]:  # every service loads prod settings, which refuse the localhost default
        env = {v["key"]: v for v in service["envVars"]}
        assert env["PORTAL_BASE_URL"].get("sync") is False, service["name"]


def test_the_api_will_not_start_with_the_example_link_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "AUTH_PROVIDER": "clerk",
        "CLAIM_TOKEN_PROVIDER": "hmac",
        "CLERK_ISSUER": ISSUER,
        "CLERK_JWKS_URL": "",
        "CLAIM_TOKEN_SECRET": EXAMPLE_CLAIM_TOKEN_SECRET,
    }.items():
        monkeypatch.setenv(key, value)
    caches = (get_settings, deps.build_provider, deps.build_claim_verifier)
    for cache in caches:
        cache.cache_clear()
    try:
        with pytest.raises(UnsafeClaimTokenSecret, match="example value"):
            create_app()  # the keys are fetched lazily, so nothing here reaches Clerk
    finally:
        monkeypatch.undo()
        for cache in caches:
            cache.cache_clear()


def test_the_prod_api_needs_the_web_proxy_secret() -> None:
    """Without it, every claimant reaches the API as the web server and shares one rate-limit bucket (PR 9)."""
    from recallops.api.app import require_web_proxy_secret

    with pytest.raises(RuntimeError, match="WEB_PROXY_SECRET"):
        require_web_proxy_secret(Settings())
    with pytest.raises(RuntimeError, match="WEB_PROXY_SECRET"):
        require_web_proxy_secret(Settings(web_proxy_secret=SecretStr("short")))
    require_web_proxy_secret(Settings(web_proxy_secret=SecretStr("x" * 32)))
    assert "x" * 32 not in repr(Settings(web_proxy_secret=SecretStr("x" * 32)))
