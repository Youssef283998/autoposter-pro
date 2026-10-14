"""LinkedIn adapter: API v2 via httpx with OAuth 2.0.

Supports personal profile and company page posts, media upload (register →
upload → attach), and proactive/reactive token refresh.
"""

from __future__ import annotations

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

logger = get_logger("platform.linkedin")

API_BASE = "https://api.linkedin.com/v2"


@register_platform
class LinkedInAdapter(Platform):
    """LinkedIn API v2 adapter."""

    name: ClassVar[str] = "linkedin"
    limits: ClassVar[PlatformLimits] = PlatformLimits(
        max_chars=3000,
        max_media=9,
        allowed_mime=frozenset(
            {"image/jpeg", "image/png", "image/gif", "video/mp4"}
        ),
        max_bytes=20 * 1024 * 1024,
        max_video_seconds=None,
        counts_links_as_fixed_length=None,
        supports_threads=False,
        supports_alt_text=True,
    )

    def __init__(self, account: AccountCredentials, settings: PlatformCaps) -> None:
        super().__init__(account, settings)
        self._access_token = account.secrets.get("access_token", "")
        self._refresh_token = account.secrets.get("refresh_token", "")
        self._client_id = account.secrets.get("client_id", "")
        self._client_secret = account.secrets.get("client_secret", "")
        self._person_urn = account.secrets.get("person_urn", "")
        self._org_urn = account.secrets.get("org_urn", "")
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )

    @property
    def _author_urn(self) -> str:
        """Person or organization URN."""
        return self._org_urn or self._person_urn

    async def validate(self, post: Post, target: PostTarget) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        body = target.variant_body or post.variants.get(target.platform, post.body)
        if len(body) > self.limits.max_chars:
            issues.append(
                ValidationIssue(
                    field="body",
                    message=(
                        f"Text is {len(body)} chars; "
                        f"LinkedIn limit is {self.limits.max_chars}."
                    ),
                )
            )
        if len(post.media) > self.limits.max_media:
            issues.append(
                ValidationIssue(
                    field="media",
                    message=(
                        f"{len(post.media)} media items; "
                        f"LinkedIn limit is {self.limits.max_media}."
                    ),
                )
            )
        for i, item in enumerate(post.media):
            if item.mime not in self.limits.allowed_mime:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=f"MIME {item.mime} not supported by LinkedIn.",
                    )
                )
            if item.size > self.limits.max_bytes:
                issues.append(
                    ValidationIssue(
                        field=f"media[{i}]",
                        message=(
                            f"File is {item.size} bytes; "
                            f"LinkedIn limit is {self.limits.max_bytes}."
                        ),
                    )
                )
        if not self._author_urn:
            issues.append(
                ValidationIssue(
                    field="account",
                    message="No person_urn or org_urn configured for this LinkedIn account.",
                )
            )
        return issues

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        media = post.media

        try:
            media_urns: list[str] = []
            for item in media:
                urn = await self._upload_media(item)
                if urn:
                    media_urns.append(urn)

            share_media_category = "NONE"
            if media_urns:
                uploaded_items = [m for m, uid in zip(media, media_urns, strict=True) if uid]
                if uploaded_items and all(
                    m.mime.startswith("image/") for m in uploaded_items
                ):
                    share_media_category = "IMAGE"
                else:
                    share_media_category = "VIDEO"

            share_content: dict[str, object] = {
                "shareCommentary": {"text": body},
                "shareMediaCategory": share_media_category,
            }
            if media_urns:
                share_content["media"] = [
                    {"status": "READY", "media": urn} for urn in media_urns
                ]

            payload: dict[str, object] = {
                "author": self._author_urn,
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": share_content,
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            }

            resp = await self._client.post("/ugcPosts", json=payload)
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
        """Register upload → upload asset → return URN."""
        path = item.stored_path or item.source_path

        # Register upload
        register_payload: dict[str, object] = {
            "registerUploadRequest": {
                "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                "owner": self._author_urn,
                "serviceRelationships": [
                    {
                        "relationshipType": "OWNER",
                        "identifier": "urn:li:userGeneratedContent",
                    }
                ],
            }
        }
        reg_resp = await self._client.post(
            "/assets?action=registerUpload", json=register_payload
        )
        reg_resp.raise_for_status()
        reg_data = reg_resp.json()
        upload_url = (
            reg_data.get("value", {})
            .get("uploadMechanism", {})
            .get("com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {})
            .get("uploadUrl")
        )
        asset_urn = reg_data.get("value", {}).get("asset")
        if not upload_url or not asset_urn:
            return None

        # Upload asset
        with open(path, "rb") as fh:
            upload_resp = await self._client.put(upload_url, content=fh.read())
        upload_resp.raise_for_status()

        return str(asset_urn)

    def _parse_response(self, resp: httpx.Response) -> PublishResult:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_http_error(exc)
        # LinkedIn returns 201 with empty body or an ID header.
        post_id = resp.headers.get("x-restli-id", "")
        return PublishResult(
            success=True,
            platform_post_id=post_id or None,
            url=f"https://www.linkedin.com/feed/update/{post_id}" if post_id else None,
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
        """Refresh the OAuth token."""
        if not self._refresh_token:
            raise AuthError(
                "No refresh token available for LinkedIn account.",
                fix="Run `autoposter setup linkedin` to re-authorize.",
            )
        from autoposter.auth.oauth import refresh_access_token

        token = await refresh_access_token(
            client_id=self._client_id,
            client_secret=self._client_secret or None,
            refresh_token=self._refresh_token,
            token_url="https://www.linkedin.com/oauth/v2/accessToken",
        )
        self._access_token = token.access_token
        if token.refresh_token:
            self._refresh_token = token.refresh_token
        self._client.headers["Authorization"] = f"Bearer {self._access_token}"

    async def healthcheck(self) -> HealthStatus:
        try:
            resp = await self._client.get("/me")
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
