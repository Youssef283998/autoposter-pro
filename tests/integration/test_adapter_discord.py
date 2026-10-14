"""Discord adapter tests with mocked HTTP (respx)."""

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
from autoposter.platforms.discord import DiscordAdapter
from autoposter.utils.timeutil import utcnow

WEBHOOK_URL = "https://discord.com/api/webhooks/123/xyz"


def _webhook_account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="discord",
        label="default",
        kind="webhook",
        secrets={"webhook_url": WEBHOOK_URL},
        status=AccountStatus.ACTIVE,
    )


def _bot_account() -> AccountCredentials:
    return AccountCredentials(
        id=2,
        platform="discord",
        label="bot",
        kind="bot_token",
        secrets={"bot_token": "bot-token-123", "channel_id": "999"},
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello discord") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="discord", account_id=1)


@respx.mock
async def test_discord_webhook_text_success() -> None:
    route = respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(200, json={"id": "msg-1", "channel_id": "999"})
    )
    adapter = DiscordAdapter(_webhook_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert result.platform_post_id == "msg-1"
    assert route.called


@respx.mock
async def test_discord_bot_text_success() -> None:
    route = respx.post("https://discord.com/api/v10/channels/999/messages").mock(
        return_value=httpx.Response(200, json={"id": "msg-2", "channel_id": "999"})
    )
    adapter = DiscordAdapter(_bot_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert route.called


@respx.mock
async def test_discord_rate_limit() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(
            429, json={"message": "rate limited", "retry_after": 5.5}
        )
    )
    adapter = DiscordAdapter(_webhook_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED
    assert result.retry_after_seconds == 5.5


@respx.mock
async def test_discord_auth_expired() -> None:
    respx.post(WEBHOOK_URL).mock(
        return_value=httpx.Response(401, json={"message": "invalid webhook"})
    )
    adapter = DiscordAdapter(_webhook_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.AUTH_EXPIRED


@respx.mock
async def test_discord_validate_char_limit() -> None:
    adapter = DiscordAdapter(_webhook_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 2500), _target())
    assert any(i.field == "body" for i in issues)


@respx.mock
async def test_discord_healthcheck_webhook() -> None:
    respx.get(WEBHOOK_URL).mock(
        return_value=httpx.Response(200, json={"id": "123", "name": "test"})
    )
    adapter = DiscordAdapter(_webhook_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert status.ok
