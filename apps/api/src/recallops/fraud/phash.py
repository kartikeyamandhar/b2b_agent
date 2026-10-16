"""Perceptual hash (DCT pHash, 64 bits) with numpy only. One implementation for synth, the portal upload
path, and check_duplicates, so planted and real duplicates behave the same."""

from __future__ import annotations

import io
from functools import lru_cache

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageOps

HASH_SIZE = 8
SAMPLE = 32


@lru_cache(maxsize=1)
def _dct_matrix() -> npt.NDArray[np.float64]:
    n = SAMPLE
    k = np.arange(n).reshape(-1, 1)
    i = np.arange(n).reshape(1, -1)
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0, :] = np.sqrt(1 / n)
    return m


def phash_image(image: Image.Image) -> str:
    gray = ImageOps.exif_transpose(image).convert("L").resize((SAMPLE, SAMPLE), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    dct = _dct_matrix() @ pixels @ _dct_matrix().T
    low = dct[:HASH_SIZE, :HASH_SIZE].flatten()
    median = float(np.median(low[1:]))
    bits = low > median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def phash_bytes(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        return phash_image(image)


def hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()
