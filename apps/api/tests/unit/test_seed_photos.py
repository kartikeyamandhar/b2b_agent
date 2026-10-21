"""The seed's photo writes survive a transient storage failure. The first production seed made about 3,000 storage
calls over the network and met a 429 and then a read timeout (2026-10-21); the seed owns its deterministic keys, so
it repeats the delete-then-put pair, and only a failure that persists stops it."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from recallops import seed
from recallops.storage import StorageError

KEY = "00000000-0000-4000-8000-00000000d001/11111111-1111-4111-8111-111111111111/label.jpg"


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 40, 40)).save(out, "JPEG")
    return out.getvalue()


class FlakyStorage:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[str] = []
        self.objects: dict[str, bytes] = {}

    def put(self, relative: str, data: bytes, content_type: str) -> None:
        self.calls.append("put")
        if self.failures:
            self.failures -= 1
            raise StorageError(f"put {relative} timed out (ReadTimeout)")
        self.objects[relative] = data

    def get(self, relative: str) -> bytes | None:
        return self.objects.get(relative)

    def delete(self, relative: str) -> None:
        self.calls.append("delete")
        self.objects.pop(relative, None)


def test_a_transient_failure_repeats_the_delete_and_put(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr("recallops.seed.time.sleep", waits.append)
    storage = FlakyStorage(failures=1)
    facts = seed.store_seed_photo(storage, KEY, jpeg())
    assert storage.calls == ["delete", "put", "delete", "put"]
    assert KEY in storage.objects and facts.sha256
    assert waits == [1.0]


def test_a_failure_that_persists_still_stops_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("recallops.seed.time.sleep", lambda s: None)
    storage = FlakyStorage(failures=10)
    with pytest.raises(StorageError, match="timed out"):
        seed.store_seed_photo(storage, KEY, jpeg())
    assert storage.calls.count("put") == seed.SEED_PHOTO_ATTEMPTS
