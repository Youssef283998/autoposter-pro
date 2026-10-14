"""Web dashboard tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoposter.config.settings import load_settings
from autoposter.core.db import init_database, make_engine
from autoposter.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client with a fresh database."""
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "default_timezone: UTC\ntick_seconds: 5\n"
        f'data_dir: "{tmp_path}/data"\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()
    engine = make_engine(paths.db_path)
    init_database(engine)
    engine.dispose()

    app = create_app(settings=settings)
    return TestClient(app)


def test_queue_page(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Queue" in resp.text


def test_calendar_page(client: TestClient) -> None:
    resp = client.get("/calendar")
    assert resp.status_code == 200
    assert "Calendar" in resp.text


def test_composer_page(client: TestClient) -> None:
    resp = client.get("/composer")
    assert resp.status_code == 200
    assert "Composer" in resp.text


def test_logs_page(client: TestClient) -> None:
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert "Logs" in resp.text


def test_health_page(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "Health" in resp.text


def test_create_post_requires_csrf(client: TestClient) -> None:
    resp = client.post(
        "/posts",
        data={
            "body": "test",
            "platforms": "fake",
            "datetime_str": "2026-10-14 09:00",
            "timezone": "UTC",
            "csrf_token": "invalid",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "error=csrf" in resp.headers.get("location", "")


def test_create_post_with_csrf(client: TestClient) -> None:
    # Get a CSRF token from the composer page.
    resp = client.get("/composer")
    assert resp.status_code == 200
    # Extract CSRF token from the form.
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
    assert match is not None
    csrf_token = match.group(1)

    resp = client.post(
        "/posts",
        data={
            "body": "test post",
            "platforms": "fake",
            "datetime_str": "2026-10-14 09:00",
            "timezone": "UTC",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_login_redirect_when_no_password(client: TestClient) -> None:
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 302


def test_static_files(client: TestClient) -> None:
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
