"""Domain model tests: hashing, idempotency keys, validators."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from autoposter.core.models import (
    ErrorCategory,
    Post,
    PostStatus,
    PostTarget,
    TargetStatus,
    compute_content_hash,
    make_idempotency_key,
)


def test_content_hash_stable_for_same_body() -> None:
    a = compute_content_hash("Hello world")
    b = compute_content_hash("Hello world")
    assert a == b
    assert len(a) == 64


def test_content_hash_ignores_whitespace_and_unicode_form() -> None:
    composed = "café  launch"  # é as single code point
    decomposed = "café   launch"  # e + combining acute
    assert compute_content_hash(composed) == compute_content_hash(decomposed)


def test_content_hash_differs_for_different_body() -> None:
    assert compute_content_hash("Hello") != compute_content_hash("Hello!")


def test_content_hash_includes_sorted_media() -> None:
    base = compute_content_hash("body", ["a" * 64, "b" * 64])
    reordered = compute_content_hash("body", ["b" * 64, "a" * 64])
    assert base == reordered
    different = compute_content_hash("body", ["c" * 64])
    assert base != different


def test_idempotency_key_format() -> None:
    assert make_idempotency_key(42, "telegram", 7) == "42:telegram:7"


def test_post_target_autofills_idempotency_key() -> None:
    target = PostTarget(post_id=1, platform="x", account_id=2)
    assert target.idempotency_key == "1:x:2"


def test_post_target_preserves_explicit_idempotency_key() -> None:
    target = PostTarget(post_id=1, platform="x", account_id=2, idempotency_key="custom")
    assert target.idempotency_key == "custom"


def test_post_requires_aware_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Post(
            body="hi",
            scheduled_at_utc=datetime(2026, 10, 14, 9, 0),  # naive
            timezone="UTC",
        )


def test_post_accepts_aware_datetime() -> None:
    post = Post(
        body="hi",
        scheduled_at_utc=datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
        timezone="Europe/Berlin",
        status=PostStatus.SCHEDULED,
    )
    assert post.status is PostStatus.SCHEDULED


def test_error_category_values() -> None:
    assert ErrorCategory.RETRYABLE.value == "retryable"
    assert ErrorCategory.AMBIGUOUS.value == "ambiguous"
    assert TargetStatus.PUBLISHING.value == "publishing"
