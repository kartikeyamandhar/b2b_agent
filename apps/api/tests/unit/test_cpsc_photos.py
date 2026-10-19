"""The CPSC photo block: selection, replay without image files, and scoring (evals/cpsc_photos/PREREG.md)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from PIL import Image

from recallops.evals import cpsc_photos as cp
from recallops.llm.client import ImageInput
from recallops.settings import Settings
from tests import fakes


def png(width: int = 40, height: int = 30) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 10, 10)).save(out, format="PNG")
    return out.getvalue()


def record(number: str, *images: tuple[str, str]) -> dict[str, Any]:
    return {"RecallNumber": number, "Images": [{"URL": url, "Caption": cap} for url, cap in images]}


def test_candidates_pools_and_seeded_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "instruction_for", lambda r: "cut the cord" if r["RecallNumber"] != "3" else None)
    records = [
        record(
            "1",
            ("https://www.cpsc.gov/a.png", "Recalled heater"),
            ("https://www.cpsc.gov/b.png", "Location of model number"),
            ("https://www.cpsc.gov/c.png", "Model number label"),
        ),
        record("2", ("https://www.cpsc.gov/d.png", "Recalled lamp with the power cord cut for disposal")),
        record("3", ("https://www.cpsc.gov/e.png", "Recalled toy")),  # no instruction: not a product candidate
        record("4", ("", "Recalled fan")),  # no URL
    ]
    pools = cp.candidates(records)
    assert [c.url for c in pools["label"]] == ["https://www.cpsc.gov/b.png"]  # first label image only, per recall
    assert [c.url for c in pools["product"]] == ["https://www.cpsc.gov/a.png"]
    assert [(c.url, c.instruction) for c in pools["destruction"]] == [("https://www.cpsc.gov/d.png", "cut the cord")]
    assert pools == cp.candidates(list(reversed(records)))  # order comes from the seed, not the input order


def test_destruction_without_instruction_uses_the_production_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "instruction_for", lambda r: None)
    pools = cp.candidates([record("9", ("https://www.cpsc.gov/x.jpg", "Helmet with straps cut off"))])
    assert pools["destruction"][0].instruction == cp.DEFAULT_INSTRUCTION
    assert pools["product"] == []


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_download_accepts_cpsc_images_and_follows_redirects_only_within_cpsc() -> None:
    body = png()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/moved.png":
            return httpx.Response(302, headers={"location": "https://www.cpsc.gov/real.png"})
        if request.url.path == "/relative.png":
            return httpx.Response(302, headers={"location": "/real.png"})
        if request.url.path == "/away.png":
            return httpx.Response(302, headers={"location": "https://evil.example/real.png"})
        return httpx.Response(200, content=body)

    settings = Settings()
    with _client(handler) as client:
        assert cp.download(client, "https://www.cpsc.gov/moved.png", settings) == body
        assert cp.download(client, "https://www.cpsc.gov/relative.png", settings) == body
        with pytest.raises(cp.Rejected, match="not CPSC"):
            cp.download(client, "https://www.cpsc.gov/away.png", settings)
        with pytest.raises(cp.Rejected, match="not CPSC"):
            cp.download(client, "https://example.org/a.png", settings)
        with pytest.raises(cp.Rejected, match="not CPSC"):
            cp.download(client, "http://www.cpsc.gov/a.png", settings)


def test_download_refuses_errors_oversize_and_non_images() -> None:
    settings = Settings(max_photo_bytes=1_000)
    big = png(400, 400)
    with _client(lambda r: httpx.Response(404)) as client, pytest.raises(cp.Rejected, match="HTTP 404"):
        cp.download(client, "https://www.cpsc.gov/a.png", settings)
    junk = _client(lambda r: httpx.Response(200, content=b"not an image"))
    with junk as client, pytest.raises(cp.Rejected, match="not an image"):
        cp.download(client, "https://www.cpsc.gov/a.png", settings)
    oversize = _client(lambda r: httpx.Response(200, content=big))
    with oversize as client, pytest.raises(cp.Rejected, match="MAX_PHOTO_BYTES"):
        cp.download(client, "https://www.cpsc.gov/a.png", settings)


def _item(i: str, task: str, data: bytes, **kw: Any) -> dict[str, Any]:
    meta = cp.image_meta(data)
    return {"id": i, "task": task, "recall_number": "1", "instruction": kw.get("instruction"), **meta}


def test_replay_needs_only_hash_and_sent_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "IMAGES", tmp_path)
    data = png()
    item = _item("L01", "label", data)
    replayed = cp.image_input(item, live=False)
    assert replayed.data_b64 == ""
    present = cp.image_path(item["sha256"], item["format"])
    present.write_bytes(data)
    sent = cp.image_input(item, live=False)
    assert sent.key() == replayed.key() == ImageInput("image/png", "x", 40, 30, item["sha256"]).key()
    present.write_bytes(png(41, 30))  # a different file under the same name
    with pytest.raises(ValueError, match="sha256"):
        cp.image_input(item, live=False)
    present.unlink()
    with pytest.raises(FileNotFoundError):
        cp.image_input(item, live=True)


@pytest.mark.parametrize(
    ("truth", "reading", "verdict", "exact"),
    [
        ({"legible": True, "model_texts": ["TL-1302Z"]}, {"legible": True, "model_text": "TL-1302Z"}, "read", True),
        ({"legible": True, "model_texts": ["TL-1302Z"]}, {"legible": True, "model_text": "tl 1302z"}, "read", False),
        ({"legible": True, "model_texts": ["TL-1302Z"]}, {"legible": True, "model_text": "TL1308Z"}, "misread", False),
        (
            {"legible": True, "model_texts": ["TL-1302Z"]},
            {"legible": False, "model_text": "TL1302Z"},
            "abstained",
            None,
        ),
        ({"legible": True, "model_texts": ["TL-1302Z"]}, {"legible": True, "model_text": None}, "abstained", None),
        ({"legible": False, "model_texts": []}, {"legible": True, "model_text": "X1"}, "hallucinated", None),
        ({"legible": False, "model_texts": []}, {"legible": False, "model_text": None}, "abstained_ok", None),
    ],
)
def test_label_scoring(truth: dict[str, Any], reading: dict[str, Any], verdict: str, exact: bool | None) -> None:
    got = cp.score_item({"task": "label"}, truth, reading)
    assert got["verdict"] == verdict
    assert got.get("exact") == exact


def test_wrong_model_texts_are_split_into_printed_codes_and_unprinted_text() -> None:
    truth = {"legible": True, "model_texts": ["X-203"], "printed_codes": ["65901322", "X-203N-TH"]}
    serial = cp.score_item({"task": "label"}, truth, {"legible": True, "model_text": "65901322"})
    assert (serial["verdict"], serial["kind"]) == ("misread", "other_code")
    invented = cp.score_item({"task": "label"}, truth, {"legible": True, "model_text": "X-208"})
    assert (invented["verdict"], invented["kind"]) == ("misread", "unprinted")
    none = {"legible": False, "model_texts": [], "printed_codes": ["FTBFSD-0162"]}
    sku = cp.score_item({"task": "label"}, none, {"legible": True, "model_text": "FTBFSD 0162"})
    assert (sku["verdict"], sku["kind"]) == ("hallucinated", "other_code")  # E3 still counts it, as pre-registered
    item = cp.score_item(
        {"task": "label"}, {**none, "printed_codes": ["61970"]}, {"legible": True, "model_text": "ITEM 61970"}
    )
    assert item["kind"] == "other_code"
    brackets = cp.score_item(
        {"task": "label"}, {"legible": True, "model_texts": ["PUA-US"]}, {"legible": True, "model_text": "<PUA-US>"}
    )
    assert (brackets["verdict"], brackets["kind"]) == ("misread", "formatting")


def test_destruction_scoring_and_missing() -> None:
    item = {"task": "product"}
    assert cp.score_item(item, {"destruction_shown": False}, {"complies": False})["verdict"] == "correct"
    assert cp.score_item(item, {"destruction_shown": False}, {"complies": True})["verdict"] == "wrong"
    assert cp.score_item(item, {"destruction_shown": True}, {"complies": True})["group"] == "shown"
    assert cp.score_item(item, {"destruction_shown": True}, None)["verdict"] == "missing"


def test_run_scores_replayed_readings_and_writes_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        _item("L01", "label", png(40, 30)),
        _item("L02", "label", png(41, 30)),
        _item("P01", "product", png(42, 30), instruction="cut the cord"),
        _item("D01", "destruction", png(43, 30), instruction="cut the cord"),
    ]
    for it in items:
        it.update(url="https://www.cpsc.gov/x.png", caption="", image_index=0)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"items": items, "skipped": [], "pool_sizes": {"label": 9, "product": 9, "destruction": 1}})
    )
    labels = {
        "status": "test",
        "items": {
            "L01": {"legible": True, "model_texts": ["AB-12"]},
            "L02": {"legible": False, "model_texts": []},
            "P01": {"destruction_shown": False},
            "D01": {"destruction_shown": True},
        },
    }
    (tmp_path / "labels.json").write_text(json.dumps(labels))
    monkeypatch.setattr(cp, "DIR", tmp_path)
    monkeypatch.setattr(cp, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(cp, "LABELS", tmp_path / "labels.json")
    monkeypatch.setattr(cp, "IMAGES", tmp_path / "images")
    llm, transport = fakes.client(
        [fakes.label("AB12"), fakes.label("ZZ9"), fakes.destruction(False), fakes.destruction(True)]
    )
    out = cp.run(Settings(), tmp_path / "results", llm=llm)
    m = out["metrics"]
    assert (m["label_read"], m["label_read_exact"], m["label_hallucinated"]) == (1, 0, 1)
    assert (m["product_rejected"], m["destruction_accepted"], m["missing"]) == (1, 1, 0)
    assert m["model_calls"] == 4 and len(transport.calls) == 4
    met = {e["expectation"][:2]: e["met"] for e in out["expectations"]}
    assert met["E1"] is True and met["E3"] is False and met["E4"] is False  # 1 rejected is under 18
    table = (tmp_path / "results" / "cpsc_photos.md").read_text()
    assert "| L02 | label | 1 | hallucinated | ZZ9 |" in table
    assert json.loads((tmp_path / "results" / "cpsc_photos.json").read_text())["metrics"] == m


def test_fake_mode_reports_the_block_as_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item("L01", "label", png())
    (tmp_path / "manifest.json").write_text(
        json.dumps({"items": [item], "skipped": [], "pool_sizes": {"label": 1, "product": 0, "destruction": 0}})
    )
    (tmp_path / "labels.json").write_text(json.dumps({"items": {"L01": {"legible": False, "model_texts": []}}}))
    monkeypatch.setattr(cp, "DIR", tmp_path)
    monkeypatch.setattr(cp, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(cp, "LABELS", tmp_path / "labels.json")
    out = cp.run(Settings(llm_mode="fake"), tmp_path / "results")
    assert out["status"] == "not run (LLM_MODE=fake)"
    assert out["metrics"]["missing"] == 1 and out["metrics"]["model_calls"] == 0
    assert all(e["met"] is None for e in out["expectations"])
    assert "Generated by `make eval` (fake)" in (tmp_path / "results" / "cpsc_photos.md").read_text()


def test_run_refuses_unlabeled_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item("L01", "label", png())
    (tmp_path / "manifest.json").write_text(json.dumps({"items": [item], "skipped": [], "pool_sizes": {}}))
    (tmp_path / "labels.json").write_text(json.dumps({"items": {}}))
    monkeypatch.setattr(cp, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(cp, "LABELS", tmp_path / "labels.json")
    with pytest.raises(SystemExit, match="L01"):
        cp.run(Settings(), tmp_path, llm=fakes.client([])[0])


def test_committed_manifest_matches_the_pre_registered_rule() -> None:
    manifest = cp.load_manifest()
    items = manifest["items"]
    assert [sum(1 for i in items if i["task"] == t) for t in cp.TASKS] == [
        40,
        20,
        manifest["pool_sizes"]["destruction"],
    ]
    assert len({i["id"] for i in items}) == len(items)
    for task in ("label", "product"):
        numbers = [i["recall_number"] for i in items if i["task"] == task]
        assert len(numbers) == len(set(numbers)), f"{task}: one photo per recall"
    for i in items:
        assert urlsplit(i["url"]).hostname in cp.ALLOWED_HOSTS
        assert len(i["sha256"]) == 64 and hashlib.sha256(b"").hexdigest() != i["sha256"]
        assert max(i["sent_width"], i["sent_height"]) <= 1568
        assert (i["task"] == "label") == (i["instruction"] is None)


@pytest.mark.skipif(not cp.RAW.exists(), reason="needs the cached CPSC feed in data/raw/cpsc (gitignored)")
def test_rerunning_the_rule_reproduces_the_committed_manifest() -> None:
    pools = cp.candidates(cp.load_records())
    items = cp.load_manifest()["items"]
    for task in cp.TASKS:
        chosen = [(i["recall_number"], i["image_index"], i["url"]) for i in items if i["task"] == task]
        order = [(c.recall_number, c.image_index, c.url) for c in pools[task]]
        assert chosen == order[: len(chosen)], f"{task}: the manifest is not the rule's first picks"
