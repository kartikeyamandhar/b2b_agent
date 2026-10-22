"""Company verification: an organization runs a recall only after the platform operator approves it (2026-10-22).

The first live deploy let any signed-up organization open a program for any recall and issue claim links in that
company's name. Now every firm requests access to a recall (or to a typed notice); the operator approves, rejects, or
revokes; and at most one organization holds an approved request per recall (a unique index, so a race cannot give a
recall two owners).

recall_access is a tenant table (forced RLS like the others). A firm reads and creates its own requests; the app role
may insert only the request columns (column grants), so a firm can never write an approved row. Decisions and the
operator's cross-firm list go through two SECURITY DEFINER functions owned by recallops_owner, which reach every row
through an owner-only policy; the API calls them only after checking the caller is an operator. Existing programs get
a pending request each (requested_by 'migration'), so the operator decides them like any other.
"""

from alembic import op

revision = "0008_recall_access"
down_revision = "0007_login"
branch_labels = None
depends_on = None

UP = r"""
CREATE TABLE recall_access (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    firm_id uuid NOT NULL REFERENCES firms (id),
    recall_id uuid REFERENCES recalls (id),
    typed_title text CHECK (typed_title IS NULL OR length(typed_title) BETWEEN 1 AND 300),
    typed_company text CHECK (typed_company IS NULL OR length(typed_company) BETWEEN 1 AND 200),
    typed_notice text CHECK (typed_notice IS NULL OR length(typed_notice) <= 20000),
    status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'approved', 'rejected', 'revoked')),
    requested_by text NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 128),
    requester_domain text CHECK (requester_domain IS NULL OR length(requester_domain) <= 253),
    decided_by text CHECK (decided_by IS NULL OR length(decided_by) <= 128),
    decided_at timestamptz,
    note text CHECK (note IS NULL OR length(note) <= 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((recall_id IS NULL) <> (typed_title IS NULL)),
    CHECK ((typed_title IS NULL) = (typed_company IS NULL))
);
CREATE UNIQUE INDEX recall_access_one_owner ON recall_access (recall_id)
    WHERE status = 'approved' AND recall_id IS NOT NULL;
CREATE UNIQUE INDEX recall_access_one_open ON recall_access (firm_id, recall_id)
    WHERE status IN ('requested', 'approved') AND recall_id IS NOT NULL;
CREATE INDEX recall_access_by_status ON recall_access (status, created_at);

ALTER TABLE recall_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE recall_access FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON recall_access USING (firm_id = recallops_current_firm())
    WITH CHECK (firm_id = recallops_current_firm());
CREATE POLICY owner_all ON recall_access TO recallops_owner USING (true) WITH CHECK (true);
GRANT SELECT ON recall_access TO recallops_app;
GRANT INSERT (firm_id, recall_id, typed_title, typed_company, typed_notice, requested_by, requester_domain)
    ON recall_access TO recallops_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON recall_access TO recallops_internal;

ALTER TABLE recall_programs ADD COLUMN access_id uuid REFERENCES recall_access (id);
CREATE UNIQUE INDEX recall_programs_one_per_typed_access ON recall_programs (access_id) WHERE access_id IS NOT NULL;

CREATE FUNCTION recallops_access_requests() RETURNS TABLE (
    id uuid, firm_id uuid, firm_name text, recall_id uuid, typed_title text, typed_company text, typed_notice text,
    status text, requested_by text, requester_domain text, decided_by text, decided_at timestamptz, note text,
    created_at timestamptz, approved_elsewhere text
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT a.id, a.firm_id, f.name, a.recall_id, a.typed_title, a.typed_company, left(a.typed_notice, 4000),
           a.status, a.requested_by, a.requester_domain, a.decided_by, a.decided_at, a.note, a.created_at,
           (SELECT f2.name FROM public.recall_access a2 JOIN public.firms f2 ON f2.id = a2.firm_id
             WHERE a.recall_id IS NOT NULL AND a2.recall_id = a.recall_id AND a2.status = 'approved'
               AND a2.id <> a.id LIMIT 1)
      FROM public.recall_access a JOIN public.firms f ON f.id = a.firm_id
     ORDER BY (a.status = 'requested') DESC, a.created_at DESC
     LIMIT 500
$$;

CREATE FUNCTION recallops_decide_access(p_id uuid, p_status text, p_by text, p_note text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    cur text;
    owner_firm uuid;
BEGIN
    IF p_by IS NULL OR length(p_by) = 0 OR length(p_by) > 128 THEN
        RAISE EXCEPTION 'invalid operator id';
    END IF;
    SELECT status, firm_id INTO cur, owner_firm FROM public.recall_access WHERE id = p_id FOR UPDATE;
    IF cur IS NULL THEN
        RAISE EXCEPTION 'access request not found' USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT ((cur = 'requested' AND p_status IN ('approved', 'rejected')) OR (cur = 'approved' AND p_status = 'revoked'))
    THEN
        RAISE EXCEPTION 'an access request cannot move from % to %', cur, p_status USING ERRCODE = 'check_violation';
    END IF;
    UPDATE public.recall_access
       SET status = p_status, decided_by = p_by, decided_at = now(), note = left(nullif(p_note, ''), 500)
     WHERE id = p_id;
    RETURN owner_firm;
END $$;

REVOKE ALL ON FUNCTION recallops_access_requests() FROM PUBLIC;
REVOKE ALL ON FUNCTION recallops_decide_access(uuid, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_access_requests() TO recallops_app;
GRANT EXECUTE ON FUNCTION recallops_decide_access(uuid, text, text, text) TO recallops_app;
"""

# Data statements run as recallops_owner, which FORCE ROW LEVEL SECURITY binds; the owner_all policy above lets the
# owner write recall_access, and recall_programs is read with FORCE lifted for this statement only (decisions log
# 2026-10-18: a forced table hides its rows from its owner).
GRANDFATHER = r"""
ALTER TABLE recall_programs NO FORCE ROW LEVEL SECURITY;
INSERT INTO recall_access (firm_id, recall_id, requested_by)
SELECT DISTINCT p.firm_id, p.recall_id, 'migration' FROM recall_programs p WHERE p.recall_id IS NOT NULL;
ALTER TABLE recall_programs FORCE ROW LEVEL SECURITY;
"""

DOWN = r"""
DROP FUNCTION IF EXISTS recallops_decide_access(uuid, text, text, text);
DROP FUNCTION IF EXISTS recallops_access_requests();
DROP INDEX IF EXISTS recall_programs_one_per_typed_access;
ALTER TABLE recall_programs DROP COLUMN IF EXISTS access_id;
DROP TABLE IF EXISTS recall_access;
"""


def upgrade() -> None:
    op.execute(UP)
    op.execute(GRANDFATHER)


def downgrade() -> None:
    op.execute(DOWN)
