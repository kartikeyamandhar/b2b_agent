"""Pure pieces of plan v3: product claim-link tokens (signed, versioned, never readable as a claim token), the photo
downscale at upload, company names as the operator reads them, and the guard's address and path helpers."""

from __future__ import annotations

import base64
import io
import uuid

import pytest
from PIL import Image

from recallops.api.guards import INTAKE, PORTAL, client_network, redact
from recallops.auth.hmac_tokens import HmacClaimTokenVerifier, UnsafeClaimTokenSecret
from recallops.auth.intake_tokens import TOKEN_CHARS, DevIntakeTokens, HmacIntakeTokens, build_intake_tokens
from recallops.auth.provider import AuthError
from recallops.company import clean_name
from recallops.photos import downscale
from recallops.settings import EXAMPLE_CLAIM_TOKEN_SECRET, Settings

SECRET = base64.b64encode(bytes(range(32))).decode()
OTHER = base64.b64encode(bytes(range(1, 33))).decode()
FIRM = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
PROGRAM = uuid.UUID("00000000-0000-4000-8000-0000000000a1")


def tokens(secret: str = SECRET) -> HmacIntakeTokens:
    return HmacIntakeTokens(Settings(claim_token_secret=secret))


def test_an_intake_token_round_trips_and_carries_its_version() -> None:
    t = tokens()
    tok = t.issue(FIRM, PROGRAM, 3)
    assert len(tok) == TOKEN_CHARS and "=" not in tok and "." not in tok
    found = t.verify(tok)
    assert (found.firm_id, found.program_id, found.version) == (FIRM, PROGRAM, 3)
    assert t.issue(FIRM, PROGRAM, 4) != tok  # replacing the link changes it


def test_a_changed_byte_another_secret_or_another_spelling_is_refused() -> None:
    t = tokens()
    tok = t.issue(FIRM, PROGRAM, 1)
    flipped = tok[:10] + ("A" if tok[10] != "A" else "B") + tok[11:]
    with pytest.raises(AuthError):
        t.verify(flipped)
    with pytest.raises(AuthError):
        tokens(OTHER).verify(tok)
    with pytest.raises(AuthError):
        t.verify(tok + "A")
    with pytest.raises(AuthError):
        t.verify(tok[:-1] + "~")


def test_intake_and_claim_tokens_can_never_stand_in_for_each_other() -> None:
    """Both are keyed from CLAIM_TOKEN_SECRET; the intake key is derived under its own label and the lengths differ."""
    settings = Settings(claim_token_secret=SECRET)
    claim_tok = HmacClaimTokenVerifier(settings).issue(FIRM, PROGRAM)
    intake_tok = tokens().issue(FIRM, PROGRAM, 1)
    with pytest.raises(AuthError):
        tokens().verify(claim_tok)
    with pytest.raises(AuthError):
        HmacClaimTokenVerifier(settings).verify(intake_tok)


def test_the_example_secret_and_dev_links_are_refused_where_they_would_be_unsafe() -> None:
    with pytest.raises(UnsafeClaimTokenSecret):
        tokens(EXAMPLE_CLAIM_TOKEN_SECRET)
    with pytest.raises(UnsafeClaimTokenSecret):
        DevIntakeTokens(Settings(auth_provider="clerk", clerk_issuer="https://x.clerk.accounts.dev"))
    dev = build_intake_tokens(Settings(claim_token_provider="dev"))
    tok = dev.issue(FIRM, PROGRAM, 2)
    assert tok == f"intake.{FIRM}.{PROGRAM}.2" and dev.verify(tok).version == 2
    for bad in (f"intake.{FIRM}.{PROGRAM}.0", f"intake.{FIRM}.{PROGRAM}", f"dev.{FIRM}.{PROGRAM}"):
        with pytest.raises(AuthError):
            dev.verify(bad)


def _jpeg(width: int, height: int, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (200, 30, 30))
    image.paste((30, 30, 200), (0, 0, width // 2, height // 4))  # a mark that shows which way is up
    out = io.BytesIO()
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def test_a_large_photo_is_stored_smaller_and_upright_and_a_small_one_is_kept() -> None:
    assert downscale(_jpeg(800, 600), 2_000_000) is None
    smaller = downscale(_jpeg(4000, 3000, orientation=6), 2_000_000)  # a phone photo stored sideways
    assert smaller is not None
    with Image.open(io.BytesIO(smaller)) as image:
        assert image.format == "JPEG" and image.width * image.height <= 2_000_000
        assert image.height > image.width  # turned upright: orientation 6 is a 90 degree turn
        assert 0x0112 not in image.getexif()
    assert downscale(_jpeg(4000, 3000), 2_000_000) == downscale(_jpeg(4000, 3000), 2_000_000)  # duplicates still match


def test_a_company_name_cannot_hide_characters_from_the_operator() -> None:
    assert clean_name("  Acme‮ Recalls \n Inc. ") == "Acme Recalls Inc."
    assert clean_name("Zo​ë\u0000 Ltd") == "Zoë Ltd"
    for empty in ("", "   ", "​⁦"):
        with pytest.raises(ValueError, match="empty"):
            clean_name(empty)
    with pytest.raises(ValueError, match="longer"):
        clean_name("x" * 201)


def test_ipv6_clients_share_a_limit_per_64_and_intake_paths_are_redacted() -> None:
    assert client_network("2001:db8:1:2:aaaa::1") == client_network("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert client_network("2001:db8:1:3::1") != client_network("2001:db8:1:2::1")
    assert client_network("203.0.113.7") == "203.0.113.7" and client_network("testclient") == "testclient"
    assert INTAKE.match("/portal/intake/abc") and PORTAL.match("/portal/intake/abc")  # the guard tries INTAKE first
    assert redact("/portal/intake/secret-token") == "/portal/intake/<token>"
    assert redact("/portal/secret-token/photos") == "/portal/<token>/photos"


def test_env_example_lists_every_setting() -> None:
    """Invariant 12: the committed .env.example lists every variable (a commented line counts: the model prices are
    left commented on purpose). Plan v3 added nine settings that were missing from it."""
    import re

    from recallops.settings import REPO_ROOT

    lines = (REPO_ROOT / ".env.example").read_text().splitlines()
    listed = {m.group(1) for line in lines if (m := re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=", line))}
    assert sorted(name.upper() for name in Settings.model_fields if name.upper() not in listed) == []


def test_a_small_photo_that_is_heavy_on_disk_is_stored_re_encoded() -> None:
    """A 2 MP PNG under the pixel limit was stored as uploaded (6 MB); the byte cap bounds the bucket too."""
    import random

    rng = random.Random(7)
    noisy = Image.frombytes("RGB", (1600, 1200), bytes(rng.getrandbits(8) for _ in range(1600 * 1200 * 3)))
    out = io.BytesIO()
    noisy.save(out, format="PNG")
    png = out.getvalue()
    assert len(png) > 1_000_000 and 1600 * 1200 <= 2_000_000
    assert downscale(png, 2_000_000) is None  # the pixel limit alone kept it
    stored = downscale(png, 2_000_000, 1_000_000)
    assert stored is not None and stored[:2] == b"\xff\xd8" and len(stored) < len(png)
    assert downscale(png, 2_000_000, 1_000_000) == stored  # deterministic: duplicates still match


def test_ipv6_starts_also_count_per_48() -> None:
    from recallops.api.guards import client_wide_network

    assert client_wide_network("2001:db8:1:2::1") == client_wide_network("2001:db8:1:ff::9") == "2001:db8:1::/48"
    assert client_wide_network("203.0.113.7") is None and client_wide_network("testclient") is None
