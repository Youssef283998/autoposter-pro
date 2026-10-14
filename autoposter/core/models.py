"""Shared domain models and value objects.

Layering rule: ``core`` never imports ``platforms``. Adapter-facing value
objects (``PlatformLimits``, ``PublishResult``, ``ErrorCategory``...) live
here so the dispatcher, retry engine and adapters can all depend on core.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoposter.utils.text import normalize_for_hash

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PostStatus(str, Enum):
    """Lifecycle of a post (the content unit)."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSED = "missed"
    PAUSED = "paused"


class TargetStatus(str, Enum):
    """Lifecycle of one platform destination of a post."""

    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MISSED = "missed"
    PAUSED = "paused"


class AccountStatus(str, Enum):
    """Health of a stored account credential."""

    ACTIVE = "active"
    NEEDS_REAUTH = "needs_reauth"
    DISABLED = "disabled"


class ErrorCategory(str, Enum):
    """How the dispatcher should treat a publish failure."""

    RETRYABLE = "retryable"
    RATE_LIMITED = "rate_limited"
    AUTH_EXPIRED = "auth_expired"
    PERMANENT = "permanent"
    # Timeout after the request was sent: never blind-retry; reconcile first.
    AMBIGUOUS = "ambiguous"


# ---------------------------------------------------------------------------
# Adapter-facing value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationIssue:
    """A pre-flight validation problem on one field of a post."""

    field: str
    message: str
    blocking: bool = True

    def __str__(self) -> str:
        level = "error" if self.blocking else "warning"
        return f"[{level}] {self.field}: {self.message}"


@dataclass(frozen=True)
class PlatformLimits:
    """Static capability limits for one platform."""

    max_chars: int
    max_media: int
    allowed_mime: frozenset[str]
    max_bytes: int
    max_video_seconds: int | None = None
    counts_links_as_fixed_length: int | None = None
    supports_threads: bool = True
    supports_alt_text: bool = False


@dataclass(frozen=True)
class PublishResult:
    """Outcome of one publish attempt against one platform."""

    success: bool
    platform_post_id: str | None = None
    url: str | None = None
    error_category: ErrorCategory | None = None
    retry_after_seconds: float | None = None
    raw_response_redacted: dict[str, Any] | str | None = None
    thread_parts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HealthStatus:
    """Result of an account/platform healthcheck."""

    ok: bool
    detail: str
    needs_reauth: bool = False


# ---------------------------------------------------------------------------
# Persisted entities (pydantic views over ORM rows)
# ---------------------------------------------------------------------------


class MediaItem(BaseModel):
    """One media attachment, already copied into the internal store."""

    model_config = ConfigDict(frozen=True)

    source_path: Path
    stored_path: Path | None = None
    mime: str
    size: int
    sha256: str
    alt_text: str | None = None
    platform_media_ids: dict[str, str] = Field(default_factory=dict)


class PostTarget(BaseModel):
    """One (post, platform, account) destination."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    post_id: int
    platform: str
    account_id: int
    variant_body: str | None = None
    status: TargetStatus = TargetStatus.SCHEDULED
    attempts: int = 0
    next_attempt_at: datetime | None = None
    claimed_at: datetime | None = None
    platform_post_id: str | None = None
    last_error: str | None = None
    idempotency_key: str = ""

    @model_validator(mode="after")
    def _fill_idempotency_key(self) -> PostTarget:
        if not self.idempotency_key:
            object.__setattr__(
                self,
                "idempotency_key",
                make_idempotency_key(self.post_id, self.platform, self.account_id),
            )
        return self


class Post(BaseModel):
    """A scheduled piece of content and its destinations."""

    model_config = ConfigDict(frozen=True)

    body: str
    scheduled_at_utc: datetime
    timezone: str
    status: PostStatus = PostStatus.SCHEDULED
    id: int | None = None
    group_id: str | None = None
    recurrence_rule: str | None = None
    content_hash: str = ""
    media: list[MediaItem] = Field(default_factory=list)
    targets: list[PostTarget] = Field(default_factory=list)
    variants: dict[str, str] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("scheduled_at_utc")
    @classmethod
    def _must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("scheduled_at_utc must be a timezone-aware datetime (UTC)")
        return value


class AccountCredentials(BaseModel):
    """Decrypted view of a stored account. Never log ``secrets``."""

    model_config = ConfigDict(frozen=True)

    id: int
    platform: str
    label: str
    kind: str
    secrets: dict[str, str]
    token_expires_at: datetime | None = None
    status: AccountStatus = AccountStatus.ACTIVE


class AttemptRecord(BaseModel):
    """One row of the per-target audit trail."""

    model_config = ConfigDict(frozen=True)

    target_id: int
    started_at: datetime
    finished_at: datetime | None = None
    http_status: int | None = None
    error_category: ErrorCategory | None = None
    redacted_response: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_idempotency_key(post_id: int, platform: str, account_id: int) -> str:
    """Stable per-target key used to prevent double publishes on retries."""
    return f"{post_id}:{platform}:{account_id}"


def compute_content_hash(body: str, media_sha256s: Sequence[str] = ()) -> str:
    """Deterministic content hash used for duplicate detection.

    The body is NFC-normalized and whitespace-collapsed before hashing so
    cosmetic differences do not defeat dedup. Media hashes are sorted so
    attachment order does not change the digest.
    """
    digest = hashlib.sha256()
    digest.update(normalize_for_hash(body).encode("utf-8"))
    for media_hash in sorted(media_sha256s):
        digest.update(b"\x00")
        digest.update(media_hash.encode("ascii"))
    return digest.hexdigest()
