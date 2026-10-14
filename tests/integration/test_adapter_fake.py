"""Fake platform adapter tests."""

from __future__ import annotations

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import AccountCredentials, AccountStatus, Post, PostTarget
from autoposter.platforms.fake import FakePlatform
from autoposter.platforms.registry import get_platform, list_platforms


def _account() -> AccountCredentials:
    return AccountCredentials(
        id=1,
        platform="fake",
        label="default",
        kind="manual",
        secrets={},
        status=AccountStatus.ACTIVE,
    )


def _post(body: str = "hello") -> Post:
    return Post(
        body=body,
        scheduled_at_utc=__import__("autoposter.utils.timeutil", fromlist=["utcnow"]).utcnow(),
        timezone="UTC",
    )


def _target(post_id: int = 1) -> PostTarget:
    return PostTarget(post_id=post_id, platform="fake", account_id=1)


def test_fake_registered() -> None:
    assert "fake" in list_platforms()
    assert get_platform("fake") is FakePlatform


async def test_fake_publish_success() -> None:
    adapter = FakePlatform(_account(), PlatformCaps())
    result = await adapter.publish(_post(), _target(), "key-1")
    assert result.success
    assert result.platform_post_id is not None
    assert len(adapter.published) == 1


async def test_fake_validate_ok() -> None:
    adapter = FakePlatform(_account(), PlatformCaps())
    issues = await adapter.validate(_post("short"), _target())
    assert issues == []


async def test_fake_validate_too_long() -> None:
    adapter = FakePlatform(_account(), PlatformCaps())
    issues = await adapter.validate(_post("x" * 3000), _target())
    assert any(i.field == "body" for i in issues)


async def test_fake_find_recent_by_hash() -> None:
    adapter = FakePlatform(_account(), PlatformCaps())
    post = _post()
    post = post.model_copy(update={"content_hash": "abc123"})
    await adapter.publish(post, _target(), "k")
    assert await adapter.find_recent_by_hash("abc123") is not None
    assert await adapter.find_recent_by_hash("nope") is None


async def test_fake_scripted_results() -> None:
    from autoposter.core.models import ErrorCategory, PublishResult

    adapter = FakePlatform(_account(), PlatformCaps())
    adapter.scripted_results = [
        PublishResult(success=False, error_category=ErrorCategory.RATE_LIMITED)
    ]
    result = await adapter.publish(_post(), _target(), "k")
    assert not result.success
    assert result.error_category is ErrorCategory.RATE_LIMITED
