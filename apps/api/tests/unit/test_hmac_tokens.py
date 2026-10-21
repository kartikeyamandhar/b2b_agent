"""Signed claim-link tokens (invariant 10, 04-login.md section 8): round trip, expiry, tamper, wrong secret, and the
refusal of the example secret. Program close is enforced in portal.resolve (tests/api)."""

from __future__ import annotations

import base64
import uuid

import pytest

from recallops.auth.hmac_tokens import HmacClaimTokenVerifier, UnsafeClaimTokenSecret
from recallops.auth.provider import AuthError
from recallops.settings import EXAMPLE_CLAIM_TOKEN_SECRET, Settings

SECRET = base64.b64encode(bytes(range(32))).decode()
OTHER_SECRET = base64.b64encode(bytes(range(1, 33))).decode()
FIRM = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
CLAIM = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
T0 = 1_800_000_000.0


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def verifier(secret: str = SECRET, clock: Clock | None = None) -> HmacClaimTokenVerifier:
    return HmacClaimTokenVerifier(Settings(claim_token_secret=secret), clock=clock or Clock(T0))


def test_round_trip_names_the_firm_and_the_claim() -> None:
    v = verifier()
    tok = v.issue(FIRM, CLAIM)
    assert len(tok) == 96 and "=" not in tok and "." not in tok
    ctx = v.verify(tok)
    assert (ctx.firm_id, ctx.claim_id) == (FIRM, CLAIM)
    assert v.token_hash(tok) != tok and len(v.token_hash(tok)) == 64


def test_links_expire_after_ninety_days() -> None:
    clock = Clock(T0)
    v = verifier(clock=clock)
    tok = v.issue(FIRM, CLAIM)
    clock.now = T0 + 90 * 86400 - 1
    assert v.verify(tok).claim_id == CLAIM
    clock.now = T0 + 90 * 86400
    with pytest.raises(AuthError, match="expired"):
        v.verify(tok)


def test_a_changed_byte_anywhere_is_refused() -> None:
    v = verifier()
    raw = bytearray(base64.urlsafe_b64decode(v.issue(FIRM, CLAIM) + "=="))
    for i in (0, 20, 39, 40, 71):  # firm, claim, expiry, first and last MAC byte
        forged = bytearray(raw)
        forged[i] ^= 1
        with pytest.raises(AuthError):
            v.verify(base64.urlsafe_b64encode(bytes(forged)).rstrip(b"=").decode())


def test_another_secret_or_garbage_is_refused() -> None:
    tok = verifier(OTHER_SECRET).issue(FIRM, CLAIM)
    with pytest.raises(AuthError):
        verifier().verify(tok)
    for junk in ("", "dev." + str(FIRM) + "." + str(CLAIM), "!" * 96, tok[:-1]):
        with pytest.raises(AuthError):
            verifier().verify(junk)


@pytest.mark.parametrize("secret", [EXAMPLE_CLAIM_TOKEN_SECRET, base64.b64encode(b"short").decode(), "not base64!"])
def test_unsafe_secrets_refuse_to_load(secret: str) -> None:
    with pytest.raises(UnsafeClaimTokenSecret):
        verifier(secret)
