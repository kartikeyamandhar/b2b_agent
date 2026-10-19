"""The frozen adjudication evidence (evals/adjudication/PREREG.md Revision 2; red team, decisions log 2026-10-18).

The ablation's recordings answer requests built from this decision logic and these documents. Changing any of them
silently changes what the committed numbers mean, so a change must come with a PREREG revision, new recordings, and
an updated evals/adjudication/freeze.json (python -m tests.unit.test_freeze writes it). The rules_only numbers are
pure code over the golden claims and are pinned exactly.
"""

from __future__ import annotations

import hashlib
import json
import sys

from recallops.evals.adjudication import run, summarize
from recallops.evals.golden import load_golden, load_program_rules, notice_text
from recallops.retrieval.documents import program_documents
from recallops.settings import REPO_ROOT

SRC = REPO_ROOT / "apps" / "api" / "src" / "recallops"
FROZEN = (
    "claims/tools/checks.py",
    "claims/policy.py",
    "claims/agent_tools.py",
    "claims/graph.py",
    "claims/rules_only.py",
    "claims/rules_plus_vision.py",
    "claims/vision.py",
)
MANIFEST = REPO_ROOT / "evals" / "adjudication" / "freeze.json"


def _documents_digest() -> str:
    adj = REPO_ROOT / "evals" / "adjudication"
    titles = {n.source_id: n.title for n in load_golden(REPO_ROOT / "evals" / "extraction" / "golden_notices.json")}
    digest = hashlib.sha256()
    for pid, rules in sorted(load_program_rules(adj / "program_rules.json").items()):
        source = notice_text(REPO_ROOT / "evals" / "extraction" / "notices", pid)
        for doc in program_documents(titles.get(pid, pid), source, rules, 10_000):
            digest.update(f"{pid}|{doc.kind}|{doc.title}|{doc.text}".encode())
    return digest.hexdigest()


def current() -> dict[str, str]:
    files = {path: hashlib.sha256((SRC / path).read_bytes()).hexdigest() for path in FROZEN}
    return {**files, "eval program documents": _documents_digest()}


def test_the_ablation_logic_and_documents_are_unchanged() -> None:
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))["sha256"]
    changed = sorted(k for k, v in current().items() if frozen.get(k) != v)
    assert changed == [], f"frozen by PREREG Revision 2, changed: {changed}; log a revision and re-record first"


def test_rules_only_on_golden300_is_exactly_the_committed_number() -> None:
    rows, _ = run()
    summary = summarize(rows)
    assert (summary["n"], summary["macro_f1"], summary["outcome_accuracy"]) == (300, 0.9817, 0.9867)


if __name__ == "__main__":
    MANIFEST.write_text(
        json.dumps(
            {
                "note": "sha256 of the decision logic and eval-program documents the ablation recordings were made "
                "against (PREREG Revision 2). Regenerate only with a logged revision: python -m tests.unit.test_freeze",
                "sha256": current(),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(f"wrote {MANIFEST.relative_to(REPO_ROOT)}\n")
