"""The platform operator by email (owner decision 2026-10-22): an account is the operator while one of its verified
addresses is in PLATFORM_OPERATOR_EMAILS, so the role survives a deleted and recreated account. A Clerk answer is kept
for a minute and a failure for 15 s, one lookup runs at a time per account, the whole call has one deadline, and an
account Clerk cannot vouch for is never the operator (edge-case review of the change)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from recallops import access
from recallops.settings import Settings, canonical_email

OPERATOR = "kartikeyamandhar777@gmail.com"
KELVIN = chr(0x212A)  # KELVIN SIGN: lowercases to an ASCII k


def _settings(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "auth_provider": "clerk",
        "clerk_secret_key": SecretStr("sk_test_lookup"),
        "platform_operator_emails": f" {OPERATOR.upper()} , ops@example.org",
    }
    return Settings(**(values | over))


def _user(*emails: tuple[str, str], primary: int = 0) -> dict[str, Any]:
    return {
        "primary_email_address_id": f"idn_{primary}",
        "email_addresses": [
            {"id": f"idn_{i}", "email_address": address, "verification": {"status": status}}
            for i, (address, status) in enumerate(emails)
        ],
    }


class FakeClerk:
    """Clerk's Backend API behind httpx.MockTransport: users by id, or a handler override for odd answers."""

    def __init__(self) -> None:
        self.users: dict[str, Any] = {}
        self.calls: list[str] = []
        self.answer: Callable[[httpx.Request], httpx.Response] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        assert request.headers["Authorization"] == "Bearer sk_test_lookup"
        if self.answer is not None:
            return self.answer(request)
        user_id = request.url.path.rsplit("/", 1)[1]
        if user_id not in self.users:
            return httpx.Response(404, json={"errors": []})
        return httpx.Response(200, json=self.users[user_id])


@pytest.fixture()
def clerk(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeClerk]:
    fake = FakeClerk()
    monkeypatch.setattr("recallops.access._clerk_http", httpx.Client(transport=httpx.MockTransport(fake.handle)))
    access._clerk_cache.clear()
    yield fake
    access._clerk_cache.clear()


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr("recallops.access.time.monotonic", lambda: now[0])
    return now


def test_a_verified_listed_address_is_the_operator_whatever_the_account_id(clerk: FakeClerk) -> None:
    clerk.users["user_first"] = _user((OPERATOR, "verified"))
    clerk.users["user_recreated"] = _user(("KartikeyaMandhar777@Gmail.com", "verified"))
    clerk.users["user_dotted"] = _user(("kartikeya.mandhar777@gmail.com", "verified"))  # Google may return the dots
    clerk.users["user_googlemail"] = _user(("kartikeyamandhar777@googlemail.com", "verified"))
    clerk.users["user_secondary"] = _user(("work@example.net", "verified"), ("ops@example.org", "verified"))
    for user_id in clerk.users:
        assert access.is_operator(_settings(), user_id), user_id


@pytest.mark.parametrize(
    "address",
    [
        "kartikeyamandhar777+clerk_test@gmail.com",  # a +tag is another address
        f"{KELVIN}artikeyamandhar777@gmail.com",
        "kartikeyamandhar777@gmail.com.evil.example",
        "kartikeyamandhar777@gmai1.com",
    ],
)
def test_look_alike_addresses_are_not_the_operator(clerk: FakeClerk, address: str) -> None:
    clerk.users["user_lookalike"] = _user((address, "verified"))
    assert not access.is_operator(_settings(), "user_lookalike")


def test_an_unverified_or_unlisted_address_is_not_the_operator(clerk: FakeClerk) -> None:
    for status in ("unverified", "expired", "transferable", "failed"):
        access._clerk_cache.clear()
        clerk.users["user_claims_it"] = _user(("someone@example.net", "verified"), (OPERATOR, status))
        assert not access.is_operator(_settings(), "user_claims_it"), status
    clerk.users["user_company"] = _user(("buyer@primark.example", "verified"))
    assert not access.is_operator(_settings(), "user_company")
    assert not access.is_operator(_settings(), "user_unknown")  # Clerk answers 404
    assert not access.is_operator(_settings(), None)


@pytest.mark.parametrize(
    "body",
    [
        [],
        "user",
        {"email_addresses": "x"},
        {"email_addresses": [{"email_address": OPERATOR, "verification": "verified"}]},
        {"email_addresses": [{"email_address": 7, "verification": {"status": "verified"}}]},
        {"email_addresses": [None, 3, {"email_address": OPERATOR, "verification": ["verified"]}]},
    ],
)
def test_an_odd_clerk_answer_fails_closed_without_raising(clerk: FakeClerk, body: Any) -> None:
    clerk.answer = lambda _: httpx.Response(200, json=body)
    assert not access.is_operator(_settings(), "user_odd")
    assert access.requester_domain(_settings(), "user_odd") is None


def test_a_redirect_or_a_body_that_is_not_json_fails_closed(clerk: FakeClerk) -> None:
    clerk.answer = lambda _: httpx.Response(302, headers={"location": "https://elsewhere.example/"})
    assert not access.is_operator(_settings(), "user_redirected")
    clerk.answer = lambda _: httpx.Response(200, content=b"<html>maintenance</html>")
    assert not access.is_operator(_settings(), "user_html")


def test_a_failure_is_kept_15_seconds_and_a_success_a_minute(clerk: FakeClerk, clock: list[float]) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("unreachable", request=request)

    clerk.users["user_first"] = _user((OPERATOR, "verified"))
    clerk.answer = down
    for _ in range(5):
        assert not access.is_operator(_settings(), "user_first")  # fails closed, one wait per window
    assert len(clerk.calls) == 1
    clerk.answer = None
    clock[0] += access.CLERK_FAILURE_TTL_S + 1
    for _ in range(5):
        assert access.is_operator(_settings(), "user_first")
    assert len(clerk.calls) == 2
    clerk.users["user_first"] = _user(("left@example.com", "verified"))  # the address was removed from the account
    clock[0] += access.CLERK_LOOKUP_TTL_S + 1
    assert not access.is_operator(_settings(), "user_first")
    assert len(clerk.calls) == 3


def test_one_deadline_for_the_whole_call_and_a_size_cap(clerk: FakeClerk, clock: list[float]) -> None:
    class Trickle(httpx.SyncByteStream):
        """Each byte arrives in time for httpx's read timeout, but the whole answer takes far too long."""

        def __iter__(self) -> Iterator[bytes]:
            for byte in json.dumps(_user((OPERATOR, "verified"))).encode():
                clock[0] += 0.5
                yield bytes([byte])

    clerk.answer = lambda _: httpx.Response(200, stream=Trickle())
    assert not access.is_operator(_settings(), "user_slow")
    access._clerk_cache.clear()
    clerk.answer = lambda _: httpx.Response(200, content=b" " * (access.CLERK_MAX_BYTES + 1))
    assert not access.is_operator(_settings(), "user_huge")


def test_one_lookup_at_a_time_per_account(clerk: FakeClerk) -> None:
    release = threading.Event()
    clerk.users["user_first"] = _user((OPERATOR, "verified"))

    def slow(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, json=clerk.users["user_first"])

    clerk.answer = slow
    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(access.is_operator(_settings(), "user_first"))) for _ in range(6)
    ]
    for t in threads:
        t.start()
    release.set()
    for t in threads:
        t.join(10)
    assert results == [True] * 6
    assert len(clerk.calls) == 1


def test_no_lookup_without_a_real_key_a_clerk_id_or_a_listed_address(clerk: FakeClerk) -> None:
    assert access.is_operator(_settings(platform_operator_user_ids="user_listed"), "user_listed")
    assert not access.is_operator(_settings(auth_provider="dev"), "user_first")
    assert not access.is_operator(_settings(clerk_secret_key=SecretStr("sk_test_fake")), "user_first")
    assert not access.is_operator(_settings(platform_operator_emails=""), "user_first")
    assert not access.is_operator(_settings(), "user_../../v1/clients")  # never part of a URL path
    assert clerk.calls == []


def test_the_request_hint_still_reads_the_primary_address(clerk: FakeClerk) -> None:
    clerk.users["user_first"] = _user(("a@gmail.com", "verified"), ("b@3M.com", "verified"), primary=1)
    assert access.requester_domain(_settings(), "user_first") == "3m.com"
    assert access.requester_domain(_settings(auth_provider="dev"), "user_first") is None


def test_addresses_compare_as_gmail_reads_them() -> None:
    assert canonical_email(" Kartikeya.Mandhar777@GoogleMail.com ") == OPERATOR
    assert canonical_email("a.b+tag@gmail.com") == "ab+tag@gmail.com"  # the tag stays
    assert canonical_email("first.last@example.com") == "first.last@example.com"  # dots matter elsewhere
    assert _settings().operator_emails() == frozenset({OPERATOR, "ops@example.org"})


@pytest.mark.parametrize(
    ("listed", "message"),
    [
        ("kartikeya, ops@example.com", "ASCII email addresses"),
        ("mailto:ops@example.com", "ASCII email addresses"),
        (f"{KELVIN}ops@example.com", "ASCII email addresses"),
        ("ops+clerk_test@example.com", "clerk_test"),
    ],
)
def test_a_malformed_or_unsafe_operator_list_is_refused(listed: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(platform_operator_emails=listed)


def test_production_refuses_operator_addresses_it_could_never_check() -> None:
    prod = {"app_env": "prod", "access_approval": "required", "neo4j_uri": ""}
    prod |= {"portal_base_url": "https://recallops.example/claim", "platform_operator_emails": OPERATOR}
    for broken in (
        {"auth_provider": "dev"},
        {"auth_provider": "clerk"},
        {"auth_provider": "clerk", "clerk_secret_key": ""},
    ):
        with pytest.raises(ValidationError, match="needs AUTH_PROVIDER=clerk and a real CLERK_SECRET_KEY"):
            Settings.model_validate(prod | broken)
    try:
        Settings.model_validate(prod | {"auth_provider": "clerk", "clerk_secret_key": "sk_test_real"})
    except ValidationError as exc:  # later production checks (the Clerk issuer and others) may still refuse
        assert "PLATFORM_OPERATOR_EMAILS" not in str(exc)
