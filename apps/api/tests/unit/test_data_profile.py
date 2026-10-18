"""The data profile runs on the committed fixture (data/raw is gitignored, so CI cannot run make profile)."""

from __future__ import annotations

import json

from recallops.data_profile import profile_golden, profile_recalls
from recallops.settings import REPO_ROOT


def test_profile_recalls_on_the_committed_sample() -> None:
    raw = json.loads((REPO_ROOT / "data" / "samples" / "cpsc_sample.json").read_text(encoding="utf-8"))
    profile = profile_recalls(raw)
    assert profile["records"] == len(raw) == 51
    assert sum(v["recalls"] for v in profile["by_year"].values()) == 51
    assert 0 <= profile["remedy_requires_photo"]["pct"] <= 100


def test_profile_golden_counts_match_the_committed_sets() -> None:
    golden = profile_golden()
    assert golden["claims"]["count"] == 300
    assert golden["claims"]["programs"] == 20
    assert sum(golden["claims"]["expected_outcomes"].values()) == 300
    assert golden["notices"]["count"] == len(
        json.loads((REPO_ROOT / "evals" / "extraction" / "golden_notices.json").read_text(encoding="utf-8"))["notices"]
    )
