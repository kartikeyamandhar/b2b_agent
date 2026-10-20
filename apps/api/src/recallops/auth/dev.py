"""Development auth provider (AUTH_PROVIDER=dev). Reads X-Dev-Firm-Id, X-Dev-Role and optional X-Dev-User-Id.

It trusts the caller completely, so it refuses to load when any Stripe or Clerk live key is configured, and
it never loads when APP_ENV=prod.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from uuid import UUID

from pydantic import SecretStr

from recallops.auth.provider import AuthError, ClaimTokenContext, NoFirmError, Principal
from recallops.logs import get_logger, log_event
from recallops.settings import Settings, is_local_uri
from recallops.tenancy import FirmContext

log = get_logger("auth.dev")

LIVE_KEY = re.compile(r"_live_")
DEV_TOKEN = re.compile(r"^dev\.([0-9a-fA-F-]{36})\.([0-9a-fA-F-]{36})$")


class UnsafeDevAuth(RuntimeError):
    pass


def live_keys_present(settings: Settings, environ: Mapping[str, str] | None = None) -> list[str]:
    """Names of Stripe or Clerk settings or environment variables holding a live key."""
    found = []
    for name, value in settings.model_dump().items():
        if not (name.startswith("stripe_") or name.startswith("clerk_")):
            continue
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value  # secrets are masked in dumps
        if isinstance(raw, str) and LIVE_KEY.search(raw):
            found.append(name)
    for name, value in (environ if environ is not None else os.environ).items():
        upper = name.upper()
        if (upper.startswith("STRIPE") or upper.startswith("CLERK")) and LIVE_KEY.search(value):
            found.append(name)
    return sorted(set(found))


class DevAuthProvider:
    name = "dev"

    def __init__(self, settings: Settings, environ: Mapping[str, str] | None = None) -> None:
        if settings.app_env == "prod":
            raise UnsafeDevAuth("the dev auth provider never runs with APP_ENV=prod")
        live = live_keys_present(settings, environ)
        if live:
            raise UnsafeDevAuth(f"refusing to load the dev auth provider: live keys present in {live}")
        if settings.neo4j_uri and not is_local_uri(settings.neo4j_uri):
            raise UnsafeDevAuth("refusing to load the dev auth provider next to a remote fraud graph (NEO4J_URI)")
        log_event(log, "dev_auth_provider_loaded", app_env=settings.app_env)

    def authenticate(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        raw_firm = lowered.get("x-dev-firm-id")
        role = lowered.get("x-dev-role", "admin")
        user = lowered.get("x-dev-user-id", "dev-user")
        if role not in ("admin", "reviewer"):
            raise AuthError("X-Dev-Role must be admin or reviewer")
        if raw_firm is None:
            return Principal(user_id=user, firm_id=None, role=None)
        try:
            firm = UUID(raw_firm)
        except ValueError as exc:
            raise AuthError("X-Dev-Firm-Id must be a UUID") from exc
        return Principal(user_id=user, firm_id=firm, role=role)  # type: ignore[arg-type]

    def firm_context(self, principal: Principal) -> FirmContext:
        if principal.firm_id is None or principal.role is None:
            raise NoFirmError("no organization: send X-Dev-Firm-Id")
        return FirmContext(firm_id=principal.firm_id, user_id=principal.user_id, role=principal.role)


class DevClaimTokenVerifier:
    """Dev portal tokens are 'dev.<firm uuid>.<claim uuid>'. Not secret, so it refuses to load with APP_ENV=prod or
    next to the Clerk login (CLAIM_TOKEN_PROVIDER=dev only in development); the login owner's HMAC verifier
    (component 4, behind ClaimTokenVerifier) replaces it."""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is not None and (settings.app_env == "prod" or settings.auth_provider == "clerk"):
            raise UnsafeDevAuth("dev claim tokens are not secret: refusing with APP_ENV=prod or AUTH_PROVIDER=clerk")

    def verify(self, token: str) -> ClaimTokenContext:
        m = DEV_TOKEN.match(token)
        if not m:
            raise AuthError("invalid claim token")
        try:
            return ClaimTokenContext(firm_id=UUID(m.group(1)), claim_id=UUID(m.group(2)))
        except ValueError as exc:
            raise AuthError("invalid claim token") from exc

    def token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, firm_id: UUID, claim_id: UUID) -> str:
        return f"dev.{firm_id}.{claim_id}"
