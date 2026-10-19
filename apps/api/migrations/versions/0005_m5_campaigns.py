"""M5 notification campaign tooling (01-backend.md section 8 notified_* boxes; CLAUDE.md M5).

- registrations (tenant, RLS forced): product owners a firm already knows (registration cards, warranty lists),
  uploaded as CSV. Synthetic only: the upload refuses rows that are not marked synthetic (example.* emails,
  555-01xx phones), because the project handles no real consumer data. matched and match_reasons come from the same
  pure-code identification checks the adjudicator uses. claim_id is the portal claim issued to a matched owner.
- notifications (tenant, RLS forced): one outreach per registration and channel, drafted from a template that
  quotes the recall notice verbatim, then sent. Sending is simulated (no email, mail, or SMS provider exists); the
  MPR's notified_* boxes count sent notifications.

Revision ID: 0005_m5_campaigns
Revises: 0004_m4_money_graph
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0005_m5_campaigns"
down_revision = "0004_m4_money_graph"
branch_labels = None
depends_on = None

TABLES = ("registrations", "notifications")

UP = r"""
CREATE TABLE registrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    external_ref text NOT NULL CHECK (length(external_ref) BETWEEN 1 AND 80),
    name text,
    email text,
    phone text,
    address text,
    model text,
    serial text,
    date_code text,
    purchase_date date,
    matched boolean,
    match_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    claim_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (program_id, external_ref),
    UNIQUE (id, firm_id),
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE,
    FOREIGN KEY (claim_id, firm_id) REFERENCES claims (id, firm_id)
);
CREATE INDEX registrations_program_idx ON registrations (program_id, matched);

CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    program_id uuid NOT NULL,
    registration_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('email', 'mail', 'phone_text')),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
    subject text NOT NULL,
    body text NOT NULL,
    drafted_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    UNIQUE (registration_id, channel),
    FOREIGN KEY (registration_id, firm_id) REFERENCES registrations (id, firm_id) ON DELETE CASCADE,
    FOREIGN KEY (program_id, firm_id) REFERENCES recall_programs (id, firm_id) ON DELETE CASCADE,
    CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);
CREATE INDEX notifications_program_idx ON notifications (program_id, channel, status);
"""


def _rls() -> str:
    statements = []
    for table in TABLES:
        statements += [
            f"CREATE INDEX {table}_firm_idx ON {table} (firm_id)",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY tenant_isolation ON {table} USING (firm_id = recallops_current_firm()) "
            "WITH CHECK (firm_id = recallops_current_firm())",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO recallops_app, recallops_internal",
        ]
    return ";\n".join(statements) + ";"


def upgrade() -> None:
    op.execute(UP)
    op.execute(_rls())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS registrations")
