"""Audit CLI: keeps, edits, refuses unverifiable values, logs changes, never needs an extractor."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from recallops.evals.golden import load_golden, notice_text, validate_golden
from recallops.evals.label_cli import AUDITED, main, to_label
from recallops.settings import REPO_ROOT

SOURCE = REPO_ROOT / "evals" / "extraction"


def workspace(tmp_path: Path, source_id: str) -> Path:
    base = tmp_path / "extraction"
    (base / "notices").mkdir(parents=True)
    shutil.copy(SOURCE / "golden_notices.json", base / "golden_notices.json")
    shutil.copy(SOURCE / "notices" / f"{source_id}.txt", base / "notices" / f"{source_id}.txt")
    return base


def answers(*values: str) -> Callable[[str], str]:
    queue = list(values)
    return lambda prompt: queue.pop(0) if queue else ""


def test_round_trip_of_every_golden_label() -> None:
    from recallops.evals.golden import build_rules

    for notice in load_golden(SOURCE / "golden_notices.json"):
        rebuilt = build_rules(to_label(notice.rules), notice_text(SOURCE / "notices", notice.source_id))
        assert rebuilt.identification.models == notice.rules.identification.models
        assert rebuilt.identification.sold_to == notice.rules.identification.sold_to


def test_audit_keeps_everything_on_enter(tmp_path: Path) -> None:
    base = workspace(tmp_path, "24082")
    out: list[str] = []
    assert main(["--audit", "--source-id", "24082"], ask=answers(), say=out.append, base=base) == 0
    notice = next(n for n in load_golden(base / "golden_notices.json") if n.source_id == "24082")
    assert notice.label_status == AUDITED
    assert notice.rules.identification.models == ["SE4570"]
    assert validate_golden([notice], base / "notices") == []
    assert not (base / "audit_log.jsonl").exists() or (base / "audit_log.jsonl").read_text() == ""


def test_audit_edit_is_logged(tmp_path: Path) -> None:
    base = workspace(tmp_path, "24082")
    main(
        ["--audit", "--source-id", "24082"],
        ask=answers("", "Grainger | www.grainger.com"),
        say=lambda s: None,
        base=base,
    )
    notice = next(n for n in load_golden(base / "golden_notices.json") if n.source_id == "24082")
    assert notice.rules.identification.sold_at == ["Grainger", "www.grainger.com"]
    log = [json.loads(line) for line in (base / "audit_log.jsonl").read_text().splitlines()]
    assert [entry["field"] for entry in log] == ["sold_at"]


def test_audit_refuses_value_not_in_notice(tmp_path: Path) -> None:
    base = workspace(tmp_path, "24082")
    out: list[str] = []
    main(["--audit", "--source-id", "24082"], ask=answers("NOT-A-MODEL"), say=out.append, base=base)
    assert any("refused" in line for line in out)
    notice = next(n for n in load_golden(base / "golden_notices.json") if n.source_id == "24082")
    assert notice.rules.identification.models == ["SE4570"]


def test_blind_mode_requires_every_field(tmp_path: Path) -> None:
    base = workspace(tmp_path, "24082")
    out: list[str] = []
    replies = answers(
        "SE4570",
        "Grainger",
        "2022-09-01",
        "September 2022",
        "2023-09-30",
        "September 2023",
        "3200",
        "About 3,200",
        "replacement",
        "free replacement boots",
        "-",
        "-",
        "-",
        "800-550-7327",
    )
    assert main(["--blind", "--source-id", "24082"], ask=replies, say=out.append, base=base) == 0
    labeled = json.loads(out[-1])
    assert labeled["rules"]["identification"]["models"] == ["SE4570"]
    assert labeled["rules"]["remedy"]["remedy_type"] == "replacement"
