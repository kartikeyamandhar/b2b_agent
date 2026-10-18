"""Embedders for document_chunks (CLAUDE.md invariant 7: prose documents use vectors).

The project has no embedding provider key, so the default is a deterministic local embedder (hash-ngram-v1): word
unigrams, word bigrams, and character trigrams of each word are hashed into EMBEDDING_DIM signed buckets with
log term frequency, then L2-normalized. It is a lexical-semantic hybrid (shared stems and phrases land near each
other), cheap, reproducible across machines, and honest about what it is: not a neural embedding. A provider
embedder plugs in behind the same protocol (decisions log, 2026-10-18).
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from collections import Counter
from typing import Protocol

import numpy as np
import numpy.typing as npt

HASH_EMBEDDER = "hash-ngram-v1"
_WORD = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _features(text: str) -> Counter[str]:
    words = _WORD.findall(text.lower())
    feats: Counter[str] = Counter()
    for w in words:
        feats[f"w:{w}"] += 1
        padded = f"<{w}>"
        for i in range(len(padded) - 2):
            feats[f"c:{padded[i : i + 3]}"] += 1
    for a, b in itertools.pairwise(words):
        feats[f"b:{a}_{b}"] += 1
    return feats


class HashNgramEmbedder:
    model_id = HASH_EMBEDDER

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def vector(self, text: str) -> npt.NDArray[np.float64]:
        out = np.zeros(self.dim, dtype=np.float64)
        for feat, tf in _features(text).items():
            digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            weight = (1.0 + math.log(tf)) * (0.5 if feat.startswith("c:") else 1.0)
            out[value % self.dim] += weight if (value >> 63) & 1 else -weight
        norm = float(np.linalg.norm(out))
        return out / norm if norm > 0 else out

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in self.vector(t)] for t in texts]


def build_embedder(model_id: str, dim: int) -> Embedder:
    if model_id == HASH_EMBEDDER:
        return HashNgramEmbedder(dim)
    raise NotImplementedError(
        f"embedding model {model_id!r} needs a provider client and key; set EMBEDDING_MODEL_ID={HASH_EMBEDDER}"
    )
