"""Discord adapter: webhook and bot token support.

Threads are supported via the ``thread_name`` webhook parameter (creates a
new thread in forum channels).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, ClassVar

import httpx

from autoposter.config.schema import PlatformCaps
from autoposter.core.models import (
    AccountCredentials,
    ErrorCategory,
    HealthStatus,
    MediaItem,
    PlatformLimits,
    Post,
    PostTarget,
    PublishResult,
    ValidationIssue,
)
from autoposter.platforms.base import Platform
from autoposter.platforms.registry import register_platform
from autoposter.utils.logging import get_logger
from autoposter.utils.redact import redact_text

logger = get_logger("platform.discord")

API_BASE = "https://discord.com/api/v10"


@register_platform
class DiscordAdapter(Platform):
    """Discord webhook or bot adapter."""

    name: ClassVar[str] = "discord"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=2000,
        max_media=10,
        allowed_mime=frozenset(
            {
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
                "video/mp4",
                "video/webm",
                "audio/mpeg",
                "audio/ogg",
            }
        ),
        max_bytes=25 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=True,
        supports_alt_text=False,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self._webhook_url = account.secrets.get("webhook_url", "")
        self._bot_token = account.secrets.get("bot_token", "")
        self._channel_id = account.secrets.get("channel_id", "")
        self._thread_name = account.secrets.get("thread_name", "")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    @property
    def _is_webhook(self) -> bool:
        return bool(self._webhook_url)

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        if len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=f"Text is {len(body)} chars; Discord limit is {self.limits.max_chars}.",
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=(
                        f"{len(post.media)} media items; "
                        f"Discord limit is {self.limits.max_media}."
                    ),
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by Discord.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=(
                            f"File is {item.size} bytes; "
                            f"Discord limit is {self.limits.max_bytes}."
                        ),
                    )
                )
        if not self._is_webhook and not self._channel_id:
            issues.append(
                ValidationIssue(
                    field="account",
                    message="No channel_id configured for this Discord bot account.",
                )
            )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        try:
            if self._is_webhook:
                return await self._publish_webhook(body, media)
            return await self._publish_bot(body, media)
        except httpx.TimeoutException:
            return PublishResult(
                success=False,
                error_category=ErrorCategory.AMBIGUOUS,
                raw_response_redacted="timeout after send",
            )
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        except httpx.RequestError as exc:
            return PublishResult(
                success=False,
                error_category=ErrorCategory.RETRYABLE,
                raw_response_redacted=redact_text(str(exc)),
            )

    async def _publish_webhook(
        self, content: str, media: list[MediaItem]
    ) -> PublishResult:
        payload: dict[str, object] = {"content": content}
        if self._thread_name:
            payload["thread_name"] = self._thread_name

        if not media:
            resp = await self._client.post(self._webhook_url, json=payload)
            return self._parse_response(resp)

        # Multipart with files.
        files: list[tuple[str, Any]] = []
        for i, item in enumerate(media):
            path = item.stored_path or item.source_path
            files.append((f"files[{i}]", open(path, "rb")))  # noqa: SIM115
        try:
            resp = await self._client.post(
                self._webhook_url,
                data={"payload_json": json.dumps(payload)},
                files=files,
            )
        finally:
            for _, fh in files:
                fh.close()
        return self._parse_response(resp)

    async def _publish_bot(
        self, content: str, media: list[MediaItem]
    ) -> PublishResult:
        headers = {"Authorization": f"Bot {self._bot_token}"}
        url = f"/channels/{self._channel_id}/messages"

        if not media:
            resp = await self._client.post(
                url, json={"content": content}, headers=headers
            )
            return self._parse_response(resp)

        files: list[tuple[str, Any]] = []
        for i, item in enumerate(media):
            path = item.stored_path or item.source_path
            files.append((f"files[{i}]", open(path, "rb")))  # noqa: SIM115
        try:
            resp = await self._client.post(
                url,
                data={"content": content},
                files=files,
                headers=headers,
            )
        finally:
            for _, fh in files:
                fh.close()
        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> PublishResult:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        data = resp.json()
        msg_id = data.get("id")
        url = None
        if msg_id:
            guild_id = data.get("guild_id", "@me")
            channel_id = data.get("channel_id", "")
            url = f"https://discord.com/channels/{guild_id}/{channel_id}/{msg_id}"
        return PublishResult(
            success=True,
            platform_post_id=str(msg_id) if msg_id else None,
            url=url,
        )

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> PublishResult:
        status = exc.response.status_code
        body = redact_text(exc.response.text[:2000])
        if status == 429:
            retry_after = None
            with contextlib.suppress(Exception):
                retry_after = exc.response.json().get("retry_after")
            return PublishResult(
                success=False,
                error_category=ErrorCategory.RATE_LIMITED,
                retry_after_seconds=float(retry_after) if retry_after else None,
                raw_response_redacted=body,
            )
        if status == 401:
            return PublishResult(
                success=False,
                error_category=ErrorCategory.AUTH_EXPIRED,
                raw_response_redacted=body,
            )
        if status >= 500:
            return PublishResult(
                success=False,
                error_category=ErrorCategory.RETRYABLE,
                raw_response_redacted=body,
            )
        return PublishResult(
            success=False,
            error_category=ErrorCategory.PERMANENT,
            raw_response_redacted=body,
        )

    async def refresh_auth(self) -> None:
        # Discord bot tokens and webhooks don't expire via OAuth refresh.
        return None

    async def healthcheck(self) -> HealthStatus:
        try:
            if self._is_webhook:
                resp = await self._client.get(self._webhook_url)
                return HealthStatus(ok=True, detail="webhook reachable")
            resp = await self._client.get(
                "/users/@me", headers={"Authorization": f"Bot {self._bot_token}"}
            )
            resp.raise_for_status()
            return HealthStatus(ok=True, detail="bot token valid")
        except httpx.HTTPStatusError as exc:
            return HealthStatus(
                ok=False,
                detail=f"HTTP {exc.response.status_code}",
                needs_reauth=exc.response.status_code == 401,
            )
        except httpx.RequestError as exc:
            return HealthStatus(ok=False, detail=redact_text(str(exc)))

    async def aclose(self) -> None:
        await self._client.aclose()
