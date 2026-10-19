"""Local services listen on loopback only (README and docs/contracts/CHANGES.md say so; final review 2026-10-19)."""

from __future__ import annotations

import yaml

from recallops.settings import REPO_ROOT


def test_every_published_port_is_bound_to_loopback() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    published = [port for service in compose["services"].values() for port in service.get("ports", [])]
    assert published and all(str(port).startswith("127.0.0.1:") for port in published), published
