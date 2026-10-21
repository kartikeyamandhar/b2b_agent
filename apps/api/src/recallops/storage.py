"""Claim-photo storage seam: local disk in development and tests, a private Supabase Storage bucket in production.

Object keys are relative paths built only from ids by the callers (portal, seed); every backend validates them again
before any file or network access: only [A-Za-z0-9._/-], no '..', no leading '/', no empty or '.' segment. The bucket
stays private: photos reach a browser only through the API's firm-authenticated photo route, which proxies the bytes
after an RLS lookup, so no signed storage URL is ever issued.

Every I/O failure is a StorageError whose text names the operation, the object key, and the HTTP status or the
exception type. It never carries the service key or a response body, so it is safe to log.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from recallops.settings import Settings

KEY_CHARS = re.compile(r"[A-Za-z0-9._/-]+")
BUCKET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
MAX_KEY_LENGTH = 1024
# Supabase answers 429 above its request rate (the first production seed hit it with 3,000 calls in a burst). A 429
# was not processed, so the same request is sent again: at most RATE_LIMIT_RETRIES times, waiting the advised
# Retry-After or 0.5, 1, 2 s, never more than RATE_LIMIT_MAX_WAIT_S per wait, so a portal upload never stalls long.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT_S = 5.0


class StorageError(Exception):
    """A storage operation failed (transport error, timeout, or a non-success status)."""


class InvalidKey(StorageError):
    """The object key is not a safe relative path; refused before any file or network access."""


class Storage(Protocol):
    def put(self, relative: str, data: bytes, content_type: str) -> None:
        """Store a new object. Supabase refuses to overwrite (x-upsert: false); callers use fresh keys or delete
        first. Raises StorageError."""
        ...

    def get(self, relative: str) -> bytes | None:
        """The object's bytes, or None when it does not exist. Raises StorageError on any other failure."""
        ...

    def delete(self, relative: str) -> None:
        """Remove the object; an absent object is not an error. Raises StorageError."""
        ...


def validate_key(relative: str) -> str:
    """Return the key unchanged when it is a safe relative object path; raise InvalidKey otherwise."""
    if not relative or len(relative) > MAX_KEY_LENGTH:
        raise InvalidKey("object key is empty or too long")
    if KEY_CHARS.fullmatch(relative) is None:
        raise InvalidKey("object key has characters outside [A-Za-z0-9._/-]")
    if relative.startswith("/") or ".." in relative:
        raise InvalidKey("object key is absolute or contains '..'")
    if any(part in ("", ".") for part in relative.split("/")):
        raise InvalidKey("object key has an empty or '.' segment")
    return relative


class LocalStorage:
    """Files under root (STORAGE_DIR). A key that resolves outside root (through a symlink, say) is refused."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __repr__(self) -> str:
        return f"LocalStorage({str(self.root)!r})"

    def _path(self, relative: str) -> Path:
        key = validate_key(relative)
        root = self.root.resolve()
        target = (root / key).resolve()
        if root not in target.parents:
            raise InvalidKey("object key resolves outside the storage root")
        return target

    def put(self, relative: str, data: bytes, content_type: str) -> None:
        target = self._path(relative)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            raise StorageError(f"put {relative} failed: {type(exc).__name__}") from exc

    def get(self, relative: str) -> bytes | None:
        target = self._path(relative)
        try:
            return target.read_bytes() if target.is_file() else None
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError(f"get {relative} failed: {type(exc).__name__}") from exc

    def delete(self, relative: str) -> None:
        target = self._path(relative)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"delete {relative} failed: {type(exc).__name__}") from exc


NotFound = Literal["object", "bucket"]


def _not_found(response: httpx.Response) -> NotFound | None:
    """Which thing is missing, from a 404 or a 400 not-found answer. Storage API versions answer a missing object
    with 404, or with 400 and a JSON body whose statusCode is "404"; a missing bucket must stay an error, so the
    worker never treats a misconfigured bucket as a claim without photos. The body is read here and never logged."""
    if response.status_code not in (400, 404):
        return None
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return "object" if response.status_code == 404 else None
    code, error, message = (str(body.get(k, "")).lower() for k in ("code", "error", "message"))
    if code == "nosuchbucket" or "bucket not found" in (error, message):
        return "bucket"
    if response.status_code == 404:
        return "object"
    if code == "nosuchkey" or error == "not_found" or str(body.get("statusCode", "")) == "404":
        return "object"
    return None


def _rate_limit_wait(response: httpx.Response, attempt: int) -> float:
    """The advised Retry-After in seconds when it is a number, else 0.5, 1, 2 s by attempt; capped either way."""
    try:
        advised = float(response.headers.get("retry-after", ""))
    except ValueError:
        advised = -1.0
    wait = advised if advised >= 0 else 0.5 * 2**attempt
    return min(wait, RATE_LIMIT_MAX_WAIT_S)


class SupabaseStorage:
    """The Supabase Storage REST API with the service role key (server side only). One pooled httpx client."""

    def __init__(
        self,
        url: str,
        service_key: SecretStr | str,
        bucket: str,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("SUPABASE_URL must be an http(s) URL with a host")
        if BUCKET_NAME.fullmatch(bucket) is None:
            raise ValueError("SUPABASE_BUCKET must match [A-Za-z0-9][A-Za-z0-9._-]{0,62}")
        key = service_key.get_secret_value() if isinstance(service_key, SecretStr) else service_key
        if not key:
            raise ValueError("SUPABASE_SERVICE_KEY is empty")
        self.base = url.rstrip("/") + "/storage/v1/object"
        self.bucket = bucket
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {key}", "apikey": key},
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"SupabaseStorage({self.base!r}, bucket={self.bucket!r})"

    def close(self) -> None:
        self._client.close()

    def _send(
        self,
        op: str,
        key: str,
        method: str,
        url: str,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        json_body: object = None,
    ) -> httpx.Response:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.request(method, url, content=content, headers=headers, json=json_body)
            except httpx.TimeoutException as exc:
                raise StorageError(f"{op} {key} timed out ({type(exc).__name__})") from exc
            except httpx.HTTPError as exc:
                raise StorageError(f"{op} {key} failed: {type(exc).__name__}") from exc
            if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                return response
            self._sleep(_rate_limit_wait(response, attempt))
        raise AssertionError("unreachable")  # the loop always returns on its last attempt

    def put(self, relative: str, data: bytes, content_type: str) -> None:
        key = validate_key(relative)
        response = self._send(
            "put",
            key,
            "POST",
            f"{self.base}/{self.bucket}/{key}",
            content=data,
            headers={"content-type": content_type, "x-upsert": "false"},
        )
        if not response.is_success:
            raise StorageError(f"put {key} failed: HTTP {response.status_code}")

    def get(self, relative: str) -> bytes | None:
        key = validate_key(relative)
        response = self._send("get", key, "GET", f"{self.base}/authenticated/{self.bucket}/{key}")
        if response.status_code == 200:
            return response.content
        missing = _not_found(response)
        if missing == "object":
            return None
        what = "bucket not found" if missing == "bucket" else f"HTTP {response.status_code}"
        raise StorageError(f"get {key} failed: {what}")

    def delete(self, relative: str) -> None:
        key = validate_key(relative)
        response = self._send("delete", key, "DELETE", f"{self.base}/{self.bucket}", json_body={"prefixes": [key]})
        if response.is_success:
            return
        missing = _not_found(response)
        if missing == "object":
            return
        what = "bucket not found" if missing == "bucket" else f"HTTP {response.status_code}"
        raise StorageError(f"delete {key} failed: {what}")


def build_storage(settings: Settings) -> Storage:
    """One per process (the API and the worker each hold theirs); building opens no connection. Production refuses
    local disk: the API and the worker run as separate services that share no disk, so the worker would find no
    photos."""
    if settings.app_env == "prod" and settings.storage_backend == "local":
        raise StorageError("STORAGE_BACKEND=local is not allowed with APP_ENV=prod: use the private bucket (supabase)")
    if settings.storage_backend == "supabase":
        return SupabaseStorage(
            settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket, settings.storage_timeout_s
        )
    return LocalStorage(settings.storage_dir)
