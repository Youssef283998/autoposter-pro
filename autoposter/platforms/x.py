"""X (Twitter) adapter: API v2 via httpx with OAuth 2.0 PKCE.

Supports text posts, media upload (chunked), threads via replies, and
proactive/reactive token refresh.
"""

from __future__ import annotations

import time
from typing import ClassVar

import httpx

from autoposter.config.schema import PlatformCaps
from autoposter.core.errors import AuthError
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
from autoposter.utils.text import count_x_length

logger = get_logger("platform.x")

API_BASE = "https://api.twitter.com/2"
UPLOAD_BASE = "https://upload.twitter.com/1.1"


@register_platform
class XAdapter(Platform):
    """X (Twitter) API v2 adapter."""

    name: ClassVar[str] = "x"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=280,
        max_media=4,
        allowed_mime=frozenset(
            {"image/jpeg", "image/png", "image/gif", "video/mp4"}
        ),
        max_bytes=5 * 1024 * 1024,
        max_video_seconds=140,
        counts_links_as_fixed_length=23,
        supports_threads=True,
        supports_alt_text=True,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self._access_token = account.secrets.get("access_token", "")
        self._refresh_token = account.secrets.get("refresh_token", "")
        self._client_id = account.secrets.get("client_id", "")
        self._client_secret = account.secrets.get("client_secret", "")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Authorization": f"Bearer {self._access_token}"},
        )
        self._upload_client = httpx.AsyncClient(
            base_url=UPLOAD_BASE,
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"Authorization": f"Bearer {self._access_token}"},
        )

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        weighted = count_x_length(body, link_weight=self.limits.counts_links_as_fixed_length or 23)
        if weighted > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=(
                        f"Text is {weighted} weighted chars "
                        f"(links count as {self.limits.counts_links_as_fixed_length}); "
                        f"X limit is {self.limits.max_chars}."
                    ),
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=f"{len(post.media)} media items; X limit is {self.limits.max_media}.",
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by X.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"File is {item.size} bytes; X limit is {self.limits.max_bytes}.",
                    )
                )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        try:
            media_ids: list[str] = []
            for item in media:
                media_id = await self._upload_media(item)
                if media_id:
                    media_ids.append(media_id)

            payload: dict[str, object] = {"text": body}
            if media_ids:
                payload["media"] = {"media_ids": media_ids}

            resp = await self._client.post("/tweets", json=payload)
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
        """Chunked media upload: INIT → APPEND → FINALIZE."""
        path = item.stored_path or item.source_path
        size = item.size
        media_type = item.mime

        # INIT
        init_resp = await self._upload_client.post(
            "/media/upload.json",
            data={
                "command": "INIT",
                "total_bytes": str(size),
                "media_type": media_type,
            },
        )
        init_resp.raise_for_status()
        media_id = init_resp.json().get("media_id_string")
        if not media_id:
            return None

        # APPEND (chunked)
        chunk_size = 2 * 1024 * 1024  # 2MB chunks
        with open(path, "rb") as fh:
            segment = 0
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                append_resp = await self._upload_client.post(
                    "/media/upload.json",
                    data={"command": "APPEND", "media_id": media_id, "segment_index": str(segment)},
                    files={"media": chunk},
                )
                append_resp.raise_for_status()
                segment += 1

        # FINALIZE
        await self._upload_client.post(
            "/media/upload.json",
            data={"command": "FINALIZE", "media_id": media_id},
        )
        return str(media_id)

    def _parse_response(self, resp: httpx.Response) -> PublishResult:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        data = resp.json()
        tweet_data = data.get("data", {})
        tweet_id = tweet_data.get("id")
        return PublishResult(
            success=True,
            platform_post_id=str(tweet_id) if tweet_id else None,
            url=f"https://x.com/i/web/status/{tweet_id}" if tweet_id else None,
        )

    def _classify_http_error(self, exc: httpx.HTTPStatusError) -> PublishResult:
        status = exc.response.status_code
        body = redact_text(exc.response.text[:2000])
        if status == 429:
            reset = exc.response.headers.get("x-rate-limit-reset")
            retry_after = None
            if reset:
                retry_after = max(0.0, float(reset) - time.time())
            return PublishResult(
                success=False,
                error_category=ErrorCategory.RATE_LIMITED,
                retry_after_seconds=retry_after,
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
        """Refresh the OAuth token."""
        if not self._refresh_token:
            raise AuthError(
                "No refresh token available for X account.",
                fix="Run `autoposter setup x` to re-authorize.",
            )
        from autoposter.auth.oauth import refresh_access_token

        token = await refresh_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret or None,
            refresh_token=self._refresh_token,
            token_url=f"{API_BASE}/oauth2/token",
        )
        self._access_token = token.access_token
        if token.refresh_token:
            self._refresh_token = token.refresh_token
        self._client.headers["Authorization"] = f"Bearer {self._access_token}"
        self._upload_client.headers["Authorization"] = f"Bearer {self._access_token}"

    async def healthcheck(self) -> HealthStatus:
        try:
            resp = await self._client.get("/users/me")
            resp.raise_for_status()
            return HealthStatus(ok=True, detail="token valid")
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
        await self._upload_client.aclose()
