"""End-to-end scheduler tick tests with a fake adapter."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from autoposter.config.schema import Config, MissedPolicy, PlatformCaps
from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.dispatcher import Dispatcher
from autoposter.core.models import HealthStatus, Post, PostStatus, PostTarget, PublishResult
from autoposter.core.orm import AccountRow, PostRow, PostTargetRow
from autoposter.core.retry import RetryPolicy
from autoposter.core.scheduler import Scheduler, spawn_due_recurrences
from autoposter.utils.timeutil import utcnow


class ScriptedAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        self.published.append(idempotency_key)
        return PublishResult(success=True, platform_post_id=f"id-{idempotency_key}")

    async def refresh_auth(self) -> None:
        return None

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="ok")


def _account(engine: Engine) -> int:
    with session_scope(engine) as session:
        account = AccountRow(platform="fake", label="default", encrypted_credentials="x")
        session.add(account)
        session.flush()
        return int(account.id)


def _config() -> Config:
    return Config(
        tick_seconds=1,
        missed=MissedPolicy(post_late_within_minutes=30, max_catchup_posts_per_tick=2),
        platforms={
            "fake": PlatformCaps(hourly=None, daily=None, delay_between_posts_seconds=(0.0, 0.0))
        },
    )


def _scheduler(engine: Engine, adapter: ScriptedAdapter, **kwargs: Any) -> Scheduler:
    from contextlib import contextmanager

    from autoposter.core.db import session_scope

    @contextmanager
    def _sessions():
        with session_scope(engine) as session:
            yield session

    dispatcher = Dispatcher(
        adapter_factory=lambda p, a: adapter,
        session_factory=_sessions,
        retry_policy=RetryPolicy(jitter=0.0),
    )
    return Scheduler(
        session_factory=_sessions,
        config=_config(),
        dispatcher=dispatcher,
        **kwargs,
    )


async def test_tick_publishes_due_post(engine: Engine) -> None:
    account_id = _account(engine)
    adapter = ScriptedAdapter()
    with session_scope(engine) as session:
        q.add_post(
            session,
            body="due",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
    scheduler = _scheduler(engine, adapter)
    report = await scheduler.tick()
    assert report.claimed == 1
    assert report.dispatched is not None
    assert report.dispatched.published == 1
    assert adapter.published
    with session_scope(engine) as session:
        post = session.execute(select(PostRow)).scalars().one()
        assert post.status == PostStatus.PUBLISHED.value


async def test_tick_skips_future_posts(engine: Engine) -> None:
    account_id = _account(engine)
    adapter = ScriptedAdapter()
    with session_scope(engine) as session:
        q.add_post(
            session,
            body="future",
            scheduled_at_utc=utcnow() + timedelta(hours=3),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
    scheduler = _scheduler(engine, adapter)
    report = await scheduler.tick()
    assert report.claimed == 0
    assert adapter.published == []


async def test_tick_marks_missed_beyond_grace(engine: Engine) -> None:
    account_id = _account(engine)
    adapter = ScriptedAdapter()
    with session_scope(engine) as session:
        post = q.add_post(
            session,
            body="ancient",
            scheduled_at_utc=utcnow() - timedelta(hours=2),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        pid = post.id
    scheduler = _scheduler(engine, adapter)
    report = await scheduler.tick()
    assert report.missed.marked_missed
    assert adapter.published == []
    with session_scope(engine) as session:
        refreshed = q.get_post(session, pid)
        assert refreshed is not None
        assert refreshed.status is PostStatus.MISSED


async def test_tick_recovers_stuck_on_first_run(engine: Engine) -> None:
    account_id = _account(engine)
    adapter = ScriptedAdapter()
    with session_scope(engine) as session:
        post = q.add_post(
            session,
            body="stuck",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        row = session.get(PostTargetRow, claimed[0].target_id)
        assert row is not None
        row.claimed_at = utcnow() - timedelta(seconds=600)
    scheduler = _scheduler(
        engine, adapter, stuck_threshold_seconds=300, recovery_lookup=lambda *a: "found-id"
    )
    report = await scheduler.tick()
    # Recovery marks it published; nothing left to claim.
    assert report.claimed == 0
    assert adapter.published == []
    with session_scope(engine) as session:
        refreshed = q.get_post(session, post.id)
        assert refreshed is not None
        assert refreshed.status is PostStatus.PUBLISHED
        assert refreshed.targets[0].platform_post_id == "found-id"


async def test_spawn_recurrence_creates_next_instance(engine: Engine) -> None:
    account_id = _account(engine)
    base = utcnow().replace(hour=9, minute=0, second=0, microsecond=0)
    with session_scope(engine) as session:
        post = q.add_post(
            session,
            body="daily promo",
            scheduled_at_utc=base,
            timezone="UTC",
            recurrence_rule="daily",
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        pid = post.id
        # Fake "already published" state.
        row = session.get(PostRow, pid)
        assert row is not None
        row.status = PostStatus.PUBLISHED.value
        row.group_id = f"recurrence-{pid}"
        for target in row.targets:
            target.status = "published"
        session.flush()
    with session_scope(engine) as session:
        spawned = spawn_due_recurrences(session)
        assert len(spawned) == 1
        child = q.get_post(session, spawned[0])
        assert child is not None
        assert child.body == "daily promo"
        assert child.recurrence_rule == "daily"
        assert child.scheduled_at_utc == base + timedelta(days=1)
        assert child.status is PostStatus.SCHEDULED
        assert len(child.targets) == 1
        # Second call must not duplicate.
        assert spawn_due_recurrences(session) == []


async def test_tick_never_bursts_backlog(engine: Engine) -> None:
    """A huge overdue backlog publishes at most max_catchup per tick."""
    account_id = _account(engine)
    adapter = ScriptedAdapter()
    with session_scope(engine) as session:
        for i in range(10):
            q.add_post(
                session,
                body=f"backlog {i}",
                scheduled_at_utc=utcnow() - timedelta(hours=3),
                targets=[q.TargetSpec(platform="fake", account_id=account_id)],
            )
    scheduler = _scheduler(engine, adapter)
    report = await scheduler.tick()
    # All 10 are beyond the 30m grace -> marked missed, none published.
    assert len(report.missed.marked_missed) == 10
    assert report.claimed == 0
    assert adapter.published == []
