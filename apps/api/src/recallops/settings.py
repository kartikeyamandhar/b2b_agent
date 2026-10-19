"""Application settings. Every variable is documented here and listed in .env.example with a fake value."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]

# Fraud graph values that must never reach production (the committed defaults and .env.example values).
EXAMPLE_NEO4J_PASSWORD = "graph-dev-only"
EXAMPLE_GRAPH_KEY_SECRET = "ZmFrZS1ncmFwaC1rZXktc2VjcmV0LTMyLWJ5dGVz"
GRAPH_KEY_MIN_BYTES = 32
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_uri(uri: str) -> bool:
    return (urlsplit(uri).hostname or "") in LOCAL_HOSTS


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore", frozen=True
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
    clerk_secret_key: str = Field("sk_test_fake", description="Clerk secret key (login owner).")
    clerk_publishable_key: str = Field("pk_test_fake", description="Clerk publishable key.")
    clerk_jwks_url: str = Field("https://example.clerk.accounts.dev/.well-known/jwks.json")
    clerk_allowed_origins: str = Field("http://localhost:3000", description="Comma-separated azp values.")
    claim_token_secret: str = Field("ZmFrZS1jbGFpbS10b2tlbi1zZWNyZXQtMzItYnl0ZXM=")
    claim_token_provider: Literal["dev", "hmac"] = Field(
        "dev", description="dev: dev.<firm>.<claim> links (development only); hmac: the login owner's signed links."
    )

    # Payments seam (component 3).
    stripe_secret_key: str = Field("sk_test_fake", description="Stripe test-mode secret key only.")
    stripe_publishable_key: str = Field("pk_test_fake")
    stripe_webhook_secret: str = Field("whsec_fake")
    stripe_api_version: str = Field("2026-09-30.fake", description="Pinned Stripe API version.")

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

    # Storage for claim photos. Local directory in dev; Supabase private bucket in prod.
    storage_dir: Path = Field(REPO_ROOT / "data" / "generated" / "storage")
    supabase_url: str = Field("https://example.supabase.co")
    supabase_service_key: str = Field("fake-service-key")
    supabase_bucket: str = Field("claim-photos")

    # Fraud graph (Neo4j, a projection of Postgres; decisions log 2026-10-18). An empty NEO4J_URI disables the graph.
    # AuraDB uses neo4j+s://<instance>.databases.neo4j.io. One Community database serves dev and test, so every node
    # carries a namespace; tests run in their own.
    neo4j_uri: str = Field("bolt://localhost:57687", description="Bolt URI; empty disables the fraud graph.")
    neo4j_user: str = Field("neo4j")
    neo4j_password: SecretStr = Field(SecretStr(EXAMPLE_NEO4J_PASSWORD), description="Real password only in .env.")
    neo4j_database: str = Field("neo4j", description="Community and AuraDB Free have one user database.")
    neo4j_namespace: str = Field("dev", pattern=r"^[a-z][a-z0-9_]{0,31}$", description="Separates dev from test.")
    neo4j_timeout_s: float = Field(10.0, description="Connection and per-transaction timeout.")
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
    max_photo_bytes: int = Field(8_000_000, description="Largest accepted upload.")
    max_photo_pixels: int = Field(40_000_000, description="Larger images are refused (decompression bombs).")
    portal_rate_per_token_hour: int = Field(60, description="Portal requests per claim link per hour (04-login.md).")
    portal_uploads_per_token_hour: int = Field(10, description="Photo uploads per claim link per hour.")
    portal_rate_per_ip_hour: int = Field(300, description="Portal requests per client IP per hour.")

    # Campaigns (M5): owner registration uploads.
    registrations_max_bytes: int = Field(5_000_000, description="Largest registrations CSV accepted.")
    registrations_max_rows: int = Field(50_000, description="Rows per registrations CSV.")

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

    @field_validator("storage_dir", "replay_dir", "llm_record_dir")
    @classmethod
    def _repo_relative(cls, value: Path) -> Path:
        """Relative paths in .env are relative to the repository root, not the process cwd."""
        return value if value.is_absolute() else REPO_ROOT / value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
