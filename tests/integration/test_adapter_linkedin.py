"""LinkedIn adapter tests with mocked HTTP (respx)."""

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
from autoposter.platforms.linkedin import LinkedInAdapter
from autoposter.utils.timeutil import utcnow

API = "https://api.linkedin.com/v2"


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="linkedin",
        label="default",
        kind="oauth",
        secrets={
            "access_token": "tok-123",
            "refresh_token": "ref-456",
            "client_id": "cid",
            "client_secret": "csecret",
            "person_urn": "urn:li:person:abc",
        },
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello LinkedIn") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="linkedin", account_id=1)


@respx.mock
async def test_linkedin_text_success() -> None:
    route = respx.post(f"{API}/ugcPosts").mock(
        return_value=httpx.Response(201, headers={"x-restli-id": "urn:li:share:123"})
    )
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert result.platform_post_id == "urn:li:share:123"
    assert "linkedin.com" in (result.url or "")
    assert route.called


@respx.mock
async def test_linkedin_rate_limit() -> None:
    respx.post(f"{API}/ugcPosts").mock(
        return_value=httpx.Response(
            429, json={"message": "rate limited"}, headers={"Retry-After": "60"}
        )
    )
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED
    assert result.retry_after_seconds == 60.0


@respx.mock
async def test_linkedin_auth_expired() -> None:
    respx.post(f"{API}/ugcPosts").mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.AUTH_EXPIRED


@respx.mock
async def test_linkedin_server_error() -> None:
    respx.post(f"{API}/ugcPosts").mock(
        return_value=httpx.Response(503, json={"message": "unavailable"})
    )
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RETRYABLE


@respx.mock
async def test_linkedin_validate_char_limit() -> None:
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 4000), _target())
    assert any(i.field == "body" for i in issues)


@respx.mock
async def test_linkedin_healthcheck() -> None:
    respx.get(f"{API}/me").mock(
        return_value=httpx.Response(200, json={"id": "abc"})
    )
    adapter = LinkedInAdapter(_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert status.ok
