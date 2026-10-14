"""Bluesky adapter: AT Protocol via the atproto SDK.

Uses the official atproto library for session management, blob upload and
record creation. Handles token refresh internally.
"""

from __future__ import annotations

from typing import Any, ClassVar

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

logger = get_logger("platform.bluesky")


@register_platform
class BlueskyAdapter(Platform):
    """Bluesky AT Protocol adapter (atproto SDK)."""

    name: ClassVar[str] = "bluesky"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=300,
        max_media=4,
        allowed_mime=frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"}),
        max_bytes=1 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=True,
        supports_alt_text=True,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self._handle = account.secrets.get("handle", "")
        self._app_password = account.secrets.get("app_password", "")
        self._client: Any = None
        self._logged_in = False

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                from atproto import Client
            except ImportError as exc:
                raise PlatformError(
                    "Bluesky support requires the atproto package.",
                    fix="Install with: pip install autoposter[bluesky]",
                ) from exc
            self._client = Client()
        if not self._logged_in:
            self._client.login(self._handle, self._app_password)
            self._logged_in = True
        return self._client

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        # Bluesky counts graphemes, not code points.
        from autoposter.utils.text import grapheme_len

        if grapheme_len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=(
                        f"Text is {grapheme_len(body)} graphemes; "
                        f"Bluesky limit is {self.limits.max_chars}."
                    ),
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=(
                        f"{len(post.media)} media items; "
                        f"Bluesky limit is {self.limits.max_media}."
                    ),
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by Bluesky.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=(
                            f"File is {item.size} bytes; "
                            f"Bluesky limit is {self.limits.max_bytes}."
                        ),
                    )
                )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        try:
            client = await self._get_client()

            embed_images: list[dict[str, object]] = []
            for item in media:
                blob = await self._upload_blob(client, item)
                if blob:
                    embed_images.append(
                        {
                            "alt": item.alt_text or "",
                            "image": blob,
                        }
                    )

            record: dict[str, object] = {
                "$type": "app.bsky.feed.post",
                "text": body,
                "createdAt": _now_iso(),
            }
            if embed_images:
                record["embed"] = {
                    "$type": "app.bsky.feed.post#embed",
                    "images": embed_images,
                }

            resp = await client.com.atproto.repo.create_record(
                {
                    "repo": client.me.did,
                    "collection": "app.bsky.feed.post",
                    "record": record,
                }
            )
            return PublishResult(
                success=True,
                platform_post_id=resp.uri,
                url=_uri_to_url(resp.uri),
            )
        except PlatformError:
            raise
        except Exception as exc:
            text = redact_text(str(exc))
            category = ErrorCategory.RETRYABLE
            if "expired" in text.lower() or "unauthorized" in text.lower():
                category = ErrorCategory.AUTH_EXPIRED
            elif "rate" in text.lower():
                category = ErrorCategory.RATE_LIMITED
            return PublishResult(
                success=False,
                error_category=category,
                raw_response_redacted=text,
            )

    async def _upload_blob(self, client: Any, item: MediaItem) -> object:
        path = item.stored_path or item.source_path
        with open(path, "rb") as fh:
            data = fh.read()
        resp = await client.com.atproto.repo.upload_blob(data)
        return resp.blob

    async def refresh_auth(self) -> None:
        if self._client is not None:
            self._logged_in = False
            self._client = None

    async def healthcheck(self) -> HealthStatus:
        try:
            client = await self._get_client()
            return HealthStatus(ok=True, detail=f"logged in as {client.me.handle}")
        except Exception as exc:
            return HealthStatus(ok=False, detail=redact_text(str(exc)))

    async def aclose(self) -> None:
        self._client = None
        self._logged_in = False


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _uri_to_url(uri: str) -> str | None:
    """Convert at://did/collection/rkey to a bsky.app URL."""
    if not uri.startswith("at://"):
        return None
    parts = uri.split("/")
    if len(parts) < 5:
        return None
    did, _, _collection, rkey = parts[0], parts[1], parts[2], parts[3]
    return f"https://bsky.app/profile/{did}/post/{rkey}"
