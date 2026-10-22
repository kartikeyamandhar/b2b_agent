"""Clerk auth provider (AUTH_PROVIDER=clerk, component 4).

Clerk hosts sign-up and sign-in and issues a short-lived RS256 session JWT. This provider verifies that JWT against
the instance's JWKS and turns it into a FirmContext. One account is one company (owner decision 2026-10-22: no
organizations): the firm is found by the token's `sub` and created on first sight by the SECURITY DEFINER function
recallops_firm_for_clerk_user (migration 0009), because the API role never bypasses RLS. The account acts as its
company's admin. The platform operator approves companies and never gets one; when Clerk cannot say whether an account
is the operator, sign-in answers 503 and creates nothing.

Token shape (Clerk session token v2): sub (user id), iss (Frontend API URL), azp (origin that requested the token),
exp, nbf. Organization claims, if a token still carries them, are ignored.
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

from recallops.access import operator_status
from recallops.auth.provider import AuthError, AuthUnavailable, NoFirmError, OperatorHasNoFirm, Principal
from recallops.db.engine import app_engine
from recallops.db.session import set_firm
from recallops.logs import get_logger, log_event
from recallops.settings import Settings
from recallops.tenancy import FirmContext

log = get_logger("auth.clerk")

ALGORITHMS = ["RS256"]
JWKS_LIFESPAN_S = 3600  # 04-login.md section 3: cache the key set 1 hour, refetch on an unknown kid
JWKS_TIMEOUT_S = 3  # a slow JWKS holds PyJWKClient's lock; keep the worst wait short (backend review of PR 8)
FIRM_CACHE_TTL_S = 300
FIRM_CACHE_MAX = 10_000
NEW_FIRM_NAME = "New company"  # the company names itself in onboarding (POST /v1/onboarding)

FIND_FIRM_SQL = text("SELECT recallops_firm_of_clerk_user(:user_id)")
RESOLVE_FIRM_SQL = text("SELECT recallops_firm_for_clerk_user(:user_id, :name)")
MIRROR_MEMBER_SQL = text(
    "INSERT INTO firm_members (firm_id, clerk_user_id, role) VALUES (:firm_id, :user_id, :role) "
    "ON CONFLICT (firm_id, clerk_user_id) DO UPDATE SET role = EXCLUDED.role "
    "WHERE firm_members.role IS DISTINCT FROM EXCLUDED.role"
)


@dataclass(frozen=True, slots=True)
class ClerkPrincipal(Principal):
    """A verified Clerk session: the account is its company's admin."""


class ClerkAuthProvider:
    name = "clerk"

    def __init__(self, settings: Settings, jwks_client: Any = None, engine: Engine | None = None) -> None:
        if not settings.clerk_issuer:
            raise ValueError("AUTH_PROVIDER=clerk needs CLERK_ISSUER (the instance's Frontend API URL)")
        self._issuer = settings.clerk_issuer.rstrip("/")
        self._origins = frozenset(o.strip().rstrip("/") for o in settings.clerk_allowed_origins.split(",") if o.strip())
        self._jwks = jwks_client or jwt.PyJWKClient(
            settings.clerk_keys_url(), cache_jwk_set=True, lifespan=JWKS_LIFESPAN_S, timeout=JWKS_TIMEOUT_S
        )
        self._engine = engine
        self._settings = settings
        self._firms: dict[str, tuple[UUID, float]] = {}
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
        return ClerkPrincipal(user_id=user, firm_id=None, role="admin")

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
        except jwt.PyJWKClientConnectionError as exc:  # Clerk unreachable: an outage, not a bad token
            log_event(log, "clerk_jwks_unavailable", reason=type(exc).__name__)
            raise AuthUnavailable("sign-in verification unavailable") from exc
        except (jwt.PyJWTError, ValueError) as exc:  # PyJWKClientError is a PyJWTError
            log_event(log, "clerk_token_rejected", reason=type(exc).__name__)
            raise AuthError("invalid session token") from exc
        azp = claims.get("azp")
        if azp is not None and (not isinstance(azp, str) or azp.rstrip("/") not in self._origins):
            log_event(log, "clerk_token_rejected", reason="azp")
            raise AuthError("invalid session token")
        return claims

    def firm_context(self, principal: Principal) -> FirmContext:
        """The account's company, created at its first sign-in. Order matters: a company that already exists costs no
        Clerk call; only a new account is checked against the operator, and an unknown answer creates nothing."""
        if not isinstance(principal, ClerkPrincipal):
            raise NoFirmError("not a Clerk session")
        user_id = principal.user_id
        now = time.monotonic()
        with self._lock:
            cached = self._firms.get(user_id)
        if cached is not None and cached[1] > now:
            return FirmContext(firm_id=cached[0], user_id=user_id, role="admin")
        if user_id in self._settings.operator_ids():
            raise OperatorHasNoFirm("the platform operator has no company: use the operator window")
        firm_id = self._find(user_id)
        if firm_id is None:
            status = operator_status(self._settings, user_id)
            if status == "operator":
                raise OperatorHasNoFirm("the platform operator has no company: use the operator window")
            if status == "unknown":
                raise AuthUnavailable("sign-in verification unavailable")
            firm_id = self._resolve(user_id)
        with self._lock:
            if len(self._firms) >= FIRM_CACHE_MAX:
                self._firms.clear()
            self._firms[user_id] = (firm_id, now + FIRM_CACHE_TTL_S)
        return FirmContext(firm_id=firm_id, user_id=user_id, role="admin")

    def _find(self, user_id: str) -> UUID | None:
        with Session(self._engine or app_engine()) as session, session.begin():
            found = session.execute(FIND_FIRM_SQL, {"user_id": user_id}).scalar_one_or_none()
        return found if isinstance(found, UUID) else None

    def _resolve(self, user_id: str) -> UUID:
        """Create (or find, when two first requests race) the account's company, then mirror the member row (audit
        joins only). Runs as recallops_app: the function writes the firm; the member row is written under the firm's
        own RLS context."""
        with Session(self._engine or app_engine(), expire_on_commit=False) as session, session.begin():
            firm_id = session.execute(RESOLVE_FIRM_SQL, {"user_id": user_id, "name": NEW_FIRM_NAME}).scalar_one()
            if not isinstance(firm_id, UUID):
                raise NoFirmError("the account could not be resolved")
            set_firm(session, firm_id)
            session.execute(MIRROR_MEMBER_SQL, {"firm_id": firm_id, "user_id": user_id, "role": "admin"})
        log_event(log, "clerk_firm_resolved", firm_id=str(firm_id), created=True)
        return firm_id
