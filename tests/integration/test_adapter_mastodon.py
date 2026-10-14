"""Mastodon adapter tests with mocked HTTP (respx)."""

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
from autoposter.platforms.mastodon import MastodonAdapter
from autoposter.utils.timeutil import utcnow

BASE = "https://mastodon.example"


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="mastodon",
        label="default",
        kind="oauth",
        secrets={"api_base": BASE, "access_token": "tok-123"},
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello mastodon") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="mastodon", account_id=1)


@respx.mock
async def test_mastodon_text_success() -> None:
    route = respx.post(f"{BASE}/api/v1/statuses").mock(
        return_value=httpx.Response(
            200, json={"id": "1001", "url": f"{BASE}/@user/1001"}
        )
    )
    adapter = MastodonAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert result.platform_post_id == "1001"
    assert result.url == f"{BASE}/@user/1001"
    assert route.called


@respx.mock
async def test_mastodon_rate_limit() -> None:
    respx.post(f"{BASE}/api/v1/statuses").mock(
        return_value=httpx.Response(429, text="rate limited", headers={"Retry-After": "60"})
    )
    adapter = MastodonAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED
    assert result.retry_after_seconds == 60.0


@respx.mock
async def test_mastodon_auth_expired() -> None:
    respx.post(f"{BASE}/api/v1/statuses").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    adapter = MastodonAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.AUTH_EXPIRED


@respx.mock
async def test_mastodon_server_error() -> None:
    respx.post(f"{BASE}/api/v1/statuses").mock(
        return_value=httpx.Response(503, text="unavailable")
    )
    adapter = MastodonAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RETRYABLE


@respx.mock
async def test_mastodon_validate_char_limit() -> None:
    adapter = MastodonAdapter(_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 600), _target())
    assert any(i.field == "body" for i in issues)


@respx.mock
async def test_mastodon_healthcheck() -> None:
    respx.get(f"{BASE}/api/v1/accounts/verify_credentials").mock(
        return_value=httpx.Response(200, json={"id": "1", "username": "test"})
    )
    adapter = MastodonAdapter(_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert status.ok
