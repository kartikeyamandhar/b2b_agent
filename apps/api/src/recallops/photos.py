"""Photo ingestion shared by the synthetic generator and (M2) the portal upload route: hashes are always
computed on the stored bytes, after orientation is applied, never on an in-memory render."""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from recallops.fraud.phash import phash_image

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


def store(root: Path, relative_path: str, data: bytes) -> PhotoFacts:
    """Write the bytes under root (local dev storage) and return the facts computed from those bytes."""
    facts = inspect(data)
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return facts
