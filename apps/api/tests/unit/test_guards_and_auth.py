"""Rate-limit client address behind proxies, and the dev provider next to a remote graph (final review 2026-10-19)."""

from __future__ import annotations

import pytest

from recallops.api.guards import client_address
from recallops.auth.dev import DevAuthProvider, UnsafeDevAuth
from recallops.settings import Settings


def scope(peer: str, forwarded: str | None) -> dict[str, object]:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return {"client": (peer, 1234), "headers": headers}


def test_without_trusted_proxies_the_socket_peer_is_the_client() -> None:
    assert client_address(scope("10.0.0.5", "6.6.6.6"), 0) == "10.0.0.5"


def test_behind_one_proxy_the_right_most_entry_is_the_client_and_spoofing_does_not_help() -> None:
    assert client_address(scope("10.0.0.5", "203.0.113.7"), 1) == "203.0.113.7"
    # a client that sends its own X-Forwarded-For only adds entries on the left
    assert client_address(scope("10.0.0.5", "1.1.1.1, 2.2.2.2, 203.0.113.7"), 1) == "203.0.113.7"
    assert client_address(scope("10.0.0.5", None), 1) == "10.0.0.5"


SECRET = "w" * 40


def from_web(peer: str, client_ip: str, secret: str | None) -> dict[str, object]:
    headers = [(b"x-recallops-client-ip", client_ip.encode())]
    if secret is not None:
        headers.append((b"x-recallops-web-secret", secret.encode()))
    return {"client": (peer, 1234), "headers": headers}


def test_the_web_app_names_the_claimant_only_with_the_shared_secret() -> None:
    """Every portal call comes from the web server; without this, all claimants shared its one address (PR 9)."""
    assert client_address(from_web("10.0.0.9", "198.51.100.4", SECRET), 0, SECRET) == "198.51.100.4"
    assert client_address(from_web("10.0.0.9", "2001:db8::1", SECRET), 1, SECRET) == "2001:db8::1"
    # a direct caller cannot pick its bucket: wrong, missing, or unconfigured secret falls back to the peer
    assert client_address(from_web("10.0.0.9", "198.51.100.4", "guess"), 0, SECRET) == "10.0.0.9"
    assert client_address(from_web("10.0.0.9", "198.51.100.4", None), 0, SECRET) == "10.0.0.9"
    assert client_address(from_web("10.0.0.9", "198.51.100.4", SECRET), 0, "") == "10.0.0.9"
    # only an IP address is believed, never free text that would become a shared key
    assert client_address(from_web("10.0.0.9", "everyone", SECRET), 0, SECRET) == "10.0.0.9"


def test_the_dev_provider_refuses_a_remote_fraud_graph() -> None:
    with pytest.raises(UnsafeDevAuth, match="remote fraud graph"):
        DevAuthProvider(Settings(neo4j_uri="neo4j+s://abc.databases.neo4j.io"), environ={})
    DevAuthProvider(Settings(neo4j_uri="bolt://localhost:57687"), environ={})
