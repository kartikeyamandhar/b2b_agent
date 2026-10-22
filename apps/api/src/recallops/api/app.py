"""FastAPI application. `make api` serves it; docs/contracts/openapi.json is generated from it."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from recallops.api import problems, routes
from recallops.api import schemas as s
from recallops.auth.deps import build_claim_verifier, build_provider
from recallops.logs import configure
from recallops.settings import Settings, get_settings

DESCRIPTION = """RecallOps backend API (component 1). Contract for the UI (component 2), payments (component 3),
and login (component 4) owners. In development, authenticate with X-Dev-Firm-Id and X-Dev-Role headers
(AUTH_PROVIDER=dev); the login owner's Clerk provider replaces them with a bearer token. Errors are RFC 7807
problem details with a correlation_id. Stripe Billing and Connect run in test mode only."""


def require_web_proxy_secret(settings: Settings) -> None:
    """The portal is served through the web app; without the shared secret every claimant shares the web server's
    address and one rate-limit bucket (review of PR 9). Checked at API startup only: the worker never needs it."""
    if len(settings.web_proxy_secret.get_secret_value()) < 32:
        raise RuntimeError("WEB_PROXY_SECRET must be at least 32 characters in prod (shared with the web app)")


def _keep_null_examples(app: FastAPI) -> None:
    """FastAPI drops None values while it encodes the document, so schema examples lost their null fields (for
    example Program.closed_at) and no longer matched the responses. Put each model's examples back as written."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            spec = generate()
            for name, schema in spec.get("components", {}).get("schemas", {}).items():
                model = getattr(s, name, None)
                extra = (
                    model.model_config.get("json_schema_extra")
                    if isinstance(model, type) and issubclass(model, BaseModel)
                    else None
                )
                if isinstance(extra, dict) and "examples" in extra:
                    schema["examples"] = json.loads(json.dumps(extra["examples"], default=str))
        return app.openapi_schema or generate()

    app.openapi = openapi  # type: ignore[method-assign]


def create_app() -> FastAPI:
    settings = get_settings()
    configure(settings.log_level)
    build_provider()  # fail at startup, not on the first request (the dev provider refuses live keys)
    build_claim_verifier()  # the dev verifier refuses prod and Clerk; the HMAC one refuses the example secret
    if settings.app_env == "prod":
        routes.photo_storage()  # refuses local disk in prod at startup, not on the first photo
        require_web_proxy_secret(settings)
    from recallops.api.guards import install as install_guards

    app = FastAPI(title="RecallOps API", version="0.5.0", description=DESCRIPTION)
    _keep_null_examples(app)
    problems.install(app)
    install_guards(app)
    for router in (
        routes.account,
        routes.public,
        routes.access,
        routes.operator,
        routes.firm,
        routes.billing,
        routes.portal,
        routes.internal,
        routes.health,
    ):
        app.include_router(router)
    from recallops.api import dev_console

    if dev_console.enabled(settings):  # never with the Clerk provider or in prod; not in the contract
        app.include_router(dev_console.router)
    return app


app = create_app()
