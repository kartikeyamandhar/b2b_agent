"""M3 security hardening (red-team findings, decisions log 2026-10-18).

- The API runs only as recallops_app (invariant 2). What it used to read through the internal role now goes through
  narrow SECURITY DEFINER functions owned by recallops_owner: the global model spend total (the meter's budget
  stop), and two Stripe helpers for the payments owner's webhook (record an event once, resolve a firm by its
  Stripe customer id). FORCE RLS applies to the owner as well, so the owner gets an explicit all-rows policy on
  the tables those functions read.
- The nightly feed pull runs in the API under a shared secret: recallops_app gets INSERT and UPDATE on the public
  tables recalls and feed_runs (no tenant data).
- rate_limits (non-tenant, keyed by a hash of the claim token or the client IP, no claim data): fixed-window counters
  for the portal (invariant 10). The login owner can swap the limiter; the table stays.
- audit_log is append-only: a trigger refuses UPDATE and DELETE (including the cascade from firms) unless the
  session sets app.audit_purge = on, which only reset tooling does; recallops_app loses DELETE on firms.
- Deleting an agent run deletes its LangGraph checkpoint rows (they carry claimant data and no firm_id).

Revision ID: 0003_m3_security
Revises: 0002_m2_agent
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0003_m3_security"
down_revision = "0002_m2_agent"
branch_labels = None
depends_on = None

UP = r"""
CREATE POLICY owner_all ON agent_steps TO recallops_owner USING (true) WITH CHECK (true);
CREATE POLICY owner_all ON firms TO recallops_owner USING (true) WITH CHECK (true);

CREATE FUNCTION recallops_spend_total() RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT coalesce(sum(cost_usd), 0) FROM agent_steps
$$;
REVOKE ALL ON FUNCTION recallops_spend_total() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_spend_total() TO recallops_app, recallops_internal;

CREATE FUNCTION recallops_firm_by_stripe_customer(customer text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT id FROM firms WHERE stripe_customer_id = customer
$$;
REVOKE ALL ON FUNCTION recallops_firm_by_stripe_customer(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_firm_by_stripe_customer(text) TO recallops_app;

CREATE FUNCTION recallops_record_stripe_event(event_id text, event_type text, body jsonb) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    INSERT INTO stripe_events (id, type, payload) VALUES (event_id, event_type, body) ON CONFLICT (id) DO NOTHING;
    RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION recallops_record_stripe_event(text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_record_stripe_event(text, text, jsonb) TO recallops_app;

CREATE FUNCTION recallops_mark_stripe_event_processed(event_id text) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    UPDATE stripe_events SET processed_at = now() WHERE id = event_id
$$;
REVOKE ALL ON FUNCTION recallops_mark_stripe_event_processed(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_mark_stripe_event_processed(text) TO recallops_app;

GRANT INSERT, UPDATE ON recalls, feed_runs TO recallops_app;

CREATE TABLE rate_limits (
    key text NOT NULL,
    window_start timestamptz NOT NULL,
    count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (key, window_start)
);
CREATE INDEX rate_limits_window_idx ON rate_limits (window_start);
GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limits TO recallops_app, recallops_internal;

CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF coalesce(current_setting('app.audit_purge', true), '') <> 'on' THEN
        RAISE EXCEPTION 'audit_log is append-only';
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
REVOKE DELETE ON firms FROM recallops_app;

CREATE FUNCTION agent_runs_drop_checkpoints() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = langgraph, public AS $$
BEGIN
    DELETE FROM langgraph.checkpoint_writes WHERE thread_id = OLD.id::text;
    DELETE FROM langgraph.checkpoint_blobs WHERE thread_id = OLD.id::text;
    DELETE FROM langgraph.checkpoints WHERE thread_id = OLD.id::text;
    RETURN OLD;
END $$;
CREATE TRIGGER agent_runs_drop_checkpoints AFTER DELETE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION agent_runs_drop_checkpoints();
"""

DOWN = r"""
DROP TRIGGER IF EXISTS agent_runs_drop_checkpoints ON agent_runs;
DROP FUNCTION IF EXISTS agent_runs_drop_checkpoints();
GRANT DELETE ON firms TO recallops_app;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;
DROP FUNCTION IF EXISTS audit_log_append_only();
DROP TABLE IF EXISTS rate_limits;
REVOKE INSERT, UPDATE ON recalls, feed_runs FROM recallops_app;
DROP FUNCTION IF EXISTS recallops_mark_stripe_event_processed(text);
DROP FUNCTION IF EXISTS recallops_record_stripe_event(text, text, jsonb);
DROP FUNCTION IF EXISTS recallops_firm_by_stripe_customer(text);
DROP FUNCTION IF EXISTS recallops_spend_total();
DROP POLICY IF EXISTS owner_all ON firms;
DROP POLICY IF EXISTS owner_all ON agent_steps;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
