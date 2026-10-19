"""Backend test console: one static page at /dev/console that exercises every route through the dev auth headers.

It exists only while the header-trusting dev auth provider does (AUTH_PROVIDER=dev outside APP_ENV=prod), so it can
never be served in production. It is not part of the API contract (include_in_schema=False) and is not the product
UI, which the UI owner builds in apps/web against docs/contracts/openapi.json.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

from recallops.settings import Settings

PAGE = Path(__file__).with_name("static") / "dev_console.html"

router = APIRouter(include_in_schema=False)


def enabled(settings: Settings) -> bool:
    return settings.auth_provider == "dev" and settings.app_env != "prod"


@router.get("/dev/console", response_class=HTMLResponse)
def dev_console() -> HTMLResponse:
    try:
        html = PAGE.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the dev console page is missing") from exc
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
