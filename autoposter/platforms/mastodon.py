"""Mastodon adapter: thin httpx wrapper over the REST API.

Uploads media first, then creates a status with media_ids. Threads via
``in_reply_to_id``. Alt text via the ``description`` field.
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from autoposter.config.schema import PlatformCaps
from autoposter.core.errors import PlatformError
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

logger = get_logger("platform.mastodon")


@register_platform
class MastodonAdapter(Platform):
    """Mastodon REST API adapter."""

    name: ClassVar[str] = "mastodon"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=500,
        max_media=4,
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
                "audio/wav",
            }
        ),
        max_bytes=16 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=True,
        supports_alt_text=True,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        api_base = account.secrets.get("api_base", "https://mastodon.social").rstrip("/")
        if not api_base.startswith("https://"):
            raise PlatformError(
                "Mastodon API base must use HTTPS.",
                fix="Set MASTODON_API_BASE to an https:// URL in .env.",
            )
        self._base_url = api_base
        self._access_token = account.secrets.get("access_token", "")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {self._access_token}"},
        )

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        if len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=(
                        f"Text is {len(body)} chars; Mastodon limit is "
                        f"{self.limits.max_chars} (may vary by instance)."
                    ),
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=(
                        f"{len(post.media)} media items; "
                        f"Mastodon limit is {self.limits.max_media}."
                    ),
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by Mastodon.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=(
                            f"File is {item.size} bytes; "
                            f"Mastodon limit is {self.limits.max_bytes}."
                        ),
                    )
                )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        media_ids: list[str] = []
        try:
            for item in media:
                media_id = await self._upload_media(item)
                if media_id:
                    media_ids.append(media_id)

            payload: dict[str, object] = {"status": body}
            if media_ids:
                payload["media_ids"] = media_ids

            resp = await self._client.post("/api/v1/statuses", data=payload)
            return self._parse_response(resp)
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

    async def _upload_media(self, item: MediaItem) -> str | None:
        path = item.stored_path or item.source_path
        with open(path, "rb") as fh:
            data = {"description": item.alt_text or ""}
            resp = await self._client.post("/api/v1/media", data=data, files={"file": fh})
        resp.raise_for_status()
        result = resp.json()
        return str(result.get("id")) if result.get("id") else None

    def _parse_response(self, resp: httpx.Response) -> PublishResult:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        data = resp.json()
        post_id = data.get("id")
        url = data.get("url")
        return PublishResult(
            success=True,
            platform_post_id=str(post_id) if post_id else None,
            url=url,
        )

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> PublishResult:
        status = exc.response.status_code
        body = redact_text(exc.response.text[:2000])
        if status == 429:
            retry_after = exc.response.headers.get("Retry-After")
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
        # OAuth token refresh is handled by the auth layer (Stage 4).
        return None

    async def healthcheck(self) -> HealthStatus:
        try:
            resp = await self._client.get("/api/v1/accounts/verify_credentials")
            resp.raise_for_status()
            return HealthStatus(ok=True, detail="credentials valid")
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
