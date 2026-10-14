"""Fake platform for demo mode and tests.

Records every publish in memory, supports scripted results, and can simulate
rate limits, auth failures and ambiguous outcomes — all without network calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import ClassVar

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import (
    AccountCredentials,
    HealthStatus,
    PlatformLimits,
    Post,
    PostTarget,
    PublishResult,
    ValidationIssue,
)
from autoposter.platforms.base import Platform
from autoposter.platforms.registry import register_platform


@dataclass
class FakePublishRecord:
    """One recorded publish."""

    post_id: int
    platform: str
    account_id: int
    body: str
    idempotency_key: str
    media_count: int
    timestamp: float = field(default_factory=time.time)


@register_platform
class FakePlatform(Platform):
    """In-memory platform. Scriptable for tests and demo mode."""

    name: ClassVar[str] = "fake"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=2000,
        max_media=4,
        allowed_mime=frozenset({"image/jpeg", "image/png", "image/gif", "video/mp4"}),
        max_bytes=25 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=True,
        supports_alt_text=True,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self.published: list[FakePublishRecord] = []
        self.scripted_results: list[PublishResult] = []
        self.refresh_calls = 0
        self._hash_index: dict[str, str] = {}

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        if len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=f"Text is {len(body)} chars; limit is {self.limits.max_chars}.",
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=f"{len(post.media)} media items; limit is {self.limits.max_media}.",
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not allowed on {self.name}.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"File is {item.size} bytes; limit is {self.limits.max_bytes}.",
                    )
                )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        if self.scripted_results:
            return self.scripted_results.pop(0)
        body = target.variant_body or post.variants.get(target.platform, post.body)
        self.published.append(
            FakePublishRecord(
                post_id=post.id or 0,
                platform=self.name,
                account_id=target.account_id,
                body=body,
                idempotency_key=idempotency_key,
                media_count=len(post.media),
            )
        )
        self._hash_index[post.content_hash] = f"fake-{len(self.published)}"
        return PublishResult(
            success=True,
            platform_post_id=f"fake-{len(self.published)}",
            url=f"https://fake.example/p/{len(self.published)}",
        )

    async def refresh_auth(self) -> None:
        self.refresh_calls += 1

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="fake platform always healthy")

    async def find_recent_by_hash(self, content_hash: str) -> str | None:
        return self._hash_index.get(content_hash)
