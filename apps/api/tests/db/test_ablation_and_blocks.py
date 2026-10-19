"""The ablation harness, the block builders, and the freeze check (test database; scripted model, no network)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import Engine

from recallops.evals import ablation
from recallops.evals.ablation import BLOCKS, Block, block_hashes, metrics, render, run_row
from recallops.settings import REPO_ROOT, get_settings
from recallops.synth import probe, standin
from tests import fakes
from tests.fakes import decide, label, tool_use, turn

pytestmark = pytest.mark.db
ADJ = REPO_ROOT / "evals" / "adjudication"


def tiny_block(tmp_path: Path, n: int = 3) -> Block:
    payload = json.loads((ADJ / "golden_claims.json").read_text())
    claims = [c for c in payload["claims"] if c["program"] == "24082"][:n]
    (tmp_path / "labels").mkdir()
    for c in claims:
        shutil.copy(ADJ / "labels" / f"{c['claim_id']}.json", tmp_path / "labels" / f"{c['claim_id']}.json")
    (tmp_path / "claims.json").write_text(json.dumps({"claims": claims}))
    return Block("tiny", "tiny block", tmp_path / "claims.json", tmp_path / "labels", "program")


def test_rules_only_row_and_render(clean_db: Engine, tmp_path: Path) -> None:
    block = tiny_block(tmp_path)
    result = run_row("rules_only", block, None, get_settings(), clean_db, live=False, workers=1)
    assert result.complete and len(result.runs) == 3
    m = metrics(result.runs)
    assert m["n"] == 3 and m["mean_counted_steps"] == 0 and m["mean_cost_usd"] == 0
    ablation.BLOCKS["tiny"] = block
    try:
        md, payload = render({"tiny": {"rules_only": result}}, get_settings())
    finally:
        ablation.BLOCKS.pop("tiny")
    assert "rules_only" in md and payload["blocks"]["tiny"]["rules_only"]["n"] == 3
    assert "not evaluable" in md


def test_agent_row_with_a_scripted_model_and_a_missing_recording(clean_db: Engine, tmp_path: Path) -> None:
    block = tiny_block(tmp_path, n=2)
    claims = json.loads(block.claims_file.read_text())["claims"]
    script = []
    for i, c in enumerate(claims):
        script += [
            turn(tool_use(10 * i + 1, "read_label_photo")),
            label(c["model_claimed"]),
            decide(10 * i + 2, "approve", ["T1"]),
        ]
    llm, _ = fakes.client(script)
    result = run_row("loop_no_retrieval", block, llm, get_settings(), clean_db, live=False, workers=1)
    assert len(result.runs) == 2 and all(r.counted_steps == 3 for r in result.runs if not r.error)
    llm_empty, _ = fakes.client([])
    broken = run_row("loop_no_retrieval", block, llm_empty, get_settings(), clean_db, live=False, workers=1)
    assert not broken.complete and all(r.error for r in broken.runs)


def test_freeze_manifest_matches_the_committed_blocks() -> None:
    manifest = json.loads((ADJ / "blocks.json").read_text())["blocks"]
    for key, block in BLOCKS.items():
        now = block_hashes(block)
        assert manifest[key]["claims_sha256"] == now["claims_sha256"], key
        assert manifest[key]["labels_sha256"] == now["labels_sha256"], key


def test_probe_and_standin_rebuild_exactly(tmp_path: Path) -> None:
    for name in ("planted.yaml", "outcome_policy.yaml", "program_rules.json", "standin_specs.json"):
        shutil.copy(ADJ / name, tmp_path / name)
    summary = probe.build(tmp_path, tmp_path / "probe_photos")
    assert summary["claims"] == 60 and set(summary["categories"].values()) == {10}
    assert (tmp_path / "probe_claims.json").read_text() == (ADJ / "probe_claims.json").read_text()
    standin.build(tmp_path, tmp_path / "standin_photos")
    assert (tmp_path / "standin_claims.json").read_text() == (ADJ / "standin_claims.json").read_text()
