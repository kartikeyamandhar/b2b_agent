"""M1 initial schema: every 01-backend.md section 3 table except document_chunks (M2), with forced RLS.

Revision ID: 0001_m1_initial
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

from alembic import op

revision = "0001_m1_initial"
down_revision = None
branch_labels = None
depends_on = None

# Tables scoped by firm_id (CLAUDE.md invariant 2). firms is scoped by its own id.
TENANT_TABLES = (
    "firm_members",
    "recall_programs",
    "documents",
    "claims",
    "claim_photos",
    "agent_runs",
    "agent_steps",
    "fraud_clusters",
    "payouts",
    "mpr_reports",
    "audit_log",
    "usage_meters",
)
PUBLIC_TABLES = ("recalls", "feed_runs", "stripe_events")

DDL = r"""
CREATE FUNCTION recallops_current_firm() RETURNS uuid
LANGUAGE sql STABLE AS $$ SELECT NULLIF(current_setting('app.firm_id', true), '')::uuid $$;

CREATE TABLE recalls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL CHECK (source IN ('cpsc', 'fda', 'fsis', 'nhtsa')),
    source_id text NOT NULL CHECK (source_id <> ''),
    source_record_id text,
    recall_date date NOT NULL,
    last_publish_date date,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    url text NOT NULL DEFAULT '',
    raw jsonb NOT NULL,
    raw_sha256 text NOT NULL,
    source_text text NOT NULL,
    source_text_sha256 text NOT NULL,
    canonicalizer_version text NOT NULL,
    units_affected integer,
    units_status text NOT NULL CHECK (units_status IN ('ok', 'ambiguous', 'unparsed')),
    units_text text NOT NULL DEFAULT '',
    category text,
    retailers text[] NOT NULL DEFAULT '{}',
    manufacturers text[] NOT NULL DEFAULT '{}',
    importers text[] NOT NULL DEFAULT '{}',
    remedies text[] NOT NULL DEFAULT '{}',
    upcs text[] NOT NULL DEFAULT '{}',
    extracted_rules jsonb,
    extracted_at timestamptz,
    extractor text,
    extractor_version text,
    search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple'::regconfig, coalesce(title, '')), 'A')
        || setweight(to_tsvector('simple'::regconfig, coalesce(source_text, '')), 'B')
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);
CREATE INDEX recalls_search_idx ON recalls USING gin (search);
CREATE INDEX recalls_recall_date_idx ON recalls (recall_date);

CREATE TABLE feed_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'partial', 'failed')),
    fetched integer NOT NULL DEFAULT 0,
    inserted integer NOT NULL DEFAULT 0,
    updated integer NOT NULL DEFAULT 0,
    unchanged integer NOT NULL DEFAULT 0,
    failed integer NOT NULL DEFAULT 0,
    error text
);

CREATE TABLE stripe_events (
    id text PRIMARY KEY,
    type text NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    payload jsonb NOT NULL
);

CREATE TABLE firms (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    clerk_org_id text UNIQUE,
    stripe_customer_id text,
    plan text NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'growth', 'enterprise')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE firm_members (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    clerk_user_id text NOT NULL,
    role text NOT NULL CHECK (role IN ('admin', 'reviewer')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (firm_id, clerk_user_id)
);

CREATE TABLE recall_programs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    recall_id uuid REFERENCES recalls (id),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'closed')),
    identification_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    remedy_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
    value_threshold_cents integer NOT NULL CHECK (value_threshold_cents >= 0),
    opened_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, firm_id)
);

CREATE TABLE documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('notice', 'cap', 'warranty', 'faq', 'other')),
    title text NOT NULL,
    text text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE
);

CREATE TABLE claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    token_hash text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN (
        'draft', 'submitted', 'needs_info', 'under_review', 'approved', 'denied',
        'flagged', 'capped', 'paid', 'closed')),
    claimant jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_claimed text,
    serial_claimed text,
    date_code_claimed text,
    retailer_claimed text,
    purchase_date date,
    quantity integer NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    decision jsonb,
    remedy text CHECK (remedy IN ('refund', 'repair', 'replacement')),
    refund_cents integer CHECK (refund_cents >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    decided_at timestamptz,
    decided_by text,
    UNIQUE (id, firm_id),
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE
);
CREATE INDEX claims_program_status_idx ON claims (program_id, status, created_at);

CREATE TABLE claim_photos (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    claim_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('label', 'destruction', 'receipt', 'other')),
    storage_path text NOT NULL,
    sha256 text NOT NULL,
    phash text NOT NULL CHECK (phash ~ '^[0-9a-f]{16}$'),
    exif jsonb NOT NULL DEFAULT '{}'::jsonb,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (claim_id, firm_id) REFERENCES claims (id, firm_id) ON DELETE CASCADE
);
CREATE INDEX claim_photos_claim_idx ON claim_photos (claim_id);
CREATE INDEX claim_photos_sha256_idx ON claim_photos (firm_id, sha256);

CREATE TABLE agent_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid REFERENCES firms (id) ON DELETE CASCADE,
    claim_id uuid,
    program_id uuid,
    recall_id uuid REFERENCES recalls (id),
    graph text NOT NULL CHECK (graph IN ('extract_rules', 'adjudicate', 'rules_only')),
    thread_id text,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'capped', 'failed', 'waiting_human')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    steps integer NOT NULL DEFAULT 0,
    cost_usd numeric(10, 6) NOT NULL DEFAULT 0,
    model_id text,
    outcome jsonb,
    CHECK (firm_id IS NOT NULL OR (graph = 'extract_rules' AND claim_id IS NULL AND program_id IS NULL)),
    FOREIGN KEY (claim_id, firm_id) REFERENCES claims (id, firm_id) ON DELETE CASCADE
);
CREATE INDEX agent_runs_claim_idx ON agent_runs (claim_id, started_at DESC);

CREATE TABLE agent_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    firm_id uuid REFERENCES firms (id) ON DELETE CASCADE,
    idx integer NOT NULL CHECK (idx >= 0),
    node text NOT NULL,
    thought text,
    tool text,
    args jsonb,
    result jsonb,
    latency_ms integer NOT NULL DEFAULT 0,
    tokens_in integer NOT NULL DEFAULT 0,
    tokens_out integer NOT NULL DEFAULT 0,
    cost_usd numeric(10, 6) NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, idx)
);

-- agent_steps.firm_id must equal its run's firm_id. Under RLS the run lookup only sees the caller's
-- firm, so a step written against another firm's run fails here as well.
CREATE FUNCTION agent_steps_firm_matches_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.firm_id IS DISTINCT FROM (SELECT firm_id FROM agent_runs WHERE id = NEW.run_id) THEN
        RAISE EXCEPTION 'agent_steps.firm_id must equal agent_runs.firm_id for run %', NEW.run_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER agent_steps_firm_matches_run BEFORE INSERT OR UPDATE ON agent_steps
    FOR EACH ROW EXECUTE FUNCTION agent_steps_firm_matches_run();

CREATE TABLE fraud_clusters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('same_photo', 'same_address', 'same_payout', 'same_serial')),
    member_claim_ids uuid[] NOT NULL,
    score numeric(6, 4) NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE
);

CREATE TABLE payouts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    claim_id uuid NOT NULL UNIQUE,
    amount_cents integer NOT NULL CHECK (amount_cents >= 0),
    currency text NOT NULL DEFAULT 'usd',
    method text NOT NULL CHECK (method IN ('stripe_connect', 'check_export', 'ach_export')),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'pending_export', 'onboarding', 'transferred', 'failed', 'exported')),
    stripe_transfer_id text,
    idempotency_key uuid GENERATED ALWAYS AS (id) STORED,
    attempts integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (claim_id, firm_id) REFERENCES claims (id, firm_id) ON DELETE CASCADE
);

CREATE TABLE mpr_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    payload jsonb NOT NULL,
    reconciled boolean NOT NULL DEFAULT false,
    generated_at timestamptz NOT NULL DEFAULT now(),
    generated_by text NOT NULL,
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE
);

CREATE TABLE audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    actor text NOT NULL,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    before jsonb,
    after jsonb,
    at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usage_meters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    period_start date NOT NULL,
    claims_adjudicated integer NOT NULL DEFAULT 0,
    model_cost_usd numeric(10, 6) NOT NULL DEFAULT 0,
    UNIQUE (firm_id, period_start)
);
"""


def _rls_sql() -> str:
    statements = [
        "ALTER TABLE firms ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE firms FORCE ROW LEVEL SECURITY",
        "CREATE POLICY tenant_isolation ON firms USING (id = recallops_current_firm()) "
        "WITH CHECK (id = recallops_current_firm())",
    ]
    for table in TENANT_TABLES:
        statements += [
            f"CREATE INDEX {table}_firm_idx ON {table} (firm_id)",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} USING (firm_id = recallops_current_firm()) "
            f"WITH CHECK (firm_id = recallops_current_firm())",
        ]
    return ";\n".join(statements) + ";"


def _grants_sql() -> str:
    tenant_rw = ", ".join(t for t in ("firms", *TENANT_TABLES) if t != "audit_log")
    everything = ", ".join(("firms", *TENANT_TABLES, *PUBLIC_TABLES))
    return f"""
    GRANT SELECT ON recalls, feed_runs TO recallops_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON {tenant_rw} TO recallops_app;
    GRANT SELECT, INSERT ON audit_log TO recallops_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON {everything} TO recallops_internal;
    GRANT EXECUTE ON FUNCTION recallops_current_firm() TO recallops_app, recallops_internal;
    """


def upgrade() -> None:
    op.execute(DDL)
    op.execute(_rls_sql())
    op.execute(_grants_sql())


def downgrade() -> None:
    for table in (
        "usage_meters",
        "audit_log",
        "mpr_reports",
        "payouts",
        "fraud_clusters",
        "agent_steps",
        "agent_runs",
        "claim_photos",
        "claims",
        "documents",
        "recall_programs",
        "firm_members",
        "firms",
        "stripe_events",
        "feed_runs",
        "recalls",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS agent_steps_firm_matches_run()")
    op.execute("DROP FUNCTION IF EXISTS recallops_current_firm()")
