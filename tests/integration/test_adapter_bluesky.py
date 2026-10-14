"""Bluesky adapter tests with mocked atproto client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import (
    AccountCredentials,
    AccountStatus,
    Post,
    PostTarget,
)
from autoposter.platforms.bluesky import BlueskyAdapter
from autoposter.utils.timeutil import utcnow


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="bluesky",
        label="default",
        kind="app_password",
        secrets={"handle": "test.bsky.social", "app_password": "pw-123"},
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello bluesky") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="bluesky", account_id=1)


def test_bluesky_registered() -> None:
    from autoposter.platforms.registry import get_platform, list_platforms

    assert "bluesky" in list_platforms()
    assert get_platform("bluesky") is BlueskyAdapter


async def test_bluesky_validate_char_limit() -> None:
    adapter = BlueskyAdapter(_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 400), _target())
    assert any(i.field == "body" for i in issues)


async def test_bluesky_validate_ok() -> None:
    adapter = BlueskyAdapter(_account(), PlatformCaps())
    issues = await adapter.validate(_post("short"), _target())
    assert issues == []


async def test_bluesky_publish_success() -> None:
    adapter = BlueskyAdapter(_account(), PlatformCaps())

    mock_client = MagicMock()
    mock_client.me = MagicMock()
    mock_client.me.did = "did:plc:test"
    mock_client.me.handle = "test.bsky.social"
    mock_client.login = MagicMock()
    mock_client.com.atproto.repo.create_record = AsyncMock(
        return_value=MagicMock(uri="at://did:plc:test/app.bsky.feed.post/abc123")
    )

    with patch("atproto.Client", return_value=mock_client):
        result = await adapter.publish(_post(), _target(), "k1")

    assert result.success
    assert result.platform_post_id == "at://did:plc:test/app.bsky.feed.post/abc123"
    assert "bsky.app" in (result.url or "")


async def test_bluesky_healthcheck() -> None:
    adapter = BlueskyAdapter(_account(), PlatformCaps())

    mock_client = MagicMock()
    mock_client.me = MagicMock()
    mock_client.me.handle = "test.bsky.social"
    mock_client.login = MagicMock()

    with patch("atproto.Client", return_value=mock_client):
        status = await adapter.healthcheck()

    assert status.ok
