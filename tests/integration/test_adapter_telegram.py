"""Telegram adapter tests with mocked HTTP (respx)."""

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
from autoposter.platforms.telegram import TelegramAdapter
from autoposter.utils.timeutil import utcnow

API = "https://api.telegram.org"


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="telegram",
        label="default",
        kind="bot_token",
        secrets={"bot_token": "123:abc", "chat_id": "@testchannel"},
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello telegram") -> Post:
    return Post(body=body, scheduled_at_utc=utcnow(), timezone="UTC")


def _target() -> PostTarget:
    return PostTarget(post_id=1, platform="telegram", account_id=1)


@respx.mock
async def test_telegram_send_message_success() -> None:
    route = respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert result.success
    assert result.platform_post_id == "42"
    assert route.called


@respx.mock
async def test_telegram_rate_limit() -> None:
    respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "parameters": {"retry_after": 30},
            },
        )
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED
    assert result.retry_after_seconds == 30.0


@respx.mock
async def test_telegram_auth_expired() -> None:
    respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(401, json={"ok": False, "error_code": 401})
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.AUTH_EXPIRED


@respx.mock
async def test_telegram_server_error_retryable() -> None:
    respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(500, json={"ok": False})
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "k1")
    assert not result.success
    assert result.error_category is ErrorCategory.RETRYABLE


@respx.mock
async def test_telegram_validate_char_limit() -> None:
    adapter = TelegramAdapter(_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 5000), _target())
    assert any(i.field == "body" for i in issues)


@respx.mock
async def test_telegram_healthcheck_ok() -> None:
    respx.get(f"{API}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1}})
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert status.ok


@respx.mock
async def test_telegram_healthcheck_bad_token() -> None:
    respx.get(f"{API}/getMe").mock(
        return_value=httpx.Response(401, json={"ok": False})
    )
    adapter = TelegramAdapter(_account(), PlatformCaps())
    status = await adapter.healthcheck()
    assert not status.ok
    assert status.needs_reauth
