"""X adapter tests with mocked HTTP (respx)."""

from __future__ import annotations

import httpx
import respx

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import (
    AccountCredentials,
    AccountStatus,
    ErrorCategory,
    Post,
    PostTarget,
)
from autoposter.platforms.x import XAdapter
from autoposter.utils.timeutil import utcnow

API = "https://api.twitter.com/2"


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="x",
        label="default",
        kind="oauth",
        secrets={
            "access_token": "tok-123",
            "refresh_token": "ref-456",
            "client_id": "cid",
            "client_secret": "csecret",
        },
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello X") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="x", account_id=1)


@respx.mock
async def test_x_text_success() -> None:
    route = respx.post(f"{API}/tweets").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "123", "text": "hello X"}}
        )
    )
    adapter = XAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert result.platform_post_id == "123"
    assert "x.com" in (result.url or "")
    assert route.called


@respx.mock
async def test_x_rate_limit() -> None:
    respx.post(f"{API}/tweets").mock(
        return_value=httpx.Response(
            429,
            json={"error": "rate limited"},
            headers={"x-rate-limit-reset": "9999999999"},
        )
    )
    adapter = XAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED


@respx.mock
async def test_x_auth_expired() -> None:
    respx.post(f"{API}/tweets").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    adapter = XAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.AUTH_EXPIRED


@respx.mock
async def test_x_server_error() -> None:
    respx.post(f"{API}/tweets").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )
    adapter = XAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RETRYABLE


@respx.mock
async def test_x_validate_weighted_length() -> None:
    adapter = XAdapter(_account(), PlatformCaps())
    # 270 chars + 1 URL (counts as 23) = 293 > 280
    body = "a" * 270 + " https://example.com"
    issues = await adapter.validate(_post(body), _target())
    assert any(i.field == "body" for i in issues)


@respx.mock
async def test_x_healthcheck() -> None:
    respx.get(f"{API}/users/me").mock(
        return_value=httpx.Response(200, json={"data": {"id": "1"}})
    )
    adapter = XAdapter(_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert status.ok
