"""FastAPI dependencies: current_user, current_firm, require_role, and the portal claim token.

The provider is chosen once from AUTH_PROVIDER: dev (X-Dev-* headers) or clerk (component 4, a verified Clerk
session JWT). Route code only ever sees FirmContext, so nothing else changes.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from recallops.auth.clerk import ClerkAuthProvider
from recallops.auth.dev import DevAuthProvider, DevClaimTokenVerifier
from recallops.auth.hmac_tokens import HmacClaimTokenVerifier
from recallops.auth.provider import (
    AuthError,
    AuthProvider,
    AuthUnavailable,
    ClaimTokenContext,
    ClaimTokenVerifier,
    NoFirmError,
    Principal,
)
from recallops.settings import get_settings
from recallops.tenancy import FirmContext, Role


@lru_cache(maxsize=1)
def build_provider() -> AuthProvider:
    settings = get_settings()
    if settings.auth_provider == "dev":
        return DevAuthProvider(settings)
    return ClerkAuthProvider(settings)


@lru_cache(maxsize=1)
def build_claim_verifier() -> ClaimTokenVerifier:
    settings = get_settings()
    if settings.claim_token_provider == "dev":
        return DevClaimTokenVerifier(settings)
    return HmacClaimTokenVerifier(settings)


def current_user(request: Request) -> Principal:
    try:
        return build_provider().authenticate(dict(request.headers))
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except AuthUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "sign-in verification unavailable") from exc


def current_firm(principal: Annotated[Principal, Depends(current_user)]) -> FirmContext:
    try:
        return build_provider().firm_context(principal)
    except NoFirmError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


def require_role(role: Role) -> Callable[[FirmContext], FirmContext]:
    def guard(ctx: Annotated[FirmContext, Depends(current_firm)]) -> FirmContext:
        if role == "admin" and ctx.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
        return ctx

    return guard


def claim_token(token: str) -> ClaimTokenContext:
    try:
        return build_claim_verifier().verify(token)
    except AuthError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "claim link not found or expired") from exc


FirmDep = Annotated[FirmContext, Depends(current_firm)]
AdminDep = Annotated[FirmContext, Depends(require_role("admin"))]
ClaimTokenDep = Annotated[ClaimTokenContext, Depends(claim_token)]
