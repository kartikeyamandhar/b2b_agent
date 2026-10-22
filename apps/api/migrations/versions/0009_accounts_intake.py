"""One account, one company; one claim link per product (owner decisions 2026-10-22).

Clerk organizations are gone: a company is the signed-in account, found by its Clerk user id through two SECURITY
DEFINER functions (the app role never bypasses RLS, and the firm row does not exist at an account's first sign-in).
Only sign-in and jobs may set which account or organization a firm belongs to, on insert or update (a trigger refuses
the app role, so a firm can never capture another account's first sign-in). firms.onboarded_at records the company's
name and plan step.

Each product has one public claim link, signed over (firm, program, intake_version); replacing the link increments the
version. A company runs at most one live product per recall (a partial unique index; older duplicates the previous
code allowed are closed first), so revoking and approving again reuses the product instead of adding a second one.
Access requests keep the requester's email and whether Clerk had verified it, for the operator's window, which also
counts each company's products through a read-only owner policy. payouts.choice_opened_at is when a refund last became
the claimant's to choose (on approval, or when a hold is released): the deadline after which it becomes a mailed check
runs from it.
"""

from alembic import op

revision = "0009_accounts_intake"
down_revision = "0008_recall_access"
branch_labels = None
depends_on = None

UP = r"""
ALTER TABLE firms ADD COLUMN clerk_user_id text CHECK (clerk_user_id IS NULL OR clerk_user_id ~ '^user_[A-Za-z0-9]{1,64}$');
ALTER TABLE firms ADD CONSTRAINT firms_clerk_user_id_key UNIQUE (clerk_user_id);
ALTER TABLE firms ADD COLUMN onboarded_at timestamptz;

CREATE FUNCTION recallops_firms_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.clerk_user_id IS DISTINCT FROM OLD.clerk_user_id OR NEW.clerk_org_id IS DISTINCT FROM OLD.clerk_org_id)
       AND current_user NOT IN ('recallops_owner', 'recallops_internal') THEN
        RAISE EXCEPTION 'a firm''s account is set by sign-in only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION recallops_firms_identity_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.clerk_user_id IS NOT NULL OR NEW.clerk_org_id IS NOT NULL)
       AND current_user NOT IN ('recallops_owner', 'recallops_internal') THEN
        RAISE EXCEPTION 'a firm''s account is set by sign-in only' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER firms_identity_guard BEFORE UPDATE ON firms FOR EACH ROW EXECUTE FUNCTION recallops_firms_identity_guard();
CREATE TRIGGER firms_identity_insert_guard BEFORE INSERT ON firms
    FOR EACH ROW EXECUTE FUNCTION recallops_firms_identity_insert_guard();

CREATE FUNCTION recallops_firm_of_clerk_user(user_id text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT id FROM public.firms WHERE clerk_user_id = user_id
$$;

CREATE FUNCTION recallops_firm_for_clerk_user(user_id text, firm_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    found_id uuid;
BEGIN
    IF user_id IS NULL OR user_id !~ '^user_[A-Za-z0-9]{1,64}$' THEN
        RAISE EXCEPTION 'invalid Clerk user id';
    END IF;
    INSERT INTO public.firms (name, clerk_user_id)
        VALUES (left(coalesce(nullif(firm_name, ''), 'New company'), 200), user_id)
        ON CONFLICT (clerk_user_id) DO NOTHING;
    SELECT id INTO found_id FROM public.firms WHERE clerk_user_id = user_id;
    RETURN found_id;
END $$;

REVOKE ALL ON FUNCTION recallops_firm_of_clerk_user(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION recallops_firm_for_clerk_user(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_firm_of_clerk_user(text) TO recallops_app;
GRANT EXECUTE ON FUNCTION recallops_firm_for_clerk_user(text, text) TO recallops_app;

DROP FUNCTION IF EXISTS recallops_firm_for_clerk_org(text, text);

ALTER TABLE recall_programs ADD COLUMN intake_version integer NOT NULL DEFAULT 1 CHECK (intake_version >= 1);
-- The previous code let a firm open two programs for one CPSC recall; keep the newest live one. Data statements run as
-- recallops_owner, which FORCE ROW LEVEL SECURITY binds (decisions log 2026-10-18), so FORCE is lifted around them.
ALTER TABLE recall_programs NO FORCE ROW LEVEL SECURITY;
UPDATE recall_programs p SET status = 'closed', closed_at = now()
 WHERE p.recall_id IS NOT NULL AND p.status <> 'closed'
   AND EXISTS (SELECT 1 FROM recall_programs q WHERE q.firm_id = p.firm_id AND q.recall_id = p.recall_id
               AND q.status <> 'closed' AND (q.created_at, q.id) > (p.created_at, p.id));
ALTER TABLE recall_programs FORCE ROW LEVEL SECURITY;
CREATE UNIQUE INDEX recall_programs_one_live_per_recall ON recall_programs (firm_id, recall_id)
    WHERE recall_id IS NOT NULL AND status <> 'closed';
CREATE POLICY owner_read ON recall_programs FOR SELECT TO recallops_owner USING (true);

ALTER TABLE payouts ADD COLUMN choice_opened_at timestamptz;

ALTER TABLE recall_access ADD COLUMN requester_email text CHECK (requester_email IS NULL OR length(requester_email) <= 320);
ALTER TABLE recall_access ADD COLUMN requester_email_verified boolean;
GRANT INSERT (requester_email, requester_email_verified) ON recall_access TO recallops_app;

DROP FUNCTION IF EXISTS recallops_access_requests();
CREATE FUNCTION recallops_access_requests() RETURNS TABLE (
    id uuid, firm_id uuid, firm_name text, recall_id uuid, typed_title text, typed_company text, typed_notice text,
    status text, requested_by text, requester_domain text, decided_by text, decided_at timestamptz, note text,
    created_at timestamptz, approved_elsewhere text, requester_email text, requester_email_verified boolean,
    firm_plan text, firm_active_products bigint
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT a.id, a.firm_id, f.name, a.recall_id, a.typed_title, a.typed_company, left(a.typed_notice, 4000),
           a.status, a.requested_by, a.requester_domain, a.decided_by, a.decided_at, a.note, a.created_at,
           (SELECT f2.name FROM public.recall_access a2 JOIN public.firms f2 ON f2.id = a2.firm_id
             WHERE a.recall_id IS NOT NULL AND a2.recall_id = a.recall_id AND a2.status = 'approved'
               AND a2.id <> a.id LIMIT 1),
           a.requester_email, a.requester_email_verified, f.plan,
           (SELECT count(*) FROM public.recall_programs p WHERE p.firm_id = a.firm_id AND p.status IN ('draft', 'open')
               AND EXISTS (SELECT 1 FROM public.recall_access a3 WHERE a3.firm_id = p.firm_id AND a3.status = 'approved'
                   AND (a3.recall_id = p.recall_id OR (p.recall_id IS NULL AND a3.id = p.access_id))))
      FROM public.recall_access a JOIN public.firms f ON f.id = a.firm_id
     ORDER BY (a.status = 'requested') DESC, a.created_at DESC
     LIMIT 500
$$;
REVOKE ALL ON FUNCTION recallops_access_requests() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_access_requests() TO recallops_app;
"""

DOWN = r"""
DROP FUNCTION IF EXISTS recallops_access_requests();
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
REVOKE ALL ON FUNCTION recallops_access_requests() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_access_requests() TO recallops_app;
REVOKE INSERT (requester_email, requester_email_verified) ON recall_access FROM recallops_app;
ALTER TABLE recall_access DROP COLUMN IF EXISTS requester_email_verified;
ALTER TABLE recall_access DROP COLUMN IF EXISTS requester_email;
ALTER TABLE payouts DROP COLUMN IF EXISTS choice_opened_at;
DROP POLICY IF EXISTS owner_read ON recall_programs;
DROP INDEX IF EXISTS recall_programs_one_live_per_recall;
ALTER TABLE recall_programs DROP COLUMN IF EXISTS intake_version;
CREATE FUNCTION recallops_firm_for_clerk_org(org_id text, org_name text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    found_id uuid;
BEGIN
    IF org_id IS NULL OR length(org_id) = 0 OR length(org_id) > 128 THEN
        RAISE EXCEPTION 'invalid Clerk organization id';
    END IF;
    INSERT INTO public.firms (name, clerk_org_id) VALUES (left(coalesce(nullif(org_name, ''), org_id), 200), org_id)
        ON CONFLICT (clerk_org_id) DO NOTHING;
    SELECT id INTO found_id FROM public.firms WHERE clerk_org_id = org_id;
    RETURN found_id;
END $$;
REVOKE ALL ON FUNCTION recallops_firm_for_clerk_org(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_firm_for_clerk_org(text, text) TO recallops_app;
DROP FUNCTION IF EXISTS recallops_firm_for_clerk_user(text, text);
DROP FUNCTION IF EXISTS recallops_firm_of_clerk_user(text);
DROP TRIGGER IF EXISTS firms_identity_insert_guard ON firms;
DROP FUNCTION IF EXISTS recallops_firms_identity_insert_guard();
DROP TRIGGER IF EXISTS firms_identity_guard ON firms;
DROP FUNCTION IF EXISTS recallops_firms_identity_guard();
ALTER TABLE firms DROP COLUMN IF EXISTS onboarded_at;
ALTER TABLE firms DROP CONSTRAINT IF EXISTS firms_clerk_user_id_key;
ALTER TABLE firms DROP COLUMN IF EXISTS clerk_user_id;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
