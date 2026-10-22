"""Portal guards that must run before FastAPI parses a request body (red-team findings, decisions log 2026-10-18):

- Body size: /portal/*/photos is refused above MAX_PHOTO_BYTES plus 64 KB of form overhead and any other portal
  route above 64 KB, from Content-Length up front and by counting streamed bytes for chunked bodies (413).
- Rate limits (invariant 10; 04-login.md section 5): fixed one-hour windows in the rate_limits table, per claim token
  (requests and uploads) and per client IP on /portal/*. Over the limit: 429 with Retry-After. The login owner can
  replace this limiter; the table and the settings stay.
- Portal responses carry Referrer-Policy: no-referrer and Cache-Control: no-store, so a token in the URL is not sent
  to third parties or kept in shared caches. Request logs redact the token (recallops.api.problems).
- A product's public claim link (/portal/intake/<token>, plan v3) has its own limits on starting claims per client
  address: an IPv6 client counts per /64 (which one household holds whole) and per /48 (which one site can rotate
  through). The per-link limit is counted by the route, only for starts that pass every check, so refused or forged
  requests cannot spend a product's budget (plan v3 review). Reading the link counts like any portal read.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
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

INTAKE = re.compile(r"^/portal/intake/([^/]+)$")
PORTAL = re.compile(r"^/portal/([^/]+)(/[a-z]+)?$")
REGISTRATIONS = re.compile(r"^/v1/programs/[^/]+/registrations$")
FORM_OVERHEAD = 64_000
SMALL_BODY = 64_000

Scope = dict[str, Any]


WEB_SECRET_HEADER = b"x-recallops-web-secret"
WEB_CLIENT_HEADER = b"x-recallops-client-ip"


def _header(scope: Scope, name: bytes) -> str | None:
    return next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == name), None)


def web_client(scope: Scope, secret: str) -> str | None:
    """The claimant's address as the web app names it. The portal is served through the web server, so without this
    every claimant would share the web server's address and one per-IP bucket (review of PR 9). Believed only next to
    the shared secret and only when it parses as an IP address."""
    if not secret:
        return None
    sent = _header(scope, WEB_SECRET_HEADER)
    if sent is None or not hmac.compare_digest(sent.encode(), secret.encode()):
        return None
    named = (_header(scope, WEB_CLIENT_HEADER) or "").strip()
    try:
        return str(ipaddress.ip_address(named))
    except ValueError:
        return None


def client_address(scope: Scope, trusted_hops: int, web_secret: str = "") -> str:
    """The rate-limit key's address. A request from the web app carries the claimant's address (web_client). Behind
    trusted proxies, the right-most entries of X-Forwarded-For were written by them, so the client is the entry
    trusted_hops from the right; anything further left is client-supplied and could be forged to dodge the limit."""
    named = web_client(scope, web_secret)
    if named is not None:
        return named
    peer = str((scope.get("client") or ("unknown", 0))[0])
    if trusted_hops <= 0:
        return peer
    forwarded = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-for"), "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    return hops[-trusted_hops] if len(hops) >= trusted_hops else peer


def client_network(client: str) -> str:
    """The rate-limit identity of an address: an IPv4 address itself, an IPv6 address by its /64."""
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return client
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_wide_network(client: str) -> str | None:
    """The /48 an IPv6 address belongs to (one site's allocation), for a second, wider limit; None for IPv4."""
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return None
    return str(ipaddress.ip_network(f"{ip}/48", strict=False)) if ip.version == 6 else None


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


def limited(kind: str, value: Any, limit: int) -> int | None:
    """An hourly limit a route counts itself, after its own checks (billing per company, starts per product link):
    seconds to wait when over, else None. Same table and windows as the portal limits."""
    return _bump([(_limit_key(kind, str(value)), limit)])


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
        path = scope.get("path", "")
        intake = INTAKE.match(path)
        m = None if intake else PORTAL.match(path)
        if intake is None and m is None:
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        client = client_address(scope, settings.trusted_proxy_hops, settings.web_proxy_secret.get_secret_value())
        if intake is not None:
            token, action, max_body = intake.group(1), "/intake", SMALL_BODY
            keys = [(_limit_key("ip", client_network(client)), settings.portal_rate_per_ip_hour)]
            if scope.get("method") == "POST":
                keys.append((_limit_key("intake-ip", client_network(client)), settings.intake_rate_per_ip_hour))
                wide = client_wide_network(client)
                if wide is not None:
                    keys.append((_limit_key("intake-net", wide), settings.intake_rate_per_network_hour))
        else:
            assert m is not None
            token, action = m.group(1), (m.group(2) or "")
            upload = action == "/photos"
            max_body = settings.max_photo_bytes + FORM_OVERHEAD if upload else SMALL_BODY
            keys = [
                (_limit_key("tok", token), settings.portal_rate_per_token_hour),
                (_limit_key("ip", client_network(client)), settings.portal_rate_per_ip_hour),
            ]
            if upload and scope.get("method") == "POST":
                keys.append((_limit_key("up", token), settings.portal_uploads_per_token_hour))
        length = next((int(v) for k, v in scope.get("headers", []) if k == b"content-length" and v.isdigit()), None)
        if length is not None and length > max_body:
            await _respond(send, 413, f"request body larger than {max_body} bytes")
            return
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
    if INTAKE.match(path):
        return "/portal/intake/<token>"
    m = PORTAL.match(path)
    return f"/portal/<token>{m.group(2) or ''}" if m else path


def install(app: FastAPI) -> None:
    app.add_middleware(PortalGuard)
