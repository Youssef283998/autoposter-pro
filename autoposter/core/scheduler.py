"""Scheduler tick: claim due targets, dispatch, apply policies, spawn recurrences."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from autoposter.config.schema import Config
from autoposter.core import missed as missed_mod
from autoposter.core import queue as queue_mod
from autoposter.core.dispatcher import Dispatcher, DispatcherStats
from autoposter.core.models import TargetStatus, make_idempotency_key
from autoposter.core.recovery import reconcile_stuck_publishing
from autoposter.utils.logging import get_logger
from autoposter.utils.timeutil import clock_jump_detected, next_occurrence, utcnow

logger = get_logger("scheduler")

SessionFactory = Callable[[], AbstractContextManager[Session]]
AlertFn = Callable[[str, str], None]


@dataclass
class TickReport:
    """Everything one scheduler tick did."""

    now: datetime
    claimed: int = 0
    dispatched: DispatcherStats | None = None
    missed: missed_mod.MissedReport = field(default_factory=missed_mod.MissedReport)
    quiet: missed_mod.MissedReport = field(default_factory=missed_mod.MissedReport)
    recurrences_spawned: list[int] = field(default_factory=list)
    clock_jump: bool = False


class Scheduler:
    """DB-driven scheduler. APScheduler (or a plain loop) just calls ``tick``."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        config: Config,
        dispatcher: Dispatcher,
        claim_limit: int | None = None,
        alert: AlertFn | None = None,
        previous_tick: datetime | None = None,
        stuck_threshold_seconds: float = 300.0,
        recovery_lookup: Callable[..., str | None] | None = None,
    ) -> None:
        self._sessions = session_factory
        self.config = config
        self.dispatcher = dispatcher
        self.claim_limit = claim_limit or max(1, config.missed.max_catchup_posts_per_tick * 5)
        self._alert = alert
        self._previous_tick = previous_tick
        self._stuck_threshold = stuck_threshold_seconds
        self._recovery_lookup = recovery_lookup
        self._recovered = False

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def recover_on_startup(self) -> None:
        """Reconcile targets stuck in publishing from a previous crash."""
        with self._sessions() as session:
            report = reconcile_stuck_publishing(
                session,
                lookup=self._recovery_lookup,
                stuck_threshold_seconds=self._stuck_threshold,
            )
        if report.reconciled_published or report.requeued or report.failed:
            logger.info(
                "Crash recovery: %s published, %s requeued",
                len(report.reconciled_published),
                len(report.requeued),
            )
        self._recovered = True

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def tick(self, *, now: datetime | None = None) -> TickReport:
        """One scheduler pass. Safe to call concurrently? No — max_instances=1."""
        if not self._recovered:
            self.recover_on_startup()

        current = now or utcnow()
        report = TickReport(now=current)

        if self._previous_tick is not None and clock_jump_detected(
            self._previous_tick, current, timedelta(minutes=10)
        ):
            report.clock_jump = True
            logger.warning("System clock jump detected; rescanning queue")
        self._previous_tick = current

        # 1. Quiet hours hold (before claiming so held posts are not claimed).
        with self._sessions() as session:
            if self.config.quiet_hours is not None:
                report.quiet = missed_mod.hold_quiet_hours(
                    session,
                    now=current,
                    quiet_hours=self.config.quiet_hours,
                    default_timezone=self.config.default_timezone,
                )

        # 2. Missed-schedule policy.
        with self._sessions() as session:
            report.missed = missed_mod.process_missed_targets(
                session, now=current, policy=self.config.missed
            )
        for post_id in _affected_post_ids(report.missed):
            if self._alert is not None:
                self._alert(
                    "missed",
                    f"Post {post_id} missed its schedule and was marked missed.",
                )

        # 3. Claim due targets (atomic).
        claim_limit = self.claim_limit
        if report.missed.marked_missed:
            # Never burst a backlog: only claim up to the catch-up cap.
            claim_limit = min(
                claim_limit, max(1, self.config.missed.max_catchup_posts_per_tick)
            )
        with self._sessions() as session:
            claimed = queue_mod.claim_due_targets(
                session, now=current, limit=claim_limit
            )
        report.claimed = len(claimed)

        # 4. Dispatch.
        if claimed:
            report.dispatched = await self.dispatcher.dispatch_claimed(claimed)

        # 5. Spawn next recurrence instances for posts that just completed.
        with self._sessions() as session:
            report.recurrences_spawned = spawn_due_recurrences(session)

        return report

    # ------------------------------------------------------------------
    # Long-running loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Run the tick forever under APScheduler (max_instances=1)."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            self.tick,
            "interval",
            seconds=self.config.tick_seconds,
            max_instances=1,
            coalesce=True,
            id="autoposter-tick",
        )
        scheduler.start()
        logger.info("Scheduler started (tick every %ss)", self.config.tick_seconds)
        try:
            await asyncio.Event().wait()
        finally:
            scheduler.shutdown(wait=False)

    def run_forever_sync(self) -> None:
        """Blocking entry point used by ``autoposter run``."""
        asyncio.run(self.run_forever())


# ---------------------------------------------------------------------------
# Recurrence spawning
# ---------------------------------------------------------------------------


def spawn_due_recurrences(session: Session, *, limit: int = 20) -> list[int]:
    """After a recurring post completes, create its next instance.

    A post "completed" when its status is published and it has a
    ``recurrence_rule``. The next instance is scheduled at the next rule
    occurrence after the original ``scheduled_at_utc``.
    """
    from sqlalchemy import select

    from autoposter.core.models import PostStatus
    from autoposter.core.orm import MediaRow, PostRow, PostTargetRow

    rows = session.execute(
        select(PostRow)
        .where(
            PostRow.status == PostStatus.PUBLISHED.value,
            PostRow.recurrence_rule.is_not(None),
            PostRow.group_id.is_not(None),
        )
        .limit(limit * 3)
    ).scalars().all()

    spawned: list[int] = []
    for row in rows:
        # Skip if a successor already exists in this group.
        sibling = session.execute(
            select(PostRow.id).where(
                PostRow.group_id == row.group_id,
                PostRow.id != row.id,
                PostRow.scheduled_at_utc > row.scheduled_at_utc,
            )
        ).scalar_one_or_none()
        if sibling is not None:
            continue
        if len(spawned) >= limit:
            break
        next_time = next_occurrence(
            row.recurrence_rule or "daily",
            after_utc=row.scheduled_at_utc,
            tz_name=row.timezone,
        )
        if next_time is None:
            continue
        # One-off group ids mark "already expanded" by clearing the rule on
        # the child; the parent keeps its rule for history.
        child = PostRow(
            group_id=row.group_id,
            body=row.body,
            scheduled_at_utc=next_time,
            timezone=row.timezone,
            status=PostStatus.SCHEDULED.value,
            recurrence_rule=row.recurrence_rule,
            content_hash=row.content_hash,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(child)
        session.flush()
        for target in row.targets:
            session.add(
                PostTargetRow(
                    post_id=child.id,
                    platform=target.platform,
                    account_id=target.account_id,
                    variant_body=target.variant_body,
                    status=TargetStatus.SCHEDULED.value,
                    attempts=0,
                    next_attempt_at=next_time,
                    idempotency_key=make_idempotency_key(
                        child.id, target.platform, target.account_id
                    ),
                )
            )
        for media in session.execute(
            select(MediaRow).where(MediaRow.post_id == row.id)
        ).scalars().all():
            session.add(
                MediaRow(
                    post_id=child.id,
                    source_path=media.source_path,
                    stored_path=media.stored_path,
                    mime=media.mime,
                    size=media.size,
                    sha256=media.sha256,
                    alt_text=media.alt_text,
                    platform_media_ids=dict(media.platform_media_ids or {}),
                )
            )
        session.flush()
        spawned.append(child.id)
        logger.info(
            "Spawned recurrence %s for post %s at %s",
            child.id,
            row.id,
            next_time.isoformat(),
        )
    return spawned


def _affected_post_ids(report: missed_mod.MissedReport) -> set[int]:
    # Missed report tracks target ids; alerts are coarse in stage 2.
    return set(report.marked_missed)
