"""The claim-photo storage seam: the Supabase adapter against httpx.MockTransport (no network), the local backend's
traversal guard, and the production settings guard for the Supabase backend."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from recallops.settings import EXAMPLE_SUPABASE_SERVICE_KEY, EXAMPLE_SUPABASE_URL, Settings
from recallops.storage import (
    InvalidKey,
    LocalStorage,
    StorageError,
    SupabaseStorage,
    build_storage,
    validate_key,
)

URL = "https://proj.supabase.co"
KEY = "sb-service-SECRET-0123456789"
BUCKET = "claim-photos"
OBJECT = "00000000-0000-4000-8000-00000000d001/11111111-1111-4111-8111-111111111111/label-0a1b2c.jpg"
BAD_KEYS = (
    "",
    "/abs/x.jpg",
    "../x.jpg",
    "a/../b.jpg",
    "a/..",
    "a..b/x.jpg",
    "a//b.jpg",
    "a/./b.jpg",
    "a/",
    ".",
    "a b.jpg",
    "a\\b.jpg",
    "a?b=1",
    "a#b",
    "a%2e%2e/b",
    "ünï/x.jpg",
    "x" * 1025,
)


def supabase(handler: Callable[[httpx.Request], httpx.Response], seen: list[httpx.Request]) -> SupabaseStorage:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return SupabaseStorage(URL + "/", KEY, BUCKET, 5.0, transport=httpx.MockTransport(record))


def assert_auth(request: httpx.Request) -> None:
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["apikey"] == KEY


def test_put_posts_the_bytes_without_upsert_and_with_both_auth_headers() -> None:
    seen: list[httpx.Request] = []
    store = supabase(lambda r: httpx.Response(200, json={"Key": f"{BUCKET}/{OBJECT}"}), seen)
    store.put(OBJECT, b"\xff\xd8jpeg-bytes", "image/jpeg")
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == f"{URL}/storage/v1/object/{BUCKET}/{OBJECT}"
    assert_auth(request)
    assert request.headers["x-upsert"] == "false"
    assert request.headers["content-type"] == "image/jpeg"
    assert request.content == b"\xff\xd8jpeg-bytes"


def test_get_reads_the_authenticated_path() -> None:
    seen: list[httpx.Request] = []
    store = supabase(lambda r: httpx.Response(200, content=b"png-bytes"), seen)
    assert store.get(OBJECT) == b"png-bytes"
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == f"{URL}/storage/v1/object/authenticated/{BUCKET}/{OBJECT}"
    assert_auth(request)
    assert "x-upsert" not in request.headers


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(404, json={"statusCode": "404", "code": "NoSuchKey", "error": "not_found"}),
        httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"}),
        httpx.Response(400, json={"statusCode": "404", "code": "NoSuchKey", "message": "Object not found"}),
    ],
)
def test_get_of_a_missing_object_is_none(response: httpx.Response) -> None:
    assert supabase(lambda r: response, []).get(OBJECT) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}),
        httpx.Response(404, json={"statusCode": "404", "code": "NoSuchBucket", "error": "Bucket not found"}),
        httpx.Response(400, json={"statusCode": "400", "error": "InvalidKey", "message": f"bad {KEY}"}),
        httpx.Response(400, content=b"<html>proxy</html>"),
        httpx.Response(401, json={"message": "invalid signature"}),
        httpx.Response(500, text=f"boom {KEY}"),
        httpx.Response(302, headers={"location": "https://elsewhere.example/"}),
    ],
)
def test_get_failures_and_a_missing_bucket_raise_without_the_key_or_body(response: httpx.Response) -> None:
    with pytest.raises(StorageError) as raised:
        supabase(lambda r: response, []).get(OBJECT)
    text = str(raised.value)
    assert KEY not in text and "boom" not in text and "proxy" not in text and "signature" not in text
    assert OBJECT in text  # the error names the object, which is built only from ids


def test_put_conflict_is_a_storage_error_without_the_key_or_body() -> None:
    body = {"statusCode": "409", "error": "Duplicate", "message": f"The resource already exists {KEY}"}
    with pytest.raises(StorageError) as raised:
        supabase(lambda r: httpx.Response(409, json=body), []).put(OBJECT, b"x", "image/jpeg")
    assert "HTTP 409" in str(raised.value)
    assert KEY not in str(raised.value) and "already exists" not in str(raised.value)
    assert KEY not in repr(raised.value)


def test_delete_sends_the_prefix_list_and_absent_is_fine() -> None:
    seen: list[httpx.Request] = []
    store = supabase(lambda r: httpx.Response(200, json=[]), seen)
    store.delete(OBJECT)
    [request] = seen
    assert request.method == "DELETE"
    assert str(request.url) == f"{URL}/storage/v1/object/{BUCKET}"
    assert_auth(request)
    assert json.loads(request.content) == {"prefixes": [OBJECT]}
    assert request.headers["content-type"] == "application/json"
    supabase(lambda r: httpx.Response(404), []).delete(OBJECT)
    missing = {"statusCode": "404", "error": "not_found", "message": "Object not found"}
    supabase(lambda r: httpx.Response(400, json=missing), []).delete(OBJECT)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text=f"boom {KEY}"),
        httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"}),
    ],
)
def test_delete_failures_raise(response: httpx.Response) -> None:
    with pytest.raises(StorageError) as raised:
        supabase(lambda r: response, []).delete(OBJECT)
    assert KEY not in str(raised.value) and "boom" not in str(raised.value)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout(f"timed out reading {KEY}"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError(f"refused {KEY}"),
        httpx.RemoteProtocolError("peer closed"),
    ],
)
@pytest.mark.parametrize("op", ["put", "get", "delete"])
def test_transport_errors_and_timeouts_are_storage_errors_without_the_key(op: str, error: httpx.HTTPError) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error

    store = supabase(fail, [])
    with pytest.raises(StorageError) as raised:
        if op == "put":
            store.put(OBJECT, b"x", "image/png")
        elif op == "get":
            store.get(OBJECT)
        else:
            store.delete(OBJECT)
    assert KEY not in str(raised.value)
    assert type(error).__name__ in str(raised.value)
    if isinstance(error, httpx.TimeoutException):
        assert "timed out" in str(raised.value)


@pytest.mark.parametrize("bad", BAD_KEYS)
def test_invalid_keys_are_refused_before_any_request(bad: str) -> None:
    seen: list[httpx.Request] = []
    store = supabase(lambda r: httpx.Response(200), seen)
    with pytest.raises(InvalidKey):
        store.put(bad, b"x", "image/jpeg")
    with pytest.raises(InvalidKey):
        store.get(bad)
    with pytest.raises(InvalidKey):
        store.delete(bad)
    assert seen == []


def test_valid_keys_pass_and_the_adapter_never_shows_the_key() -> None:
    assert validate_key(OBJECT) == OBJECT
    assert validate_key("firm/claim/receipt-ab12.png") == "firm/claim/receipt-ab12.png"
    store = supabase(lambda r: httpx.Response(200), [])
    assert KEY not in repr(store) and BUCKET in repr(store)
    store.close()


@pytest.mark.parametrize(
    ("url", "bucket", "key"),
    [("ftp://proj.supabase.co", BUCKET, KEY), ("https://", BUCKET, KEY), (URL, "../other", KEY), (URL, BUCKET, "")],
)
def test_the_adapter_refuses_bad_configuration(url: str, bucket: str, key: str) -> None:
    with pytest.raises(ValueError) as raised:
        SupabaseStorage(url, key, bucket, 5.0)
    assert KEY not in str(raised.value)


# Local backend


def test_local_storage_round_trip(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path / "root")
    assert store.get(OBJECT) is None
    store.delete(OBJECT)  # absent is fine
    store.put(OBJECT, b"bytes", "image/jpeg")
    assert (tmp_path / "root" / OBJECT).read_bytes() == b"bytes"
    assert store.get(OBJECT) == b"bytes"
    store.put(OBJECT, b"again", "image/jpeg")  # local disk keeps the old overwrite behavior
    assert store.get(OBJECT) == b"again"
    store.delete(OBJECT)
    assert store.get(OBJECT) is None


@pytest.mark.parametrize("bad", BAD_KEYS)
def test_local_storage_refuses_unsafe_keys(tmp_path: Path, bad: str) -> None:
    store = LocalStorage(tmp_path / "root")
    with pytest.raises(InvalidKey):
        store.put(bad, b"x", "image/jpeg")
    with pytest.raises(InvalidKey):
        store.get(bad)
    with pytest.raises(InvalidKey):
        store.delete(bad)
    assert not (tmp_path / "root").exists()


def test_local_storage_refuses_a_key_that_resolves_outside_the_root(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.jpg").write_bytes(b"secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    store = LocalStorage(root)
    with pytest.raises(InvalidKey):
        store.get("link/secret.jpg")
    with pytest.raises(InvalidKey):
        store.put("link/new.jpg", b"x", "image/jpeg")
    with pytest.raises(InvalidKey):
        store.delete("link/secret.jpg")
    assert sorted(p.name for p in outside.iterdir()) == ["secret.jpg"]


def test_local_storage_wraps_os_errors(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_bytes(b"x")
    store = LocalStorage(root)
    with pytest.raises(StorageError) as raised:
        store.put("file/child.jpg", b"x", "image/jpeg")  # a file where a directory must be
    assert not isinstance(raised.value, InvalidKey)
    (root / "dir").mkdir()
    with pytest.raises(StorageError):
        store.delete("dir")  # a directory is never an object
    if os.geteuid() != 0:  # root reads a mode-0 file
        (root / "locked.jpg").write_bytes(b"x")
        (root / "locked.jpg").chmod(0)
        try:
            with pytest.raises(StorageError):
                store.get("locked.jpg")
        finally:
            (root / "locked.jpg").chmod(0o600)
    assert repr(store) == f"LocalStorage({str(root)!r})"


# Settings


def prod(**values: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "prod",
        "neo4j_uri": "",  # the graph guard is tested elsewhere
        "storage_backend": "supabase",
        "supabase_url": URL,
        "supabase_service_key": KEY,
    }
    return Settings(**{**base, **values})  # type: ignore[arg-type]


def test_prod_supabase_settings_build_the_adapter_with_the_configured_timeout() -> None:
    settings = prod(storage_timeout_s=3.5)
    assert KEY not in repr(settings)
    store = build_storage(settings)
    assert isinstance(store, SupabaseStorage)
    assert store.base == f"{URL}/storage/v1/object" and store.bucket == BUCKET
    assert store._client.timeout == httpx.Timeout(3.5)
    store.close()


@pytest.mark.parametrize(
    ("values", "problem"),
    [
        ({"supabase_url": EXAMPLE_SUPABASE_URL}, "SUPABASE_URL is the example value"),
        ({"supabase_url": EXAMPLE_SUPABASE_URL + "/"}, "SUPABASE_URL is the example value"),
        ({"supabase_service_key": EXAMPLE_SUPABASE_SERVICE_KEY}, "SUPABASE_SERVICE_KEY is the example value"),
        ({"supabase_service_key": ""}, "SUPABASE_SERVICE_KEY is empty"),
        ({"supabase_url": "http://proj.supabase.co"}, "SUPABASE_URL must be an https URL with a host"),
        ({"supabase_url": "proj.supabase.co"}, "SUPABASE_URL must be an https URL with a host"),
    ],
)
def test_prod_refuses_unsafe_supabase_settings(values: dict[str, object], problem: str) -> None:
    with pytest.raises(ValidationError) as raised:
        prod(**values)
    assert problem in str(raised.value)
    assert KEY not in str(raised.value)  # hide_input_in_errors: the inputs are never echoed


def test_the_guard_applies_only_to_prod_on_supabase(tmp_path: Path) -> None:
    local = prod(storage_backend="local", supabase_url="http://x", supabase_service_key=EXAMPLE_SUPABASE_SERVICE_KEY)
    assert isinstance(build_storage(local), LocalStorage)
    dev = Settings(_env_file=None, app_env="dev", neo4j_uri="", storage_backend="supabase")
    assert dev.supabase_url == EXAMPLE_SUPABASE_URL
    assert isinstance(build_storage(Settings(_env_file=None, storage_dir=tmp_path)), LocalStorage)


def test_bucket_names_are_validated_in_settings() -> None:
    with pytest.raises(ValidationError):
        prod(supabase_bucket="../other")
