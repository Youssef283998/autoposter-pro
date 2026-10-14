"""Missed-schedule policy and quiet-hours hold."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from autoposter.config.schema import MissedPolicy, QuietHours
from autoposter.core.models import PostStatus, TargetStatus
from autoposter.core.orm import PostRow, PostTargetRow
from autoposter.utils.timeutil import is_within_quiet_hours, utcnow


@dataclass
class MissedReport:
    """What the missed-policy pass did this tick."""

    marked_missed: list[int] = field(default_factory=list)
    left_scheduled: list[int] = field(default_factory=list)
    held_for_quiet_hours: list[int] = field(default_factory=list)


def is_too_late(scheduled_at: datetime, now: datetime, policy: MissedPolicy) -> bool:
    """True when a due target is outside the late-grace window."""
    lateness = (now - scheduled_at).total_seconds() / 60.0
    return lateness > policy.post_late_within_minutes


def process_missed_targets(
    session: Session,
    *,
    now: datetime,
    policy: MissedPolicy,
    limit: int = 100,
) -> MissedReport:
    """Mark hopelessly overdue scheduled targets as missed.

    Targets still inside the grace window are left scheduled so the normal
    claim path publishes them (late, but within policy). Bursting a backlog
    is prevented by the claim ``limit`` per tick plus
    ``max_catchup_posts_per_tick`` in the scheduler.
    """
    report = MissedReport()
    rows = session.execute(
        select(PostTargetRow)
        .where(
            PostTargetRow.status == TargetStatus.SCHEDULED.value,
            PostTargetRow.next_attempt_at.is_not(None),
            PostTargetRow.next_attempt_at <= now,
        )
        .options(selectinload(PostTargetRow.post))
        .limit(limit)
    ).scalars().all()
    for row in rows:
        scheduled = row.next_attempt_at or row.post.scheduled_at_utc
        if is_too_late(scheduled, now, policy):
            row.status = TargetStatus.MISSED.value
            row.last_error = (
                f"Missed schedule: was due {scheduled.isoformat()} "
                f"(grace window {policy.post_late_within_minutes}m)"
            )
            row.next_attempt_at = None
            row.claimed_at = None
            report.marked_missed.append(row.id)
            _refresh_post(session, row.post_id)
        else:
            report.left_scheduled.append(row.id)
    session.flush()
    return report


def hold_quiet_hours(
    session: Session,
    *,
    now: datetime,
    quiet_hours: QuietHours | None,
    default_timezone: str,
    limit: int = 200,
) -> MissedReport:
    """Push due targets whose local time is inside quiet hours to window end.

    Returns targets that were held. Already-publishing targets are untouched.
    """
    report = MissedReport()
    if quiet_hours is None:
        return report
    rows = session.execute(
        select(PostTargetRow)
        .where(
            PostTargetRow.status == TargetStatus.SCHEDULED.value,
            PostTargetRow.next_attempt_at.is_not(None),
            PostTargetRow.next_attempt_at <= now,
        )
        .options(selectinload(PostTargetRow.post))
        .limit(limit)
    ).scalars().all()
    for row in rows:
        tz_name = row.post.timezone or default_timezone
        scheduled = row.next_attempt_at or row.post.scheduled_at_utc
        if is_within_quiet_hours(
            scheduled, tz_name, quiet_hours.start, quiet_hours.end
        ) or is_within_quiet_hours(now, tz_name, quiet_hours.start, quiet_hours.end):
            resume_at = _next_quiet_end(now, tz_name, quiet_hours)
            row.next_attempt_at = resume_at
            report.held_for_quiet_hours.append(row.id)
    session.flush()
    return report


def _next_quiet_end(now: datetime, tz_name: str, quiet: QuietHours) -> datetime:
    """Earliest UTC instant at/after now when the local clock exits quiet hours."""
    # Scan forward minute-by-minute would be slow; walk hour boundaries instead.
    # Quiet windows are daily, so the end is at most ~24h away.
    from autoposter.utils.timeutil import ensure_aware_utc, get_zone

    zone = get_zone(tz_name)
    local = now.astimezone(zone)
    end_h, end_m = (int(p) for p in quiet.end.split(":"))
    candidate_local = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if candidate_local <= local:
        candidate_local = candidate_local + timedelta(days=1)
    return ensure_aware_utc(candidate_local, tz_name=tz_name)


def _refresh_post(session: Session, post_id: int) -> None:
    post = session.get(PostRow, post_id)
    if post is None:
        return
    statuses = {TargetStatus(t.status) for t in post.targets}
    if statuses and statuses <= {TargetStatus.MISSED}:
        post.status = PostStatus.MISSED.value
        post.updated_at = utcnow()
