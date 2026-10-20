"""Durable billing and Connect recovery, with tenant constraints and immutable provider requests."""

from alembic import op

revision = "0006_payments"
down_revision = "0005_m5_campaigns"
branch_labels = None
depends_on = None

TABLES = ("billing_accounts", "billing_invoices", "claim_usage", "run_usage_costs", "usage_reports")

UP = r"""
CREATE UNIQUE INDEX firms_stripe_customer_unique ON firms(stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_usage_identity UNIQUE(id, firm_id, claim_id);

CREATE TABLE billing_accounts (
    firm_id uuid PRIMARY KEY REFERENCES firms(id) ON DELETE CASCADE,
    subscription_id text UNIQUE,
    subscription_status text,
    paid_access boolean NOT NULL DEFAULT false,
    program_item_id text,
    checkout_attempt uuid,
    checkout_started_at timestamptz,
    checkout_session_id text UNIQUE,
    checkout_expires_at timestamptz,
    checkout_request jsonb,
    customer_started_at timestamptz,
    quantity_version integer NOT NULL DEFAULT 0 CHECK(quantity_version >= 0),
    quantity_synced_version integer NOT NULL DEFAULT -1 CHECK(quantity_synced_version >= -1),
    quantity_request jsonb,
    last_sync_error text
);
CREATE TABLE billing_invoices (
    id text PRIMARY KEY,
    firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    subscription_id text NOT NULL,
    status text NOT NULL,
    attempt_count integer NOT NULL CHECK(attempt_count >= 0),
    amount_due_cents integer NOT NULL CHECK(amount_due_cents >= 0),
    amount_paid_cents integer NOT NULL CHECK(amount_paid_cents >= 0),
    currency text NOT NULL,
    created_at timestamptz NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL CHECK(period_end >= period_start)
);
CREATE INDEX billing_invoices_subscription_idx ON billing_invoices(firm_id, subscription_id, status);
CREATE INDEX billing_invoices_created_idx ON billing_invoices(firm_id, created_at);
CREATE TABLE claim_usage (
    firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    claim_id uuid NOT NULL,
    period_start date NOT NULL,
    run_id uuid NOT NULL,
    counted_at timestamptz NOT NULL,
    growth_billable boolean NOT NULL,
    PRIMARY KEY(firm_id, claim_id, period_start),
    FOREIGN KEY(claim_id, firm_id) REFERENCES claims(id, firm_id) ON DELETE CASCADE,
    FOREIGN KEY(run_id, firm_id, claim_id) REFERENCES agent_runs(id, firm_id, claim_id) ON DELETE CASCADE,
    CHECK(period_start = date_trunc('month', counted_at AT TIME ZONE 'UTC')::date)
);
CREATE INDEX claim_usage_counted_idx ON claim_usage(firm_id, counted_at);
CREATE INDEX claim_usage_run_idx ON claim_usage(firm_id, run_id);
CREATE TABLE run_usage_costs (
    run_id uuid PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
    firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    period_start date NOT NULL CHECK(extract(day FROM period_start) = 1),
    recorded_cost numeric(10,6) NOT NULL DEFAULT 0 CHECK(recorded_cost >= 0)
);
CREATE TABLE usage_reports (
    firm_id uuid NOT NULL REFERENCES firms(id) ON DELETE CASCADE,
    day date NOT NULL,
    request jsonb NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    reported_at timestamptz,
    last_error text,
    PRIMARY KEY(firm_id, day)
);

ALTER TABLE payouts
    ADD COLUMN connect_started_at timestamptz,
    ADD COLUMN connect_ready boolean NOT NULL DEFAULT false,
    ADD COLUMN transfer_started_at timestamptz,
    ADD COLUMN transfer_retry_at timestamptz,
    ADD COLUMN transfer_request jsonb,
    ADD COLUMN stripe_reversed_cents integer NOT NULL DEFAULT 0
        CHECK(stripe_reversed_cents BETWEEN 0 AND amount_cents);
CREATE UNIQUE INDEX payouts_connect_account_unique ON payouts(connect_account_id) WHERE connect_account_id IS NOT NULL;
CREATE UNIQUE INDEX payouts_transfer_unique ON payouts(stripe_transfer_id) WHERE stripe_transfer_id IS NOT NULL;
CREATE INDEX payouts_recovery_idx ON payouts(status, transfer_retry_at)
    WHERE status IN ('onboarding', 'failed', 'transferring');

CREATE POLICY owner_connect_lookup ON payouts FOR SELECT TO recallops_owner USING(true);
CREATE FUNCTION recallops_firm_by_connect_account(account text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT firm_id FROM public.payouts WHERE connect_account_id = account
$$;
REVOKE ALL ON FUNCTION recallops_firm_by_connect_account(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION recallops_firm_by_connect_account(text) TO recallops_app;

CREATE FUNCTION payments_immutable_facts() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF TG_TABLE_NAME = 'claim_usage' THEN
        IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'claim usage identity and eligibility are immutable';
        END IF;
    ELSIF TG_TABLE_NAME = 'run_usage_costs' THEN
        IF NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.firm_id IS DISTINCT FROM OLD.firm_id
            OR NEW.period_start IS DISTINCT FROM OLD.period_start OR NEW.recorded_cost < OLD.recorded_cost THEN
            RAISE EXCEPTION 'run usage identity is immutable and cost cannot decrease';
        END IF;
    ELSIF TG_TABLE_NAME = 'usage_reports' THEN
        IF NEW.firm_id IS DISTINCT FROM OLD.firm_id OR NEW.day IS DISTINCT FROM OLD.day
            OR NEW.request IS DISTINCT FROM OLD.request OR NEW.started_at IS DISTINCT FROM OLD.started_at
            OR (OLD.reported_at IS NOT NULL AND NEW.reported_at IS DISTINCT FROM OLD.reported_at) THEN
            RAISE EXCEPTION 'usage reporting request and successful receipt are immutable';
        END IF;
    ELSIF TG_TABLE_NAME = 'billing_accounts' THEN
        IF NEW.firm_id IS DISTINCT FROM OLD.firm_id
            OR (OLD.checkout_request IS NOT NULL AND NEW.checkout_attempt IS NOT DISTINCT FROM OLD.checkout_attempt
                AND NEW.checkout_request IS DISTINCT FROM OLD.checkout_request)
            OR (OLD.quantity_request IS NOT NULL AND NEW.quantity_request IS NOT NULL
                AND NEW.quantity_request->'version' IS NOT DISTINCT FROM OLD.quantity_request->'version'
                AND NEW.quantity_request IS DISTINCT FROM OLD.quantity_request) THEN
            RAISE EXCEPTION 'billing request parameters are immutable for the same operation';
        END IF;
    ELSIF TG_TABLE_NAME = 'payouts' THEN
        IF OLD.transfer_request IS NOT NULL AND (
            NEW.transfer_request IS DISTINCT FROM OLD.transfer_request
            OR NEW.amount_cents IS DISTINCT FROM OLD.amount_cents OR NEW.currency IS DISTINCT FROM OLD.currency
            OR NEW.connect_account_id IS DISTINCT FROM OLD.connect_account_id
            OR NEW.firm_id IS DISTINCT FROM OLD.firm_id OR NEW.claim_id IS DISTINCT FROM OLD.claim_id
            OR (NEW.method IS DISTINCT FROM OLD.method AND NOT (
                OLD.status = 'failed' AND NEW.status = 'pending_export'
                AND NEW.method = 'check_export' AND OLD.stripe_transfer_id IS NULL))) THEN
            RAISE EXCEPTION 'dispatched transfer identity and money fields are immutable';
        END IF;
        IF OLD.status = 'transferring' AND NEW.status = 'transferring'
            AND OLD.transfer_started_at IS NOT NULL
            AND NEW.transfer_started_at IS DISTINCT FROM OLD.transfer_started_at THEN
            RAISE EXCEPTION 'an unknown transfer retains its first request time';
        END IF;
        IF NEW.stripe_reversed_cents < OLD.stripe_reversed_cents THEN
            RAISE EXCEPTION 'cumulative reversed cents cannot decrease';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER payments_immutable BEFORE UPDATE ON payouts
    FOR EACH ROW EXECUTE FUNCTION payments_immutable_facts();

CREATE FUNCTION run_usage_tenant_check() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.agent_runs WHERE id = NEW.run_id AND firm_id = NEW.firm_id) THEN
        RAISE EXCEPTION 'run usage firm must equal agent_runs.firm_id';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER run_usage_tenant_check BEFORE INSERT OR UPDATE ON run_usage_costs
    FOR EACH ROW EXECUTE FUNCTION run_usage_tenant_check();
"""


def upgrade() -> None:
    op.execute(UP)
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; "
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY; "
            f"CREATE POLICY tenant_isolation ON {table} USING(firm_id = recallops_current_firm()) "
            "WITH CHECK(firm_id = recallops_current_firm()); "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO recallops_internal; "
            f"GRANT SELECT, INSERT ON {table} TO recallops_app;"
        )
        if table != "billing_invoices":
            op.execute(
                f"CREATE TRIGGER payments_immutable BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION payments_immutable_facts()"
            )
        if table != "claim_usage":
            op.execute(f"GRANT UPDATE ON {table} TO recallops_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION recallops_firm_by_connect_account(text)")
    op.execute("DROP POLICY owner_connect_lookup ON payouts")
    op.execute("DROP TRIGGER payments_immutable ON payouts")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION payments_immutable_facts(); DROP FUNCTION run_usage_tenant_check()")
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_usage_identity")
    op.execute(
        "DROP INDEX firms_stripe_customer_unique; DROP INDEX payouts_connect_account_unique; "
        "DROP INDEX payouts_transfer_unique; DROP INDEX payouts_recovery_idx"
    )
    op.execute(
        "ALTER TABLE payouts DROP COLUMN connect_started_at, DROP COLUMN connect_ready, "
        "DROP COLUMN transfer_started_at, DROP COLUMN transfer_retry_at, DROP COLUMN transfer_request, "
        "DROP COLUMN stripe_reversed_cents"
    )
