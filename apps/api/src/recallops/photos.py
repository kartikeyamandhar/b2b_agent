"""Photo ingestion shared by the synthetic generator and the portal upload route: hashes are always computed on the
bytes that are stored (recallops.storage), never on an in-memory render."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from recallops.fraud.phash import phash_image
from recallops.storage import Storage

PhotoKind = str  # label | destruction | receipt | other


class InvalidPhoto(ValueError):
    pass


@dataclass(frozen=True)
class PhotoFacts:
    sha256: str
    phash: str
    exif: dict[str, Any]


DEFAULT_MAX_PIXELS = 40_000_000


def inspect(data: bytes, max_pixels: int = DEFAULT_MAX_PIXELS) -> PhotoFacts:
    """Decode-check an image. The pixel count is checked from the header before any decode, and Pillow's
    decompression-bomb warning is an error, so a small file that expands to gigabytes is refused, not decoded."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width * height > max_pixels:
                    raise InvalidPhoto(f"image is {width} by {height} pixels; the limit is {max_pixels} pixels")
                image.load()
                facts = PhotoFacts(
                    sha256=hashlib.sha256(data).hexdigest(),
                    phash=phash_image(image),
                    exif={"width": image.width, "height": image.height, "format": image.format or "unknown"},
                )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidPhoto(f"not a readable image: {exc}") from exc
    return facts


def downscale(data: bytes, max_pixels: int, max_bytes: int | None = None, quality: int = 85) -> bytes | None:
    """A claimant photo over max_pixels, or over max_bytes, re-encoded as a JPEG of at most about max_pixels (plan v3:
    a public link must not be able to fill the shared photo bucket; a 2 MP PNG is 6 MB as uploaded), turned upright
    first (a phone stores its rotation as EXIF, which a re-encode drops). None when the photo is already small enough.
    Deterministic, so two uploads of the same photo still match. Call after inspect(), which refuses decompression
    bombs."""
    with Image.open(io.BytesIO(data)) as image:
        pixels = image.width * image.height
        if pixels <= max_pixels and (max_bytes is None or len(data) <= max_bytes):
            return None
        upright = ImageOps.exif_transpose(image) or image
        scale = min(1.0, (max_pixels / (upright.width * upright.height)) ** 0.5)
        size = (max(1, int(upright.width * scale)), max(1, int(upright.height * scale)))
        small = upright.convert("RGB")
        if size != small.size:
            small = small.resize(size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        small.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def store(storage: Storage, relative_path: str, data: bytes, content_type: str = "image/jpeg") -> PhotoFacts:
    """Compute the facts on the bytes, then put exactly those bytes; returns the facts. Raises InvalidPhoto before
    anything is stored, and StorageError when the put fails."""
    facts = inspect(data)
    storage.put(relative_path, data, content_type)
    return facts
