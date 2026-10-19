"""The backend test console exists only with the dev auth provider outside prod, and stays out of the contract."""

from __future__ import annotations

from fastapi.testclient import TestClient

from recallops.api import dev_console
from recallops.api.app import app
from recallops.api.export_openapi import render
from recallops.settings import Settings


def test_console_is_served_in_development_without_caching() -> None:
    response = TestClient(app).get("/dev/console")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "RecallOps" in response.text


def test_console_is_not_part_of_the_api_contract() -> None:
    assert "/dev/console" not in render()


def test_console_is_disabled_with_clerk_or_in_prod() -> None:
    assert dev_console.enabled(Settings(auth_provider="dev", app_env="dev"))
    assert dev_console.enabled(Settings(auth_provider="dev", app_env="test"))
    assert not dev_console.enabled(Settings(auth_provider="clerk", app_env="dev"))
    assert not dev_console.enabled(Settings.model_construct(auth_provider="dev", app_env="prod"))


def test_console_page_has_no_external_resources() -> None:
    html = dev_console.PAGE.read_text(encoding="utf-8")
    for marker in ("<script src=", '<link rel="stylesheet" href="http', "@import", "cdn."):
        assert marker not in html, marker
