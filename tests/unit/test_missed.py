"""Missed-schedule policy and quiet-hours hold tests."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from autoposter.config.schema import MissedPolicy, QuietHours
from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.missed import hold_quiet_hours, is_too_late, process_missed_targets
from autoposter.core.models import TargetStatus
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


def _schedule(session: Session, *, due_seconds_ago: int, timezone: str = "UTC") -> tuple[int, int]:
    account_id = _account(session)
    post = q.add_post(
        session,
        body="x",
        scheduled_at_utc=utcnow() - timedelta(seconds=due_seconds_ago),
        timezone=timezone,
        targets=[q.TargetSpec(platform="fake", account_id=account_id)],
    )
    return int(post.id), int(post.targets[0].id)


def test_is_too_late() -> None:
    policy = MissedPolicy(post_late_within_minutes=30)
    now = utcnow()
    assert not is_too_late(now - timedelta(minutes=10), now, policy)
    assert is_too_late(now - timedelta(minutes=45), now, policy)


def test_process_missed_marks_hopeless(engine: Engine) -> None:
    with session_scope(engine) as session:
        _, tid = _schedule(session, due_seconds_ago=3600)  # 1h late, grace 30m
    with session_scope(engine) as session:
        report = process_missed_targets(
            session, now=utcnow(), policy=MissedPolicy(post_late_within_minutes=30)
        )
        assert tid in report.marked_missed
    with session_scope(engine) as session:

        from autoposter.core.orm import PostTargetRow

        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == TargetStatus.MISSED.value
        assert "Missed schedule" in (row.last_error or "")


def test_process_missed_keeps_within_grace(engine: Engine) -> None:
    with session_scope(engine) as session:
        _, tid = _schedule(session, due_seconds_ago=600)  # 10m late, grace 30m
    with session_scope(engine) as session:
        report = process_missed_targets(
            session, now=utcnow(), policy=MissedPolicy(post_late_within_minutes=30)
        )
        assert tid in report.left_scheduled
        assert tid not in report.marked_missed


def test_quiet_hours_hold_pushes_to_window_end(engine: Engine) -> None:
    # Build a due post; quiet hours 00:00-23:59 UTC means "always quiet".
    with session_scope(engine) as session:
        _, tid = _schedule(session, due_seconds_ago=1)
    with session_scope(engine) as session:
        report = hold_quiet_hours(
            session,
            now=utcnow(),
            quiet_hours=QuietHours(start="00:00", end="23:59"),
            default_timezone="UTC",
        )
        assert tid in report.held_for_quiet_hours
    with session_scope(engine) as session:

        from autoposter.core.orm import PostTargetRow

        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == TargetStatus.SCHEDULED.value
        assert row.next_attempt_at is not None
        assert row.next_attempt_at > utcnow()


def test_quiet_hours_none_is_noop(engine: Engine) -> None:
    with session_scope(engine) as session:
        report = hold_quiet_hours(
            session, now=utcnow(), quiet_hours=None, default_timezone="UTC"
        )
        assert report.held_for_quiet_hours == []
