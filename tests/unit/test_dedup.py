"""Duplicate prevention tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from autoposter.config.schema import DedupConfig
from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.dedup import DuplicateGuard, find_recent_duplicate
from autoposter.core.errors import DuplicatePostError
from autoposter.core.models import PostStatus
from autoposter.utils.timeutil import utcnow


def _account(session: Session) -> int:
    from sqlalchemy import select

    from autoposter.core.orm import AccountRow

    row = session.execute(select(AccountRow)).scalar_one_or_none()
    if row is None:
        row = AccountRow(platform="fake", label="default", encrypted_credentials="x")
        session.add(row)
        session.flush()
    return int(row.id)


def _publish_one(engine: Engine, body: str) -> int:
    with session_scope(engine) as session:
        account_id = _account(session)
        post = q.add_post(
            session,
            body=body,
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        pid = post.id
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        q.mark_target_published(session, claimed[0].target_id, platform_post_id="ok")
        return int(pid)


def test_find_recent_duplicate_hit(engine: Engine) -> None:
    pid = _publish_one(engine, "same content")
    with session_scope(engine) as session:
        post = q.get_post(session, pid)
        assert post is not None
        hit = find_recent_duplicate(
            session,
            platform="fake",
            account_id=post.targets[0].account_id,
            content_hash=post.content_hash,
            since=utcnow() - timedelta(hours=24),
        )
        assert hit is not None
        assert hit.post_id == pid


def test_find_recent_duplicate_miss_different_body(engine: Engine) -> None:
    _publish_one(engine, "content A")
    with session_scope(engine) as session:
        account_id = _account(session)
        from autoposter.core.models import compute_content_hash

        hit = find_recent_duplicate(
            session,
            platform="fake",
            account_id=account_id,
            content_hash=compute_content_hash("content B"),
            since=utcnow() - timedelta(hours=24),
        )
        assert hit is None


def test_guard_block_raises(engine: Engine) -> None:
    _publish_one(engine, "blocked content")
    guard = DuplicateGuard(DedupConfig(window_hours=24, mode="block"))
    with session_scope(engine) as session:
        account_id = _account(session)
        from autoposter.core.models import compute_content_hash

        with pytest.raises(DuplicatePostError, match="already published"):
            guard.enforce(
                session,
                platform="fake",
                account_id=account_id,
                content_hash=compute_content_hash("blocked content"),
            )


def test_guard_warn_does_not_block(engine: Engine) -> None:
    _publish_one(engine, "warn content")
    guard = DuplicateGuard(DedupConfig(window_hours=24, mode="warn"))
    with session_scope(engine) as session:
        account_id = _account(session)
        from autoposter.core.models import compute_content_hash

        verdict = guard.check(
            session,
            platform="fake",
            account_id=account_id,
            content_hash=compute_content_hash("warn content"),
        )
        assert not verdict.blocked
        assert verdict.warning is not None
        assert verdict.hit is not None


def test_guard_window_expired(engine: Engine) -> None:
    _publish_one(engine, "old content")
    # Window of 0 hours disables checking.
    guard = DuplicateGuard(DedupConfig(window_hours=0, mode="block"))
    with session_scope(engine) as session:
        account_id = _account(session)
        from autoposter.core.models import compute_content_hash

        verdict = guard.check(
            session,
            platform="fake",
            account_id=account_id,
            content_hash=compute_content_hash("old content"),
        )
        assert not verdict.blocked
        assert verdict.hit is None


def test_post_status_published_after_target(engine: Engine) -> None:
    pid = _publish_one(engine, "status check")
    with session_scope(engine) as session:
        post = q.get_post(session, pid)
        assert post is not None
        assert post.status is PostStatus.PUBLISHED
