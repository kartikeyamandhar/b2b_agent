"""The login seam. The backend depends only on these protocols; component 4 (login owner) supplies the Clerk
implementations and registers them in auth/deps.py. Nothing else in the backend changes when they do."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from recallops.tenancy import FirmContext, Role


class AuthError(Exception):
    """Authentication failed: the API answers 401."""


class NoFirmError(Exception):
    """Authenticated user without an organization: the API answers 403 (the UI shows the create-org flow)."""


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    firm_id: UUID | None
    role: Role | None


class AuthProvider(Protocol):
    """Turns request headers into a verified Principal. Must raise AuthError on anything unverifiable."""

    name: str

    def authenticate(self, headers: dict[str, str]) -> Principal: ...

    def firm_context(self, principal: Principal) -> FirmContext:
        """Resolve the firm (the Clerk provider creates the firms row on first sight)."""
        ...


@dataclass(frozen=True, slots=True)
class ClaimTokenContext:
    claim_id: UUID


class ClaimTokenVerifier(Protocol):
    """Portal tokens (invariant 10). The login owner replaces the dev verifier with HMAC tokens."""

    def verify(self, token: str) -> ClaimTokenContext: ...

    def token_hash(self, token: str) -> str: ...
