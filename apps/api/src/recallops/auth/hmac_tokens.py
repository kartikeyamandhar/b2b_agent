"""Signed claim-link tokens (CLAIM_TOKEN_PROVIDER=hmac, invariant 10, 04-login.md section 5).

Token = base64url(firm_id 16 bytes | claim_id 16 bytes | expiry 8 bytes, big-endian unix seconds | HMAC-SHA256 of
the first 40 bytes under CLAIM_TOKEN_SECRET), 72 bytes, 96 characters, no padding. The firm id is inside the signed
part so the portal opens a tenant session directly. A closed program kills its links in portal.resolve, and the
claim stores only sha256(token), so a leaked database row cannot be turned back into a link.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time
from collections.abc import Callable
from uuid import UUID

from recallops.auth.provider import AuthError, ClaimTokenContext
from recallops.settings import EXAMPLE_CLAIM_TOKEN_SECRET, Settings

PAYLOAD_BYTES = 40
MAC_BYTES = 32
TOKEN_BYTES = PAYLOAD_BYTES + MAC_BYTES
MIN_SECRET_BYTES = 32


class UnsafeClaimTokenSecret(RuntimeError):
    pass


def _decode_secret(raw: str) -> bytes:
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnsafeClaimTokenSecret("CLAIM_TOKEN_SECRET must be base64") from exc


class HmacClaimTokenVerifier:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.time) -> None:
        if settings.claim_token_secret == EXAMPLE_CLAIM_TOKEN_SECRET:
            raise UnsafeClaimTokenSecret("CLAIM_TOKEN_SECRET is the example value; generate 32 random bytes")
        secret = _decode_secret(settings.claim_token_secret)
        if len(secret) < MIN_SECRET_BYTES:
            raise UnsafeClaimTokenSecret(f"CLAIM_TOKEN_SECRET must decode to at least {MIN_SECRET_BYTES} bytes")
        self._secret = secret
        self._ttl_s = settings.claim_token_ttl_days * 86400
        self._clock = clock

    def _mac(self, payload: bytes) -> bytes:
        return hmac.new(self._secret, payload, hashlib.sha256).digest()

    def issue(self, firm_id: UUID, claim_id: UUID) -> str:
        expiry = int(self._clock()) + self._ttl_s
        payload = firm_id.bytes + claim_id.bytes + expiry.to_bytes(8, "big")
        return base64.urlsafe_b64encode(payload + self._mac(payload)).rstrip(b"=").decode("ascii")

    def verify(self, token: str) -> ClaimTokenContext:
        if len(token) != 96:  # 72 bytes in unpadded base64url
            raise AuthError("invalid claim token")
        try:
            raw = base64.urlsafe_b64decode(token + "==")
        except (binascii.Error, ValueError) as exc:
            raise AuthError("invalid claim token") from exc
        if len(raw) != TOKEN_BYTES or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != token:
            raise AuthError("invalid claim token")  # reject non-canonical encodings of the same bytes
        payload, mac = raw[:PAYLOAD_BYTES], raw[PAYLOAD_BYTES:]
        if not hmac.compare_digest(mac, self._mac(payload)):
            raise AuthError("invalid claim token")
        if int.from_bytes(payload[32:], "big") <= int(self._clock()):
            raise AuthError("claim token expired")
        return ClaimTokenContext(firm_id=UUID(bytes=payload[:16]), claim_id=UUID(bytes=payload[16:32]))

    def token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
