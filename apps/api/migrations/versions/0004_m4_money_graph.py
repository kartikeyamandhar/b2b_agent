"""M4 money path and the Neo4j fraud graph (red-team findings on the completion plan, decisions log 2026-10-18).

- payouts: status held (a claim left approved, or joined a duplicate group, before money moved) and transferring
  (component 3 claimed the row for a Stripe transfer under the row lock; a hold that lands then is recorded in
  hold_requested and applied when the result arrives). export_batch and connect_account_id are stored.
- export_batches (tenant, RLS forced): the check/ACH batch file lives in Postgres, written in the same transaction
  that marks its payouts exported, so a committed batch can never lose its file (ephemeral disks, concurrent runs).
- claims: first_submitted_at (the MPR's contact date; a needs_info answer no longer moves it) and the graph dirty
  counter. graph_version is bumped by triggers whenever a fact the fraud graph projects changes (claimant fields,
  typed fields, leaving draft, a photo added or removed), so no call site can forget it; the projection records
  the version it read in graph_synced_version only if no change landed meanwhile.
- fraud_clusters: kind same_mailing (claims whose payout mailing address is shared); a trigger refuses a member that
  is not a claim of the cluster's own firm and program, so graph output can never write another tenant's id.
- graph_state (non-tenant, no claim data): the epoch shared with the Neo4j GraphMeta node; a mismatch (an emptied
  or replaced graph) marks every claim dirty.

Revision ID: 0004_m4_money_graph
Revises: 0003_m3_security
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op

revision = "0004_m4_money_graph"
down_revision = "0003_m3_security"
branch_labels = None
depends_on = None

UP = r"""
ALTER TABLE payouts DROP CONSTRAINT payouts_status_check;
ALTER TABLE payouts ADD CONSTRAINT payouts_status_check CHECK (status IN (
    'pending', 'pending_export', 'onboarding', 'transferring', 'transferred', 'failed', 'exported', 'held'));
ALTER TABLE payouts ADD COLUMN export_batch uuid;
ALTER TABLE payouts ADD COLUMN connect_account_id text;
ALTER TABLE payouts ADD COLUMN hold_requested boolean NOT NULL DEFAULT false;

CREATE TABLE export_batches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL,
    row_count integer NOT NULL CHECK (row_count > 0),
    total_cents bigint NOT NULL CHECK (total_cents >= 0),
    sha256 text NOT NULL,
    csv bytea NOT NULL,
    UNIQUE (id, firm_id)
);
CREATE INDEX export_batches_firm_idx ON export_batches (firm_id, created_at);
ALTER TABLE export_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE export_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON export_batches USING (firm_id = recallops_current_firm())
    WITH CHECK (firm_id = recallops_current_firm());
GRANT SELECT, INSERT ON export_batches TO recallops_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON export_batches TO recallops_internal;
ALTER TABLE payouts ADD CONSTRAINT payouts_export_batch_fk
    FOREIGN KEY (export_batch, firm_id) REFERENCES export_batches (id, firm_id);

ALTER TABLE claims ADD COLUMN first_submitted_at timestamptz;
-- A needs_info answer used to move submitted_at, so the earliest claim_submitted audit row is the better first date.
-- FORCE ROW LEVEL SECURITY binds the owner too, so a data statement here would see no rows: lift it for the
-- backfill, inside this migration's transaction, and restore it (the meta-test checks every tenant table).
ALTER TABLE claims NO FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log NO FORCE ROW LEVEL SECURITY;
UPDATE claims c SET first_submitted_at = least(c.submitted_at, (
    SELECT min(a.at) FROM audit_log a
    WHERE a.firm_id = c.firm_id AND a.target_type = 'claim' AND a.target_id = c.id::text AND a.action = 'claim_submitted'
)) WHERE c.submitted_at IS NOT NULL;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
ALTER TABLE claims FORCE ROW LEVEL SECURITY;
ALTER TABLE claims ADD COLUMN graph_version integer NOT NULL DEFAULT 0;
ALTER TABLE claims ADD COLUMN graph_synced_version integer;
CREATE INDEX claims_graph_dirty_idx ON claims (program_id)
    WHERE status <> 'draft' AND graph_synced_version IS DISTINCT FROM graph_version;

CREATE FUNCTION claims_track_changes() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.first_submitted_at IS NULL AND NEW.submitted_at IS NOT NULL THEN
        NEW.first_submitted_at := NEW.submitted_at;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.claimant IS DISTINCT FROM OLD.claimant
        OR NEW.model_claimed IS DISTINCT FROM OLD.model_claimed
        OR NEW.serial_claimed IS DISTINCT FROM OLD.serial_claimed
        OR NEW.date_code_claimed IS DISTINCT FROM OLD.date_code_claimed
        OR NEW.retailer_claimed IS DISTINCT FROM OLD.retailer_claimed
        OR NEW.purchase_date IS DISTINCT FROM OLD.purchase_date
        OR (OLD.status = 'draft' AND NEW.status <> 'draft')
    ) THEN
        NEW.graph_version := OLD.graph_version + 1;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER claims_track_changes BEFORE INSERT OR UPDATE ON claims
    FOR EACH ROW EXECUTE FUNCTION claims_track_changes();

CREATE FUNCTION claim_photos_bump_graph_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE claims SET graph_version = graph_version + 1
    WHERE id = coalesce(NEW.claim_id, OLD.claim_id) AND firm_id = coalesce(NEW.firm_id, OLD.firm_id);
    RETURN NULL;
END $$;
CREATE TRIGGER claim_photos_bump_graph_version AFTER INSERT OR DELETE ON claim_photos
    FOR EACH ROW EXECUTE FUNCTION claim_photos_bump_graph_version();

ALTER TABLE fraud_clusters DROP CONSTRAINT fraud_clusters_kind_check;
ALTER TABLE fraud_clusters ADD CONSTRAINT fraud_clusters_kind_check
    CHECK (kind IN ('same_photo', 'same_address', 'same_payout', 'same_serial', 'same_mailing'));
CREATE INDEX fraud_clusters_members_idx ON fraud_clusters USING gin (member_claim_ids);

CREATE FUNCTION fraud_clusters_members_in_program() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM unnest(NEW.member_claim_ids) AS m(id)
        WHERE NOT EXISTS (
            SELECT 1 FROM claims c WHERE c.id = m.id AND c.firm_id = NEW.firm_id AND c.program_id = NEW.program_id
        )
    ) THEN
        RAISE EXCEPTION 'fraud cluster member outside firm % program %', NEW.firm_id, NEW.program_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fraud_clusters_members_in_program BEFORE INSERT OR UPDATE ON fraud_clusters
    FOR EACH ROW EXECUTE FUNCTION fraud_clusters_members_in_program();

CREATE TABLE graph_state (
    key text PRIMARY KEY,
    value text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON graph_state TO recallops_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON graph_state TO recallops_internal;
"""

DOWN = r"""
ALTER TABLE fraud_clusters NO FORCE ROW LEVEL SECURITY;
ALTER TABLE payouts NO FORCE ROW LEVEL SECURITY;
DROP TABLE IF EXISTS graph_state;
DROP TRIGGER IF EXISTS fraud_clusters_members_in_program ON fraud_clusters;
DROP FUNCTION IF EXISTS fraud_clusters_members_in_program();
DROP INDEX IF EXISTS fraud_clusters_members_idx;
DELETE FROM fraud_clusters WHERE kind = 'same_mailing';
ALTER TABLE fraud_clusters DROP CONSTRAINT fraud_clusters_kind_check;
ALTER TABLE fraud_clusters ADD CONSTRAINT fraud_clusters_kind_check
    CHECK (kind IN ('same_photo', 'same_address', 'same_payout', 'same_serial'));
DROP TRIGGER IF EXISTS claim_photos_bump_graph_version ON claim_photos;
DROP FUNCTION IF EXISTS claim_photos_bump_graph_version();
DROP TRIGGER IF EXISTS claims_track_changes ON claims;
DROP FUNCTION IF EXISTS claims_track_changes();
DROP INDEX IF EXISTS claims_graph_dirty_idx;
ALTER TABLE claims DROP COLUMN IF EXISTS graph_synced_version;
ALTER TABLE claims DROP COLUMN IF EXISTS graph_version;
ALTER TABLE claims DROP COLUMN IF EXISTS first_submitted_at;
ALTER TABLE payouts DROP CONSTRAINT IF EXISTS payouts_export_batch_fk;
DROP TABLE IF EXISTS export_batches;
ALTER TABLE payouts DROP COLUMN IF EXISTS hold_requested;
ALTER TABLE payouts DROP COLUMN IF EXISTS connect_account_id;
ALTER TABLE payouts DROP COLUMN IF EXISTS export_batch;
UPDATE payouts SET status = 'pending' WHERE status IN ('held', 'transferring');
ALTER TABLE payouts DROP CONSTRAINT payouts_status_check;
ALTER TABLE payouts ADD CONSTRAINT payouts_status_check CHECK (status IN (
    'pending', 'pending_export', 'onboarding', 'transferred', 'failed', 'exported'));
ALTER TABLE payouts FORCE ROW LEVEL SECURITY;
ALTER TABLE fraud_clusters FORCE ROW LEVEL SECURITY;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
