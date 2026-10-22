"""Application settings. Every variable is documented here and listed in .env.example with a fake value."""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]

# Fraud graph values that must never reach production (the committed defaults and .env.example values).
EXAMPLE_NEO4J_PASSWORD = "graph-dev-only"
EXAMPLE_GRAPH_KEY_SECRET = "ZmFrZS1ncmFwaC1rZXktc2VjcmV0LTMyLWJ5dGVz"
GRAPH_KEY_MIN_BYTES = 32
# Photo storage values that must never reach production (the committed defaults and .env.example values).
EXAMPLE_SUPABASE_URL = "https://example.supabase.co"
EXAMPLE_SUPABASE_SERVICE_KEY = "fake-service-key"
# Claim-link signing secret that must never sign real links (the committed default and .env.example value).
EXAMPLE_CLAIM_TOKEN_SECRET = "ZmFrZS1jbGFpbS10b2tlbi1zZWNyZXQtMzItYnl0ZXM="
# The JWKS URL older .env.example files carried; like an empty value it means "derive it from CLERK_ISSUER".
EXAMPLE_CLERK_JWKS_URL = "https://example.clerk.accounts.dev/.well-known/jwks.json"
EXAMPLE_CLERK_HOST = "example.clerk.accounts.dev"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# An ASCII address (RFC 5322 atext local part): a look-alike such as the Kelvin sign, which lowercases to "k", never
# counts as the operator's address.
EMAIL = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}")
GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
EXAMPLE_CLERK_SECRET_KEY = "sk_test_fake"


def canonical_email(address: str) -> str:
    """How operator addresses are compared: lowercase, and for Gmail without the dots Gmail ignores (one mailbox, so
    kartikeya.mandhar777@gmail.com is kartikeyamandhar777@gmail.com) and with googlemail.com read as gmail.com. A +tag
    is kept, because the tagged address can belong to a different account."""
    local, _, domain = address.strip().lower().rpartition("@")
    if domain in GMAIL_DOMAINS:
        return local.replace(".", "") + "@gmail.com"
    return f"{local}@{domain}"


def is_local_uri(uri: str) -> bool:
    return (urlsplit(uri).hostname or "") in LOCAL_HOSTS


PINNED_STRIPE_API_VERSION: Literal["2026-09-30.endive"] = "2026-09-30.endive"  # stripe 16.0.0


class Settings(BaseSettings):
    # hide_input_in_errors: a validation error never echoes the inputs, which include keys and passwords.
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", frozen=True, hide_input_in_errors=True
    )

    app_env: Literal["dev", "test", "prod"] = Field("dev", description="Deployment environment.")
    log_level: str = Field("INFO", description="Root log level for JSON-lines logs.")

    # Database. Three roles (CLAUDE.md invariant 2): app (API, RLS enforced), internal (jobs, BYPASSRLS),
    # owner (migrations, owns tables).
    database_url: str = Field(
        "postgresql://recallops_app:app-dev-only@localhost:55432/recallops",
        description="libpq URL for recallops_app, the API's only role.",
    )
    database_url_internal: str = Field(
        "postgresql://recallops_internal:internal-dev-only@localhost:55432/recallops",
        description="libpq URL for recallops_internal (BYPASSRLS), used by jobs only.",
    )
    database_url_owner: str = Field(
        "postgresql://recallops_owner:owner-dev-only@localhost:55432/recallops",
        description="libpq URL for recallops_owner, used by migrations only.",
    )

    # Auth seam (component 4 replaces the provider).
    auth_provider: Literal["dev", "clerk"] = Field("dev", description="dev reads X-Dev-* headers.")
    clerk_secret_key: SecretStr = Field(
        SecretStr(EXAMPLE_CLERK_SECRET_KEY),
        description="Clerk secret key (login owner). The API also uses it to read operator email addresses.",
    )
    clerk_publishable_key: str = Field("pk_test_fake", description="Clerk publishable key.")
    clerk_jwks_url: str = Field(
        "", description="Empty: derived from CLERK_ISSUER (<issuer>/.well-known/jwks.json). Set only to override."
    )
    clerk_allowed_origins: str = Field("http://localhost:3000", description="Comma-separated azp values.")
    clerk_issuer: str = Field(
        "", description="Clerk Frontend API URL, the session token's iss (for example https://x.clerk.accounts.dev)."
    )
    claim_token_secret: SecretStr = Field(
        SecretStr(EXAMPLE_CLAIM_TOKEN_SECRET),
        description="Base64 of at least 32 random bytes; signs claim links (hmac).",
    )
    claim_token_provider: Literal["dev", "hmac"] = Field(
        "dev", description="dev: dev.<firm>.<claim> links (development only); hmac: the login owner's signed links."
    )
    claim_token_ttl_days: int = Field(90, ge=1, le=90, description="Claim link lifetime (invariant 10: 90 days).")
    seed_clerk_user_id: str = Field(
        "",
        description="make seed binds the demo firm to this Clerk user id (development only).",
    )
    # Company verification (migration 0008): a firm runs a recall only after the platform operator approves it.
    access_approval: Literal["required", "off"] = Field(
        "required",
        description="required: firms need an operator-approved recall (always in prod). off: tests only.",
    )
    platform_operator_user_ids: str = Field(
        "",
        description="Comma-separated user ids (Clerk user_..., or the dev provider's user id) who approve firms.",
    )
    platform_operator_emails: str = Field(
        "",
        description="Comma-separated email addresses of the platform operator (Clerk only; needs CLERK_SECRET_KEY). An "
        "account is the operator while one of its verified addresses is listed, so a deleted and recreated account "
        "keeps the role. Gmail addresses match with or without dots.",
    )

    # Payments seam (component 3).
    stripe_secret_key: SecretStr = Field(SecretStr("sk_test_fake"), description="Stripe test-mode secret key only.")
    stripe_publishable_key: str = Field("pk_test_fake")
    stripe_webhook_secret: SecretStr = Field(SecretStr("whsec_fake"))
    stripe_connect_webhook_secret: SecretStr = Field(
        SecretStr("whsec_fake"), description="Accounts v2 thin-notification endpoint secret."
    )
    stripe_api_version: Literal["2026-09-30.endive"] = PINNED_STRIPE_API_VERSION
    stripe_timeout_s: float = Field(15.0, gt=0, le=30)
    stripe_program_price_id: str = ""
    stripe_claim_price_id: str = ""
    stripe_meter_event_name: str = "recallops_claims"
    stripe_portal_configuration_id: str = ""
    # The console and the portal in apps/web (PR 9); the dev console handles these routes no more.
    billing_return_url: str = "http://localhost:3000/app/billing/return"
    connect_return_url: str = "http://localhost:3000/claim/connect-return"
    connect_refresh_url: str = "http://localhost:3000/claim/connect-refresh"
    payment_poll_s: float = Field(30.0, gt=0)
    # Free covers one product (owner decision 2026-10-22): unlimited claims, both refund options. FREE_OPEN_PROGRAMS
    # is the old name of the same setting and is still read.
    free_active_products: int = Field(
        1, ge=1, validation_alias=AliasChoices("free_active_products", "free_open_programs")
    )
    growth_program_price_cents: int = Field(19900, gt=0)
    growth_included_claims: int = Field(1000, ge=0)
    growth_claim_price_cents: int = Field(25, gt=0)

    @field_validator("stripe_api_version", mode="before")
    @classmethod
    def _pinned_stripe_version(cls, value: object) -> object:
        """The version is pinned in code. An empty value or the placeholder older .env.example files carried
        ("2026-09-30.fake") means the pinned one, so an existing .env keeps working; any other value is refused with
        a message naming the fix (backend review of PR 6)."""
        if value in (None, "", "2026-09-30.fake"):
            return PINNED_STRIPE_API_VERSION
        if value != PINNED_STRIPE_API_VERSION:
            raise ValueError(f"STRIPE_API_VERSION must be {PINNED_STRIPE_API_VERSION}; delete the line from .env")
        return value

    @model_validator(mode="after")
    def _stripe_test_only(self) -> Settings:
        secret_key = self.stripe_secret_key.get_secret_value()
        if not secret_key.startswith("sk_test_") or not self.stripe_publishable_key.startswith("pk_test_"):
            raise ValueError("Stripe test-mode key prefixes are required in every environment")
        if self.app_env == "prod" and secret_key != "sk_test_fake":
            # Payments configured in prod: no localhost or dev-console return page (never served there) and the
            # prices must exist. Without a key, payments stay off and none of these are needed.
            for url in (self.billing_return_url, self.connect_return_url, self.connect_refresh_url):
                if is_local_uri(url) or "/dev/console" in url:
                    raise ValueError("payment return URLs must point at the deployed UI in prod")
            if not self.stripe_program_price_id or not self.stripe_claim_price_id:
                raise ValueError("STRIPE_PROGRAM_PRICE_ID and STRIPE_CLAIM_PRICE_ID are required in prod")
        for url in (self.billing_return_url, self.connect_return_url, self.connect_refresh_url):
            parts = urlsplit(url)
            if not parts.hostname or parts.username or parts.password or parts.query:
                raise ValueError("payment return URLs need a host and must not contain credentials or query tokens")
            if parts.scheme != "https" and not (parts.scheme == "http" and is_local_uri(url)):
                raise ValueError("payment return URLs require HTTPS except on localhost")
        return self

    internal_shared_secret: str = Field("internal-dev-only", description="Header secret for /internal/*.")

    # Ingestion.
    cpsc_api_base: str = Field("https://www.saferproducts.gov/RestWebServices/Recall")
    http_user_agent: str = Field("RecallOps research (student project)")
    cpsc_timeout_s: float = Field(30.0)
    cpsc_window_days: int = Field(30)
    cpsc_pause_s: float = Field(2.0)
    recallbench_url: str = Field("https://recallbench.com/data/cpsc-recalls-full.csv")

    # Model. Prices are documented defaults from the phase 0 decision (CLAUDE.md decisions log).
    llm_mode: Literal["fake", "replay", "live"] = Field("replay", description="live needs owner approval.")
    llm_model_id: str = Field("claude-haiku-5-5")
    anthropic_api_key: str = Field("sk-ant-fake", description="Real key only in .env, never committed.")
    llm_price_input_per_mtok_usd: Decimal = Field(Decimal("0.10"))
    llm_price_output_per_mtok_usd: Decimal = Field(Decimal("0.50"))
    llm_price_long_context_threshold_tokens: int = Field(100_000)
    llm_price_input_long_per_mtok_usd: Decimal = Field(Decimal("0.50"))
    llm_price_output_long_per_mtok_usd: Decimal = Field(Decimal("2.50"))
    llm_prices_checked_on: str = Field("2026-10-06", description="Date of the price source.")
    llm_chars_per_token_estimate: float = Field(2.0, description="Conservative: overestimates tokens.")
    llm_image_patch_px: int = Field(28, description="One image token per patch of this many pixels square.")
    llm_max_output_tokens: int = Field(2048)
    llm_timeout_s: float = Field(30.0)
    llm_budget_stop_usd: Decimal = Field(Decimal("4.00"), description="Global stop (decisions log 2026-10-18).")
    llm_budget_warn_usd: Decimal = Field(Decimal("3.00"))
    llm_spend_carried_usd: Decimal = Field(Decimal("0"), description="Spend not visible in agent_steps.")
    replay_dir: Path = Field(REPO_ROOT / "evals" / "recordings", description="Eval recordings (committed).")
    llm_record_dir: Path = Field(
        REPO_ROOT / "data" / "generated" / "llm_recordings", description="Worker recordings (gitignored)."
    )

    # Caps (CLAUDE.md invariant 4) and extraction caps.
    run_cap_steps: int = Field(8)
    run_cap_usd: Decimal = Field(Decimal("0.02"))
    run_cap_seconds: float = Field(60.0)
    extract_run_cap_usd: Decimal = Field(Decimal("0.01"))
    extract_max_calls: int = Field(2)

    # Embeddings (decisions log 2026-10-18: local deterministic embedder; a provider plugs in behind the protocol).
    embedding_model_id: str = Field("hash-ngram-v1")
    embedding_dim: int = Field(1024, description="Must match migration 0002 (vector(1024)).")

    # Storage for claim photos (recallops.storage). Local directory in dev and tests; Supabase private bucket in prod.
    # Browsers never get a storage URL: the API's photo route proxies the bytes after an RLS lookup.
    storage_backend: Literal["local", "supabase"] = Field(
        "local", description="local writes under STORAGE_DIR; supabase uses the private bucket (Storage REST API)."
    )
    storage_timeout_s: float = Field(10.0, gt=0, description="Connect, read, write, and pool timeout per request.")
    storage_dir: Path = Field(REPO_ROOT / "data" / "generated" / "storage")
    supabase_url: str = Field(EXAMPLE_SUPABASE_URL, description="Project URL; https in prod.")
    supabase_service_key: SecretStr = Field(
        SecretStr(EXAMPLE_SUPABASE_SERVICE_KEY), description="Service role key, server only; real key only in .env."
    )
    supabase_bucket: str = Field("claim-photos", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

    # Fraud graph (Neo4j, a projection of Postgres; decisions log 2026-10-18). An empty NEO4J_URI disables the graph.
    # AuraDB uses neo4j+s://<instance>.databases.neo4j.io. One Community database serves dev and test, so every node
    # carries a namespace; tests run in their own.
    neo4j_uri: str = Field("bolt://localhost:57687", description="Bolt URI; empty disables the fraud graph.")
    neo4j_user: str = Field("neo4j")
    neo4j_password: SecretStr = Field(SecretStr(EXAMPLE_NEO4J_PASSWORD), description="Real password only in .env.")
    neo4j_database: str = Field(
        "neo4j",
        description="Community's database is neo4j; an AuraDB instance names its own (the instance id, in the "
        "credentials file as NEO4J_DATABASE).",
    )
    neo4j_namespace: str = Field("dev", pattern=r"^[a-z][a-z0-9_]{0,31}$", description="Separates dev from test.")
    neo4j_timeout_s: float = Field(3.0, description="Connection and per-transaction timeout.")
    graph_key_secret: SecretStr = Field(
        SecretStr(EXAMPLE_GRAPH_KEY_SECRET),
        description="HMAC key for identity keys in the graph (addresses, emails, payout destinations are never stored "
        "raw); its UTF-8 bytes are the key, at least 32 in prod. Rotating it requires make graph-sync.",
    )
    graph_resync_batch: int = Field(25, description="Decided claims the worker re-projects per idle tick.")
    graph_backoff_s: float = Field(
        30.0, description="After a graph failure, graph calls fail fast for this long (per-process circuit breaker)."
    )

    # Adjudication defaults.
    phash_duplicate_bits: int = Field(6)
    default_value_threshold_cents: int = Field(10_000)

    # Worker (claims/worker.py). ADJUDICATOR=agent needs LLM_MODE=live (or recordings for every request);
    # rules_only runs offline at no cost.
    adjudicator: Literal["agent", "rules_only"] = Field("agent", description="What the worker runs on a claim.")
    agent_variant: str = Field("loop_full", description="claims.graph.VARIANTS key used by the worker.")
    worker_poll_s: float = Field(1.0, description="Seconds between polls when there is no work.")
    worker_lease_s: int = Field(90, description="A claim or resume lease; expiry lets another worker retry.")
    worker_max_attempts: int = Field(3, description="Failed attempts before a claim is held for a person.")
    worker_heartbeat_max_age_s: float = Field(300.0, description="/readyz reports degraded past this age.")

    # Portal (component 4 owns the token format; the backend owns the routes).
    portal_base_url: str = Field("http://localhost:3000/claim", description="Base of issued claim links.")
    # One public claim link per product (owner decision 2026-10-22; invariant 10 amended). The limits below are
    # platform safety, the same on every plan: a public link must not be able to spend the model budget or the photo
    # bucket for every company.
    intake_base_url: str = Field(
        "", description="Base of each product's public claim link; empty: PORTAL_BASE_URL's site with /file."
    )
    intake_rate_per_ip_hour: int = Field(10, ge=1, description="New claims a client address may start an hour.")
    intake_rate_per_program_hour: int = Field(60, ge=1, description="New claims one product's link may start an hour.")
    intake_rate_per_network_hour: int = Field(
        30,
        ge=1,
        description="New claims one IPv6 /48 may start an hour, across its /64s (the per-address limit is /64).",
    )
    billing_rate_per_firm_hour: int = Field(
        20, ge=1, description="Stripe Checkout and Customer Portal sessions one company may start an hour."
    )
    intake_agent_claims_per_program_day: int = Field(
        100, ge=0, description="Claims a day per product decided by the AI agent; later ones use the rules checker."
    )
    intake_photo_max_pixels: int = Field(2_000_000, ge=100_000, description="Larger photos are scaled down to this.")
    intake_photo_max_bytes: int = Field(
        1_000_000, ge=100_000, description="Claimant photos larger than this are stored re-encoded as JPEG."
    )
    draft_ttl_hours: int = Field(48, ge=1, description="Claims started but never submitted are deleted after this.")
    payout_choice_days: int = Field(14, ge=1, description="Days a claimant has to choose; then a check is mailed.")
    payout_onboarding_days: int = Field(30, ge=1, description="Days to finish Stripe bank setup; then a check.")
    max_photo_bytes: int = Field(8_000_000, description="Largest accepted upload.")
    max_photo_pixels: int = Field(40_000_000, description="Larger images are refused (decompression bombs).")
    portal_rate_per_token_hour: int = Field(60, description="Portal requests per claim link per hour (04-login.md).")
    portal_uploads_per_token_hour: int = Field(10, description="Photo uploads per claim link per hour.")
    portal_rate_per_ip_hour: int = Field(300, description="Portal requests per client IP per hour.")
    trusted_proxy_hops: int = Field(
        0,
        ge=0,
        le=3,
        description="Proxies in front of the API that append to X-Forwarded-For (Render: 1). The client is the entry "
        "that many places from the right; 0 trusts no header and uses the socket peer.",
    )
    web_proxy_secret: SecretStr = Field(
        SecretStr(""),
        description="Shared with the web app (RECALLOPS_WEB_PROXY_SECRET). Every portal call reaches the API from the "
        "web server, so the web server names the claimant's address in X-RecallOps-Client-IP; the API believes that "
        "header only beside this secret. Empty: the header is ignored. Required in prod (the API refuses to start).",
    )

    # Campaigns (M5): owner registration uploads.
    registrations_max_bytes: int = Field(5_000_000, description="Largest registrations CSV accepted.")
    registrations_max_rows: int = Field(50_000, description="Rows per registrations CSV.")

    def clerk_keys_url(self) -> str:
        """The JWKS URL the Clerk provider trusts: CLERK_JWKS_URL when set, else <issuer>/.well-known/jwks.json."""
        if self.clerk_jwks_url and self.clerk_jwks_url != EXAMPLE_CLERK_JWKS_URL:
            return self.clerk_jwks_url
        return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"

    @model_validator(mode="after")
    def _access_guard(self) -> Settings:
        """Production never runs without company verification (decisions log 2026-10-22)."""
        if self.app_env == "prod" and self.access_approval != "required":
            raise ValueError("ACCESS_APPROVAL must be required in prod")
        listed = [e.strip() for e in self.platform_operator_emails.split(",") if e.strip()]
        if any(not e.isascii() or not EMAIL.fullmatch(e.lower()) for e in listed):
            raise ValueError("PLATFORM_OPERATOR_EMAILS must be comma-separated ASCII email addresses")
        # Clerk's test mode verifies any +clerk_test address with a fixed code on development instances: anyone could
        # sign up as one.
        if any("+clerk_test" in e.lower() for e in listed):
            raise ValueError("PLATFORM_OPERATOR_EMAILS must not list a +clerk_test address")
        key = self.clerk_secret_key.get_secret_value()
        if (
            self.app_env == "prod"
            and listed
            and (self.auth_provider != "clerk" or key in ("", EXAMPLE_CLERK_SECRET_KEY))
        ):
            raise ValueError("PLATFORM_OPERATOR_EMAILS needs AUTH_PROVIDER=clerk and a real CLERK_SECRET_KEY in prod")
        return self

    def intake_base(self) -> str:
        """Where a product's claim link lives: INTAKE_BASE_URL, or the site of PORTAL_BASE_URL with /file (both are
        pages of the one website, and PORTAL_BASE_URL is already checked for prod)."""
        if self.intake_base_url:
            return self.intake_base_url.rstrip("/")
        portal = urlsplit(self.portal_base_url)
        return f"{portal.scheme}://{portal.netloc}/file"

    def operator_ids(self) -> frozenset[str]:
        return frozenset(u.strip() for u in self.platform_operator_user_ids.split(",") if u.strip())

    def operator_emails(self) -> frozenset[str]:
        return frozenset(canonical_email(e) for e in self.platform_operator_emails.split(",") if e.strip())

    @model_validator(mode="after")
    def _login_guards(self) -> Settings:
        """Claim links are bearer credentials and the JWKS URL is the root of trust for every session, so both are
        checked like the graph and storage settings (backend review of PR 8). Messages name the variable only."""
        portal = urlsplit(self.portal_base_url)
        if not portal.hostname or portal.scheme not in ("http", "https"):
            raise ValueError("PORTAL_BASE_URL must be an http(s) URL with a host")
        if portal.username or portal.password or portal.query or portal.fragment:
            raise ValueError("PORTAL_BASE_URL must not carry credentials, a query, or a fragment (the token follows)")
        if self.app_env == "prod" and (portal.scheme != "https" or is_local_uri(self.portal_base_url)):
            raise ValueError("PORTAL_BASE_URL must be the deployed https site in prod")
        if self.intake_base_url:
            intake = urlsplit(self.intake_base_url)
            if not intake.hostname or intake.scheme not in ("http", "https"):
                raise ValueError("INTAKE_BASE_URL must be an http(s) URL with a host")
            if intake.username or intake.password or intake.query or intake.fragment:
                raise ValueError("INTAKE_BASE_URL must not carry credentials, a query, or a fragment")
            if self.app_env == "prod" and (intake.scheme != "https" or is_local_uri(self.intake_base_url)):
                raise ValueError("INTAKE_BASE_URL must be the deployed https site in prod")
        if self.auth_provider != "clerk" or not self.clerk_issuer:
            return self  # ClerkAuthProvider itself refuses an empty CLERK_ISSUER
        issuer, keys = urlsplit(self.clerk_issuer), urlsplit(self.clerk_keys_url())
        for name, part, raw in (
            ("CLERK_ISSUER", issuer, self.clerk_issuer),
            ("CLERK_JWKS_URL", keys, self.clerk_keys_url()),
        ):
            if not part.hostname or not (part.scheme == "https" or (part.scheme == "http" and is_local_uri(raw))):
                raise ValueError(f"{name} must be an https URL (http only on localhost)")
        if keys.hostname != issuer.hostname:
            raise ValueError("CLERK_JWKS_URL must be on the CLERK_ISSUER host; leave it empty to derive it")
        if self.app_env == "prod":
            if issuer.scheme != "https" or is_local_uri(self.clerk_issuer) or issuer.hostname == EXAMPLE_CLERK_HOST:
                raise ValueError("CLERK_ISSUER must be the real Clerk instance's https Frontend API URL in prod")
            origins = [o.strip() for o in self.clerk_allowed_origins.split(",") if o.strip()]
            if not origins or any(urlsplit(o).scheme != "https" or is_local_uri(o) for o in origins):
                raise ValueError("CLERK_ALLOWED_ORIGINS must list the deployed https origins in prod")
        return self

    @model_validator(mode="after")
    def _graph_guards(self) -> Settings:
        """Production graph settings must be real; development must never write the production namespace. A prod
        process with the graph disabled (empty NEO4J_URI) needs none of the graph values."""
        if self.app_env != "prod":
            if self.neo4j_namespace == "prod":
                raise ValueError("NEO4J_NAMESPACE=prod is reserved for APP_ENV=prod")
            return self
        if not self.neo4j_uri:
            return self
        problems = []
        if self.neo4j_namespace != "prod":
            problems.append("NEO4J_NAMESPACE must be prod")
        secret = self.graph_key_secret.get_secret_value()
        if secret == EXAMPLE_GRAPH_KEY_SECRET:
            problems.append("GRAPH_KEY_SECRET is the example value")
        if len(secret.encode("utf-8")) < GRAPH_KEY_MIN_BYTES:
            problems.append(f"GRAPH_KEY_SECRET is shorter than {GRAPH_KEY_MIN_BYTES} bytes")
        if self.neo4j_password.get_secret_value() == EXAMPLE_NEO4J_PASSWORD:
            problems.append("NEO4J_PASSWORD is the development password")
        if not is_local_uri(self.neo4j_uri) and not urlsplit(self.neo4j_uri).scheme.endswith("+s"):
            problems.append("a remote NEO4J_URI must use TLS (neo4j+s:// or bolt+s://)")
        if problems:
            raise ValueError("unsafe fraud graph settings for APP_ENV=prod: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _storage_guards(self) -> Settings:
        """A production process on the Supabase backend must hold a real project URL over TLS and a real key."""
        if self.app_env != "prod" or self.storage_backend != "supabase":
            return self
        problems = []
        parts = urlsplit(self.supabase_url)
        if self.supabase_url.rstrip("/") == EXAMPLE_SUPABASE_URL:
            problems.append("SUPABASE_URL is the example value")
        if parts.scheme != "https" or not parts.hostname:
            problems.append("SUPABASE_URL must be an https URL with a host")
        key = self.supabase_service_key.get_secret_value()
        if key == EXAMPLE_SUPABASE_SERVICE_KEY:
            problems.append("SUPABASE_SERVICE_KEY is the example value")
        elif not key:
            problems.append("SUPABASE_SERVICE_KEY is empty")
        if problems:
            raise ValueError("unsafe photo storage settings for APP_ENV=prod: " + "; ".join(problems))
        return self

    @field_validator("storage_dir", "replay_dir", "llm_record_dir")
    @classmethod
    def _repo_relative(cls, value: Path) -> Path:
        """Relative paths in .env are relative to the repository root, not the process cwd."""
        return value if value.is_absolute() else REPO_ROOT / value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
