"""Login (component 4): resolve a Clerk organization to its firm on first sight.

The API runs as recallops_app and never uses the BYPASSRLS role (tests/api/test_security.py), but a firm's row does
not exist yet when its first user signs in, so RLS would hide it. This SECURITY DEFINER function, owned by
recallops_owner (which has the owner_all policy on firms since 0003), finds or creates the firm and returns only its
id. The member-role mirror is then written by the app role inside a normal tenant session.
"""

from alembic import op

revision = "0007_login"
down_revision = "0006_payments"
branch_labels = None
depends_on = None

UP = r"""
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
"""

DOWN = r"""
DROP FUNCTION IF EXISTS recallops_firm_for_clerk_org(text, text);
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
