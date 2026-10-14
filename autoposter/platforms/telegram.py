"""Telegram adapter: Bot API via thin httpx wrapper.

Supports text, single media, and media groups. Threads are not natively
supported by the Bot API for regular chats, so ``supports_threads=False``.
"""

from __future__ import annotations

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

logger = get_logger("platform.telegram")

API_BASE = "https://api.telegram.org"


@register_platform
class TelegramAdapter(Platform):
    """Telegram Bot API adapter."""

    name: ClassVar[str] = "telegram"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=4096,
        max_media=10,
        allowed_mime=frozenset(
            {"image/jpeg", "image/png", "image/gif", "video/mp4", "video/webm"}
        ),
        max_bytes=50 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=False,
        supports_alt_text=False,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self._token = account.secrets.get("bot_token", "")
        self._chat_id = account.secrets.get("chat_id", "")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bot {self._token}"},
        )

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        if len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=(
                        f"Text is {len(body)} chars; "
                        f"Telegram limit is {self.limits.max_chars}."
                    ),
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=(
                        f"{len(post.media)} media items; "
                        f"Telegram limit is {self.limits.max_media}."
                    ),
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by Telegram.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=(
                            f"File is {item.size} bytes; "
                            f"Telegram limit is {self.limits.max_bytes}."
                        ),
                    )
                )
        if not self._chat_id:
            issues.append(
                ValidationIssue(
                    field="account",
                    message="No chat_id configured for this Telegram account.",
                    blocking=True,
                )
            )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        try:
            if not media:
                return await self._send_message(body)
            if len(media) == 1:
                return await self._send_single_media(body, media[0])
            return await self._send_media_group(body, media)
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

    async def _send_message(self, text: str) -> PublishResult:
        resp = await self._client.post(
            "/sendMessage",
            json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"},
        )
        return self._parse_response(resp)

    async def _send_single_media(
        self, caption: str, item: MediaItem
    ) -> PublishResult:
        path = item.stored_path or item.source_path
        endpoint = "/sendPhoto" if item.mime.startswith("image/") else "/sendVideo"
        field = "photo" if item.mime.startswith("image/") else "video"
        with open(path, "rb") as fh:
            resp = await self._client.post(
                endpoint,
                data={"chat_id": self._chat_id, "caption": caption, "parse_mode": "HTML"},
                files={field: fh},
            )
        return self._parse_response(resp)

    async def _send_media_group(
        self, caption: str, media: list[MediaItem]
    ) -> PublishResult:
        # Telegram media groups: first item gets the caption.
        files: dict[str, Any] = {}
        media_json: list[dict[str, object]] = []
        for i, item in enumerate(media):
            path = item.stored_path or item.source_path
            field = f"file_{i}"
            files[field] = open(path, "rb")  # noqa: SIM115
            media_json.append(
                {
                    "type": "photo" if item.mime.startswith("image/") else "video",
                    "media": f"attach://{field}",
                    "caption": caption if i == 0 else "",
                }
            )
        try:
            resp = await self._client.post(
                "/sendMediaGroup",
                data={"chat_id": self._chat_id, "media": json.dumps(media_json)},
                files=files,
            )
        finally:
            for fh in files.values():
                fh.close()
        return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> PublishResult:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        data = resp.json()
        if not data.get("ok"):
            return PublishResult(
                success=False,
                error_category=ErrorCategory.PERMANENT,
                raw_response_redacted=redact_text(json.dumps(data)),
            )
        result = data.get("result", {})
        msg_id = result.get("message_id") if isinstance(result, dict) else None
        return PublishResult(
            success=True,
            platform_post_id=str(msg_id) if msg_id else None,
            url=None,
        )

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> PublishResult:
        status = exc.response.status_code
        body = redact_text(exc.response.text[:2000])
        if status == 429:
            retry_after = None
            try:
                data = exc.response.json()
                retry_after = data.get("parameters", {}).get("retry_after")
            except Exception:
                pass
            try:
                retry_after_seconds = float(retry_after) if retry_after else None
            except (ValueError, TypeError):
                retry_after_seconds = None
            return PublishResult(
                success=False,
                error_category=ErrorCategory.RATE_LIMITED,
                retry_after_seconds=retry_after_seconds,
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
        # Bot tokens don't expire; nothing to refresh.
        return None

    async def healthcheck(self) -> HealthStatus:
        try:
            resp = await self._client.get("/getMe")
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
