"""Queue operations: add, claim atomicity, state transitions."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.errors import PostValidationError
from autoposter.core.models import PostStatus, TargetStatus
from autoposter.core.orm import PostTargetRow
from autoposter.utils.timeutil import utcnow


def _ensure_account(session: Session, platform: str = "fake") -> int:
    from autoposter.core.orm import AccountRow

    account = session.execute(
        select(AccountRow).where(AccountRow.platform == platform)
    ).scalar_one_or_none()
    if account is None:
        account = AccountRow(platform=platform, label="default", encrypted_credentials="x")
        session.add(account)
        session.flush()
    return int(account.id)


def test_add_post_creates_targets(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="hello",
            scheduled_at_utc=utcnow(),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        assert post.id is not None
        assert len(post.targets) == 1
        assert post.targets[0].idempotency_key == f"{post.id}:fake:{account_id}"
        assert post.targets[0].next_attempt_at is not None


def test_add_post_requires_targets(engine: Engine) -> None:
    with session_scope(engine) as session, pytest.raises(PostValidationError, match="at least one"):
        q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow(),
            targets=[],
        )


def test_add_post_rejects_duplicate_targets(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        with pytest.raises(PostValidationError, match="Duplicate target"):
            q.add_post(
                session,
                body="x",
                scheduled_at_utc=utcnow(),
                targets=[
                    q.TargetSpec(platform="fake", account_id=account_id),
                    q.TargetSpec(platform="fake", account_id=account_id),
                ],
            )


def test_add_post_rejects_empty_body(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        with pytest.raises(PostValidationError, match="empty"):
            q.add_post(
                session,
                body="   ",
                scheduled_at_utc=utcnow(),
                targets=[q.TargetSpec(platform="fake", account_id=account_id)],
            )


def test_claim_due_targets_happy_path(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="due now",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        target_id = post.targets[0].id
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
        assert len(claimed) == 1
        assert claimed[0].target_id == target_id
        assert claimed[0].body == "due now"
    # Second claim must find nothing (already publishing).
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
        assert claimed == []


def test_claim_is_atomic_across_two_sessions(engine: Engine) -> None:
    """Simulate two workers: only one may claim a due target."""
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        for i in range(5):
            q.add_post(
                session,
                body=f"post {i}",
                scheduled_at_utc=utcnow() - timedelta(seconds=1),
                targets=[q.TargetSpec(platform="fake", account_id=account_id)],
            )

    with session_scope(engine) as s1:
        first = q.claim_due_targets(s1, now=utcnow(), limit=3)
    with session_scope(engine) as s2:
        second = q.claim_due_targets(s2, now=utcnow(), limit=3)
    ids1 = {c.target_id for c in first}
    ids2 = {c.target_id for c in second}
    assert ids1
    assert ids1.isdisjoint(ids2)
    assert len(ids1) == 3
    assert len(ids2) == 2  # remaining


def test_claim_skips_future_targets(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        q.add_post(
            session,
            body="later",
            scheduled_at_utc=utcnow() + timedelta(hours=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
    with session_scope(engine) as session:
        assert q.claim_due_targets(session, now=utcnow(), limit=10) == []


def test_variant_body_resolution(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        q.add_post(
            session,
            body="main body",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[
                q.TargetSpec(platform="fake", account_id=account_id, variant_body="variant!")
            ],
            variants={"other": "mapped"},
        )
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        assert claimed[0].body == "variant!"


def test_mark_published_updates_post_status(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="done",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        q.mark_target_published(session, claimed[0].target_id, platform_post_id="p-1")
    with session_scope(engine) as session:
        refreshed = q.get_post(session, post.id)
        assert refreshed is not None
        assert refreshed.status is PostStatus.PUBLISHED
        assert refreshed.targets[0].status is TargetStatus.PUBLISHED
        assert refreshed.targets[0].platform_post_id == "p-1"


def test_cancel_and_pause_resume(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow() + timedelta(hours=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        pid, _tid = post.id, post.targets[0].id
        assert q.set_post_status(session, pid, PostStatus.PAUSED)
    with session_scope(engine) as session:
        refreshed = q.get_post(session, pid)
        assert refreshed is not None
        assert refreshed.status is PostStatus.PAUSED
        assert refreshed.targets[0].status is TargetStatus.PAUSED
        assert q.set_post_status(session, pid, PostStatus.SCHEDULED)
        refreshed = q.get_post(session, pid)
        assert refreshed is not None
        assert refreshed.targets[0].status is TargetStatus.SCHEDULED
        assert q.set_post_status(session, pid, PostStatus.CANCELLED)


def test_reschedule(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow() + timedelta(hours=2),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        new_time = utcnow() + timedelta(minutes=5)
        updated = q.reschedule_post(session, post.id, new_time)
        assert updated.scheduled_at_utc == new_time
        assert updated.targets[0].next_attempt_at == new_time


def test_edit_blocked_after_publishing(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        q.claim_due_targets(session, now=utcnow(), limit=1)
        with pytest.raises(PostValidationError, match="no longer be edited"):
            q.update_post_body(session, post.id, body="new")


def test_retry_failed_target(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        post = q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        tid = post.targets[0].id
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        q.mark_target_failed(session, claimed[0].target_id, "boom")
        assert q.retry_target(session, tid)
        refreshed = q.get_post(session, post.id)
        assert refreshed is not None
        assert refreshed.targets[0].status is TargetStatus.SCHEDULED


def test_requeue_increments_attempts(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        q.add_post(
            session,
            body="x",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        tid = claimed[0].target_id
        assert claimed[0].attempts == 0
        q.requeue_target(session, tid, next_attempt_at=utcnow(), error="retryable: 503")
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.attempts == 1
        assert row.status == "scheduled"
        assert "503" in (row.last_error or "")


def test_list_posts_filters(engine: Engine) -> None:
    with session_scope(engine) as session:
        account_id = _ensure_account(session)
        q.add_post(
            session,
            body="a",
            scheduled_at_utc=utcnow(),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        q.add_post(
            session,
            body="b",
            scheduled_at_utc=utcnow() + timedelta(hours=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
    with session_scope(engine) as session:
        all_posts = q.list_posts(session)
        assert len(all_posts) == 2
        only_fake = q.list_posts(session, platform="fake")
        assert len(only_fake) == 2
        none_other = q.list_posts(session, platform="telegram")
        assert none_other == []
