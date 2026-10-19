"""Portal guards that must run before FastAPI parses a request body (red-team findings, decisions log 2026-10-18):

- Body size: /portal/*/photos is refused above MAX_PHOTO_BYTES plus 64 KB of form overhead and any other portal
  route above 64 KB, from Content-Length up front and by counting streamed bytes for chunked bodies (413).
- Rate limits (invariant 10; 04-login.md section 5): fixed one-hour windows in the rate_limits table, per claim token
  (requests and uploads) and per client IP on /portal/*. Over the limit: 429 with Retry-After. The login owner can
  replace this limiter; the table and the settings stay.
- Portal responses carry Referrer-Policy: no-referrer and Cache-Control: no-store, so a token in the URL is not sent
  to third parties or kept in shared caches. Request logs redact the token (recallops.api.problems).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import FastAPI
from sqlalchemy import text

from recallops.db.engine import app_engine
from recallops.logs import get_logger, log_event
from recallops.settings import get_settings

log = get_logger("api.guards")

PORTAL = re.compile(r"^/portal/([^/]+)(/[a-z]+)?$")
REGISTRATIONS = re.compile(r"^/v1/programs/[^/]+/registrations$")
FORM_OVERHEAD = 64_000
SMALL_BODY = 64_000

Scope = dict[str, Any]


def _limit_key(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def _bump(keys: list[tuple[str, int]]) -> int | None:
    """Increment each counter in the current hour; return seconds to wait when any is over its limit."""
    now = datetime.now(UTC)
    window = now.replace(minute=0, second=0, microsecond=0)
    with app_engine().begin() as conn:
        conn.execute(text("DELETE FROM rate_limits WHERE window_start < :old"), {"old": window - timedelta(hours=2)})
        for key, limit in keys:
            count = conn.execute(
                text(
                    "INSERT INTO rate_limits (key, window_start, count) VALUES (:k, :w, 1) "
                    "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limits.count + 1 RETURNING count"
                ),
                {"k": key, "w": window},
            ).scalar_one()
            if count > limit:
                return int((window + timedelta(hours=1) - now).total_seconds()) + 1
    return None


async def _respond(send: Any, status: int, detail: str, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    import json

    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Too Many Requests" if status == 429 else "Payload Too Large",
            "status": status,
            "detail": detail,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class PortalGuard:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if REGISTRATIONS.match(scope.get("path", "")):
            await self._bounded(scope, receive, send, get_settings().registrations_max_bytes + FORM_OVERHEAD)
            return
        m = PORTAL.match(scope.get("path", ""))
        if m is None:
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        token, action = m.group(1), (m.group(2) or "")
        upload = action == "/photos"
        max_body = settings.max_photo_bytes + FORM_OVERHEAD if upload else SMALL_BODY
        length = next((int(v) for k, v in scope.get("headers", []) if k == b"content-length" and v.isdigit()), None)
        if length is not None and length > max_body:
            await _respond(send, 413, f"request body larger than {max_body} bytes")
            return
        client = (scope.get("client") or ("unknown", 0))[0]
        keys = [
            (_limit_key("tok", token), settings.portal_rate_per_token_hour),
            (_limit_key("ip", client), settings.portal_rate_per_ip_hour),
        ]
        if upload and scope.get("method") == "POST":
            keys.append((_limit_key("up", token), settings.portal_uploads_per_token_hour))
        retry = await anyio.to_thread.run_sync(_bump, keys)
        if retry is not None:
            log_event(log, "portal_rate_limited", action=action or "/", retry_after_s=retry)
            await _respond(
                send, 429, "too many requests for this claim link or address", [(b"retry-after", str(retry).encode())]
            )
            return
        received = 0

        async def counted_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_body:
                    raise BodyTooLarge()
            return message

        async def guarded_send(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store")]
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except BodyTooLarge:
            await _respond(send, 413, f"request body larger than {max_body} bytes")

    async def _bounded(self, scope: Scope, receive: Any, send: Any, max_body: int) -> None:
        """A firm upload (registrations CSV): size-bounded before parsing, like the portal's uploads."""
        length = next((int(v) for k, v in scope.get("headers", []) if k == b"content-length" and v.isdigit()), None)
        if length is not None and length > max_body:
            await _respond(send, 413, f"request body larger than {max_body} bytes")
            return
        received = 0

        async def counted_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_body:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, counted_receive, send)
        except BodyTooLarge:
            await _respond(send, 413, f"request body larger than {max_body} bytes")


class BodyTooLarge(Exception):
    pass


def redact(path: str) -> str:
    m = PORTAL.match(path)
    return f"/portal/<token>{m.group(2) or ''}" if m else path


def install(app: FastAPI) -> None:
    app.add_middleware(PortalGuard)
