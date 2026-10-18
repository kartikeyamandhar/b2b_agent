"""Migration 0002 hand-writes the LangGraph checkpointer tables and marks library migrations 0 to 9 as applied.
If the pinned langgraph-checkpoint-postgres ever ships a different migration list, this fails before setup()
could try to run CREATE INDEX CONCURRENTLY against the database."""

from __future__ import annotations

import hashlib

from langgraph.checkpoint.postgres.base import BasePostgresSaver

EXPECTED = 10
# sha256 of the joined MIGRATIONS strings of langgraph-checkpoint-postgres 3.2.0, as mirrored in 0002_m2_agent.py
EXPECTED_SHA256 = "b61d83ce19b67141d851d7fa29a73ecc6f5cd8e9d45c1aa0f6b6f9ebe50f26bf"


def test_checkpointer_migrations_match_the_hand_written_schema() -> None:
    migrations = BasePostgresSaver.MIGRATIONS
    assert len(migrations) == EXPECTED
    joined = "\n".join(migrations)
    for needle in ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes", "task_path"):
        assert needle in joined
    assert hashlib.sha256(joined.encode()).hexdigest() == EXPECTED_SHA256
