"""M2: adjudication agent, worker leases, checkpointer tables, document chunks.

- Schema langgraph holds the LangGraph Postgres checkpointer tables, hand-written here because the library's own
  setup() runs CREATE INDEX CONCURRENTLY, which cannot run inside a migration transaction. checkpoint_migrations is
  filled with versions 0 to 9 so setup() is a no-op; tests/unit/test_contract_checkpointer.py fails if the pinned
  library ever ships a different migration list. Only recallops_internal (the worker) can read these tables; they
  have no firm_id and are never reachable from the API role (CLAUDE.md invariant 2, decisions log 2026-10-18).
- document_chunks (tenant table, RLS forced): prose documents with a pgvector embedding and an english tsvector.
- agent_steps: counted (counts toward the run's step cap), nominal_cost_usd (cost from reported usage, also for
  replayed responses whose charged cost_usd is 0), step_key (idempotent re-execution of a node after a crash).
- agent_runs: lease and resume columns for the worker, attempts, and the run config (ablation variant).
- claims: lease and attempts for the worker, messages (needs_info questions and claimant answers).
- worker_heartbeats (non-tenant, no claim data): /readyz reports the worker's age.

Revision ID: 0002_m2_agent
Revises: 0001_m1_initial
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0002_m2_agent"
down_revision = "0001_m1_initial"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1024  # settings.embedding_dim; changing it needs a new migration

CHECKPOINTER = """
CREATE SCHEMA langgraph AUTHORIZATION recallops_owner;
CREATE TABLE langgraph.checkpoint_migrations (v INTEGER PRIMARY KEY);
CREATE TABLE langgraph.checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE langgraph.checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
CREATE TABLE langgraph.checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
CREATE INDEX checkpoints_thread_id_idx ON langgraph.checkpoints (thread_id);
CREATE INDEX checkpoint_blobs_thread_id_idx ON langgraph.checkpoint_blobs (thread_id);
CREATE INDEX checkpoint_writes_thread_id_idx ON langgraph.checkpoint_writes (thread_id);
INSERT INTO langgraph.checkpoint_migrations (v) SELECT generate_series(0, 9);
REVOKE ALL ON SCHEMA langgraph FROM PUBLIC;
GRANT USAGE ON SCHEMA langgraph TO recallops_internal;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA langgraph TO recallops_internal;
"""

DDL = f"""
CREATE TABLE document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    idx integer NOT NULL CHECK (idx >= 0),
    text text NOT NULL,
    embedding vector({EMBEDDING_DIM}) NOT NULL,
    embedding_model text NOT NULL,
    search tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, idx),
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE
);
CREATE INDEX document_chunks_program_idx ON document_chunks (program_id);
CREATE INDEX document_chunks_search_idx ON document_chunks USING gin (search);
CREATE INDEX document_chunks_firm_idx ON document_chunks (firm_id);
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_chunks USING (firm_id = recallops_current_firm())
    WITH CHECK (firm_id = recallops_current_firm());

ALTER TABLE agent_steps ADD COLUMN counted boolean NOT NULL DEFAULT false;
ALTER TABLE agent_steps ADD COLUMN nominal_cost_usd numeric(10, 6) NOT NULL DEFAULT 0;
ALTER TABLE agent_steps ADD COLUMN step_key text;
ALTER TABLE agent_steps ADD CONSTRAINT agent_steps_run_step_key UNIQUE (run_id, step_key);

ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_graph_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_graph_check
    CHECK (graph IN ('extract_rules', 'adjudicate', 'rules_only', 'rules_plus_vision'));
ALTER TABLE agent_runs ADD COLUMN config jsonb NOT NULL DEFAULT '{{}}'::jsonb;
ALTER TABLE agent_runs ADD COLUMN resume_payload jsonb;
ALTER TABLE agent_runs ADD COLUMN lease_owner text;
ALTER TABLE agent_runs ADD COLUMN lease_until timestamptz;
ALTER TABLE agent_runs ADD COLUMN attempts integer NOT NULL DEFAULT 0;
CREATE INDEX agent_runs_resume_idx ON agent_runs (status) WHERE resume_payload IS NOT NULL;

ALTER TABLE claims ADD COLUMN lease_owner text;
ALTER TABLE claims ADD COLUMN lease_until timestamptz;
ALTER TABLE claims ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE claims ADD COLUMN messages jsonb NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX claims_submitted_idx ON claims (submitted_at) WHERE status = 'submitted';

CREATE TABLE worker_heartbeats (
    worker_id text PRIMARY KEY,
    seen_at timestamptz NOT NULL DEFAULT now(),
    info jsonb NOT NULL DEFAULT '{{}}'::jsonb
);

GRANT SELECT, INSERT, UPDATE, DELETE ON document_chunks TO recallops_app, recallops_internal;
GRANT SELECT ON worker_heartbeats TO recallops_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON worker_heartbeats TO recallops_internal;
"""


def upgrade() -> None:
    op.execute(CHECKPOINTER)
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS worker_heartbeats")
    op.execute("DROP INDEX IF EXISTS claims_submitted_idx")
    for column in ("messages", "attempts", "lease_until", "lease_owner"):
        op.execute(f"ALTER TABLE claims DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS agent_runs_resume_idx")
    for column in ("attempts", "lease_until", "lease_owner", "resume_payload", "config"):
        op.execute(f"ALTER TABLE agent_runs DROP COLUMN IF EXISTS {column}")
    op.execute("DELETE FROM agent_runs WHERE graph = 'rules_plus_vision'")
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_graph_check")
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_graph_check "
        "CHECK (graph IN ('extract_rules', 'adjudicate', 'rules_only'))"
    )
    op.execute("ALTER TABLE agent_steps DROP CONSTRAINT IF EXISTS agent_steps_run_step_key")
    for column in ("step_key", "nominal_cost_usd", "counted"):
        op.execute(f"ALTER TABLE agent_steps DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS document_chunks")
    op.execute("DROP SCHEMA IF EXISTS langgraph CASCADE")
