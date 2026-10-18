"""Vision tools through the metered client (fake transport, no network) and the local hashing embedder."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest
from PIL import Image

from recallops.claims.vision import (
    DEFAULT_SIZE,
    MAX_SIDE_PX,
    VisionError,
    check_destruction_photo,
    prepare_image,
    read_label_photo,
)
from recallops.llm.client import FakeTransport, LlmClient, ModelResponse
from recallops.llm.meter import CostMeter, InMemoryLedger, RunBudget
from recallops.llm.pricing import PriceTable, Usage
from recallops.retrieval.embed import HASH_EMBEDDER, HashNgramEmbedder, build_embedder
from recallops.settings import Settings

MODEL = "claude-haiku-5-5"
PRICES = PriceTable(MODEL, Decimal("0.10"), Decimal("0.50"), 100_000, Decimal("0.50"), Decimal("2.50"))


def _jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(out, format="JPEG", quality=80)
    return out.getvalue()


def _client(texts: list[str]) -> tuple[LlmClient, FakeTransport]:
    transport = FakeTransport(
        [ModelResponse(text=t, usage=Usage(700, 60), stop_reason="end_turn", model_id=MODEL) for t in texts]
    )
    meter = CostMeter(PRICES, InMemoryLedger(), Decimal(2), Decimal(1))
    return LlmClient(transport, meter, MODEL, Settings()), transport


def _run() -> RunBudget:
    return RunBudget(max_steps=8, max_usd=Decimal("0.02"), max_seconds=60)


def test_prepare_image_keeps_small_photos_and_keys_on_the_stored_hash() -> None:
    data = _jpeg(768, 576)
    image = prepare_image(data, "abc123")
    assert (image.width, image.height, image.media_type) == (768, 576, "image/jpeg")
    assert image.key() == "abc123:768x576"
    big = prepare_image(_jpeg(4000, 3000), "big")
    assert max(big.width, big.height) == MAX_SIDE_PX
    replay_only = prepare_image(None, "def456")
    assert replay_only.data_b64 == "" and (replay_only.width, replay_only.height) == DEFAULT_SIZE


def test_read_label_photo_parses_and_meters_one_call() -> None:
    reading = {"legible": True, "model_text": "TL1302Z", "serial_text": None, "date_code_text": None, "confidence": 0.9}
    llm, transport = _client([json.dumps(reading)])
    run = _run()
    result = read_label_photo(llm, run, prepare_image(_jpeg(768, 576), "s1"))
    assert result.reading.model_text == "TL1302Z"
    assert run.steps == 1 and run.cost_usd > 0
    request, _ = transport.calls[0]
    assert request.images[0].source_sha256 == "s1" and request.output_schema is not None


def test_destruction_check_and_invalid_output() -> None:
    ok = {"shows_product": True, "complies": True, "observed": "The cord is cut.", "confidence": 0.8}
    llm, transport = _client([json.dumps(ok), "not json"])
    image = prepare_image(_jpeg(768, 576), "s2")
    result = check_destruction_photo(llm, _run(), image, "cut the power cord")
    assert result.reading.complies is True
    assert "cut the power cord" in transport.calls[0][0].user_text
    with pytest.raises(VisionError):
        check_destruction_photo(llm, _run(), image, "cut the power cord")


def test_hash_embedder_is_deterministic_normalized_and_lexical() -> None:
    embedder = build_embedder(HASH_EMBEDDER, 1024)
    assert isinstance(embedder, HashNgramEmbedder)
    a, b, c = embedder.embed(
        [
            "Receipts are not required for a refund",
            "A receipt is not required to receive the refund",
            "Cut the power cord before disposal",
        ]
    )
    assert a == embedder.embed(["Receipts are not required for a refund"])[0]
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9
    dot_ab = sum(x * y for x, y in zip(a, b, strict=True))
    dot_ac = sum(x * y for x, y in zip(a, c, strict=True))
    assert dot_ab > dot_ac
    assert embedder.embed([""])[0] == [0.0] * 1024
    with pytest.raises(NotImplementedError):
        build_embedder("voyage-3.5-lite", 1024)
