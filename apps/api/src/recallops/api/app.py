"""FastAPI application. `make api` serves it; docs/contracts/openapi.json is generated from it."""

from __future__ import annotations

from fastapi import FastAPI

from recallops.api import problems, routes
from recallops.auth.deps import build_claim_verifier, build_provider
from recallops.logs import configure
from recallops.settings import get_settings

DESCRIPTION = """RecallOps backend API (component 1). Contract for the UI (component 2), payments (component 3),
and login (component 4) owners. In development, authenticate with X-Dev-Firm-Id and X-Dev-Role headers
(AUTH_PROVIDER=dev); the login owner's Clerk provider replaces them with a bearer token. Errors are RFC 7807
problem details with a correlation_id. Stripe Billing and Connect run in test mode only."""


def create_app() -> FastAPI:
    settings = get_settings()
    configure(settings.log_level)
    build_provider()  # fail at startup, not on the first request (the dev provider refuses live keys)
    build_claim_verifier()  # the dev verifier refuses prod and Clerk; the HMAC one refuses the example secret
    if settings.app_env == "prod":
        routes.photo_storage()  # refuses local disk in prod at startup, not on the first photo
    from recallops.api.guards import install as install_guards

    app = FastAPI(title="RecallOps API", version="0.5.0", description=DESCRIPTION)
    problems.install(app)
    install_guards(app)
    for router in (routes.firm, routes.billing, routes.portal, routes.internal, routes.health):
        app.include_router(router)
    from recallops.api import dev_console

    if dev_console.enabled(settings):  # never with the Clerk provider or in prod; not in the contract
        app.include_router(dev_console.router)
    return app


app = create_app()
