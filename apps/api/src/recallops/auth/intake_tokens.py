"""One public claim link per product (owner decision 2026-10-22; invariant 10 amended).

Token = base64url(firm_id 16 bytes | program_id 16 bytes | version 4 bytes, big-endian | HMAC-SHA256 of the first 36
bytes), 68 bytes, 91 characters, no padding. The MAC key is derived from CLAIM_TOKEN_SECRET under its own label, so an
intake token can never be read as a claim token or the other way round, and the length differs from a claim token's 96
characters. The firm id is inside the signed part, so the portal opens that firm's tenant session directly (invariant
2). The link has no expiry; replacing it (recall_programs.intake_version + 1) or closing the product stops it.
Development uses 'intake.<firm>.<program>.<version>', which is not secret and refuses to load in prod or with Clerk.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from recallops.auth.hmac_tokens import MIN_SECRET_BYTES, UnsafeClaimTokenSecret, _decode_secret
from recallops.auth.provider import AuthError
from recallops.settings import EXAMPLE_CLAIM_TOKEN_SECRET, Settings

LABEL = b"recallops/intake/v1"
PAYLOAD_BYTES = 36
MAC_BYTES = 32
TOKEN_CHARS = 91
DEV_TOKEN = re.compile(r"^intake\.([0-9a-f-]{36})\.([0-9a-f-]{36})\.([1-9][0-9]{0,8})$")


@dataclass(frozen=True)
class IntakeTarget:
    firm_id: UUID
    program_id: UUID
    version: int


class IntakeTokens(Protocol):
    def issue(self, firm_id: UUID, program_id: UUID, version: int) -> str: ...

    def verify(self, token: str) -> IntakeTarget: ...


class HmacIntakeTokens:
    def __init__(self, settings: Settings) -> None:
        secret = _decode_secret(settings.claim_token_secret.get_secret_value())
        if hmac.compare_digest(secret, base64.b64decode(EXAMPLE_CLAIM_TOKEN_SECRET)):
            raise UnsafeClaimTokenSecret("CLAIM_TOKEN_SECRET is the example value; generate 32 random bytes")
        if len(secret) < MIN_SECRET_BYTES:
            raise UnsafeClaimTokenSecret(f"CLAIM_TOKEN_SECRET must decode to at least {MIN_SECRET_BYTES} bytes")
        self._key = hmac.new(secret, LABEL, hashlib.sha256).digest()

    def _mac(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    def issue(self, firm_id: UUID, program_id: UUID, version: int) -> str:
        payload = firm_id.bytes + program_id.bytes + version.to_bytes(4, "big")
        return base64.urlsafe_b64encode(payload + self._mac(payload)).rstrip(b"=").decode("ascii")

    def verify(self, token: str) -> IntakeTarget:
        if len(token) != TOKEN_CHARS:
            raise AuthError("invalid claim link")
        try:
            raw = base64.urlsafe_b64decode(token + "=")
        except (binascii.Error, ValueError) as exc:
            raise AuthError("invalid claim link") from exc
        if len(raw) != PAYLOAD_BYTES + MAC_BYTES or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != token:
            raise AuthError("invalid claim link")  # one spelling per link
        payload, mac = raw[:PAYLOAD_BYTES], raw[PAYLOAD_BYTES:]
        if not hmac.compare_digest(mac, self._mac(payload)):
            raise AuthError("invalid claim link")
        version = int.from_bytes(payload[32:], "big")
        if version < 1:
            raise AuthError("invalid claim link")
        return IntakeTarget(UUID(bytes=payload[:16]), UUID(bytes=payload[16:32]), version)


class DevIntakeTokens:
    def __init__(self, settings: Settings) -> None:
        if settings.app_env == "prod" or settings.auth_provider == "clerk":
            raise UnsafeClaimTokenSecret("dev claim links are not secret: refusing with APP_ENV=prod or Clerk")

    def issue(self, firm_id: UUID, program_id: UUID, version: int) -> str:
        return f"intake.{firm_id}.{program_id}.{version}"

    def verify(self, token: str) -> IntakeTarget:
        m = DEV_TOKEN.match(token)
        if not m:
            raise AuthError("invalid claim link")
        try:
            return IntakeTarget(UUID(m.group(1)), UUID(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise AuthError("invalid claim link") from exc


def build_intake_tokens(settings: Settings) -> IntakeTokens:
    if settings.claim_token_provider == "dev":
        return DevIntakeTokens(settings)
    return HmacIntakeTokens(settings)
