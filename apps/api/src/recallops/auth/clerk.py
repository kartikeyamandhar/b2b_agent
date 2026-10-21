"""Clerk auth provider (AUTH_PROVIDER=clerk, component 4).

Clerk hosts sign-up and sign-in (email and password, with an emailed verification code at sign-up) and issues a
short-lived RS256 session JWT. This provider verifies that JWT against the instance's JWKS and turns its organization
claims into a FirmContext. The firm is the Clerk organization: its firms row is created on first sight by the
SECURITY DEFINER function recallops_firm_for_clerk_org (migration 0007), because the API role never bypasses RLS.

Token shape (Clerk session token v2): sub (user id), iss (Frontend API URL), azp (origin that requested the token),
exp, nbf, and o = {id, rol, slg} for the active organization. Version 1 tokens carried org_id, org_role ("org:admin")
and org_slug instead; both are read, and the "org:" role prefix is stripped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any
from uuid import UUID

import jwt
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from recallops.auth.provider import AuthError, NoFirmError, Principal
from recallops.db.engine import app_engine
from recallops.db.session import set_firm
from recallops.logs import get_logger, log_event
from recallops.settings import Settings
from recallops.tenancy import FirmContext, Role

log = get_logger("auth.clerk")

ALGORITHMS = ["RS256"]
JWKS_LIFESPAN_S = 3600  # 04-login.md section 3: cache the key set 1 hour, refetch on an unknown kid
JWKS_TIMEOUT_S = 5
FIRM_CACHE_TTL_S = 300
ROLES: dict[str, Role] = {"admin": "admin", "reviewer": "reviewer"}

RESOLVE_FIRM_SQL = text("SELECT recallops_firm_for_clerk_org(:org_id, :org_name)")
MIRROR_MEMBER_SQL = text(
    "INSERT INTO firm_members (firm_id, clerk_user_id, role) VALUES (:firm_id, :user_id, :role) "
    "ON CONFLICT (firm_id, clerk_user_id) DO UPDATE SET role = EXCLUDED.role "
    "WHERE firm_members.role IS DISTINCT FROM EXCLUDED.role"
)


@dataclass(frozen=True, slots=True)
class ClerkPrincipal(Principal):
    """A Principal that also names the Clerk organization, so firm_context can resolve it."""

    org_id: str | None = None
    org_slug: str | None = None


def _role(raw: object) -> Role | None:
    if not isinstance(raw, str):
        return None
    return ROLES.get(raw.removeprefix("org:"))


class ClerkAuthProvider:
    name = "clerk"

    def __init__(self, settings: Settings, jwks_client: Any = None, engine: Engine | None = None) -> None:
        if not settings.clerk_issuer:
            raise ValueError("AUTH_PROVIDER=clerk needs CLERK_ISSUER (the instance's Frontend API URL)")
        self._issuer = settings.clerk_issuer.rstrip("/")
        self._origins = frozenset(o.strip().rstrip("/") for o in settings.clerk_allowed_origins.split(",") if o.strip())
        self._jwks = jwks_client or jwt.PyJWKClient(
            settings.clerk_jwks_url, cache_jwk_set=True, lifespan=JWKS_LIFESPAN_S, timeout=JWKS_TIMEOUT_S
        )
        self._engine = engine
        self._firms: dict[tuple[str, str, str], tuple[UUID, float]] = {}
        self._lock = Lock()
        log_event(log, "clerk_auth_provider_loaded", issuer=self._issuer)

    def authenticate(self, headers: dict[str, str]) -> Principal:
        lowered = {k.lower(): v for k, v in headers.items()}
        scheme, _, token = lowered.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("missing bearer token")
        claims = self._verify(token.strip())
        user = claims.get("sub")
        if not isinstance(user, str) or not user:
            raise AuthError("token has no subject")
        org = claims.get("o")
        if isinstance(org, dict):
            org_id, role, slug = org.get("id"), _role(org.get("rol")), org.get("slg")
        else:
            org_id, role, slug = claims.get("org_id"), _role(claims.get("org_role")), claims.get("org_slug")
        if not isinstance(org_id, str) or not org_id or role is None:
            return ClerkPrincipal(user_id=user, firm_id=None, role=None)
        return ClerkPrincipal(
            user_id=user, firm_id=None, role=role, org_id=org_id, org_slug=slug if isinstance(slug, str) else None
        )

    def _verify(self, token: str) -> dict[str, Any]:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                issuer=self._issuer,
                options={"require": ["exp", "nbf", "iat", "iss", "sub"], "verify_aud": False},
                leeway=5,
            )
        except (jwt.PyJWTError, ValueError) as exc:  # PyJWKClientError is a PyJWTError
            log_event(log, "clerk_token_rejected", reason=type(exc).__name__)
            raise AuthError("invalid session token") from exc
        azp = claims.get("azp")
        if azp is not None and (not isinstance(azp, str) or azp.rstrip("/") not in self._origins):
            log_event(log, "clerk_token_rejected", reason="azp")
            raise AuthError("invalid session token")
        return claims

    def firm_context(self, principal: Principal) -> FirmContext:
        if not isinstance(principal, ClerkPrincipal) or principal.org_id is None or principal.role is None:
            raise NoFirmError("no active organization: create or select one")
        key = (principal.org_id, principal.user_id, principal.role)
        now = time.monotonic()
        with self._lock:
            cached = self._firms.get(key)
        if cached is not None and cached[1] > now:
            return FirmContext(firm_id=cached[0], user_id=principal.user_id, role=principal.role)
        firm_id = self._resolve(principal.org_id, principal.org_slug, principal.user_id, principal.role)
        with self._lock:
            self._firms[key] = (firm_id, now + FIRM_CACHE_TTL_S)
        return FirmContext(firm_id=firm_id, user_id=principal.user_id, role=principal.role)

    def _resolve(self, org_id: str, org_slug: str | None, user_id: str, role: Role) -> UUID:
        """Find or create the firm for the organization, then mirror the member's role (audit joins only; the
        role that authorizes is always the token's). Runs as recallops_app: the function finds the firm, and the
        member row is written under the firm's own RLS context."""
        with Session(self._engine or app_engine(), expire_on_commit=False) as session, session.begin():
            firm_id = session.execute(RESOLVE_FIRM_SQL, {"org_id": org_id, "org_name": org_slug or org_id}).scalar_one()
            if not isinstance(firm_id, UUID):
                raise NoFirmError("organization could not be resolved")
            set_firm(session, firm_id)
            session.execute(MIRROR_MEMBER_SQL, {"firm_id": firm_id, "user_id": user_id, "role": role})
        log_event(log, "clerk_firm_resolved", firm_id=str(firm_id))
        return firm_id
