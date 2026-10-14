"""Crash recovery: reconcile targets stuck in ``publishing`` after a restart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from autoposter.core import queue as queue_mod
from autoposter.core.models import TargetStatus
from autoposter.core.orm import PostTargetRow
from autoposter.utils.logging import get_logger
from autoposter.utils.timeutil import utcnow

logger = get_logger("recovery")

# platform, account_id, idempotency_key, content_hash -> platform post id or None
LookupFn = Callable[[str, int, str, str], "str | None"]


@dataclass
class RecoveryReport:
    """Outcome of the startup reconcile pass."""

    reconciled_published: list[int] = field(default_factory=list)
    requeued: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def reconcile_stuck_publishing(
    session: Session,
    *,
    lookup: LookupFn | None = None,
    stuck_threshold_seconds: float = 300.0,
    now: datetime | None = None,
) -> RecoveryReport:
    """Recover targets stuck in ``publishing`` (crash / power loss mid-post).

    For each stuck target:
    1. If ``lookup`` finds a matching platform post id (via idempotency key or
       content-hash search on the platform), mark it published.
    2. Otherwise requeue it so the next tick retries with the same
       idempotency key (adapters that support dedup will not double-post).

    Targets claimed more recently than ``stuck_threshold_seconds`` are left
    alone — another worker may still be publishing them.
    """
    report = RecoveryReport()
    current = now or utcnow()
    cutoff = current - timedelta(seconds=stuck_threshold_seconds)
    rows = session.execute(
        select(PostTargetRow)
        .where(
            PostTargetRow.status == TargetStatus.PUBLISHING.value,
            PostTargetRow.claimed_at.is_not(None),
            PostTargetRow.claimed_at < cutoff,
        )
        .options(selectinload(PostTargetRow.post))
    ).scalars().all()

    for row in rows:
        post = row.post
        platform_post_id = None
        if lookup is not None:
            try:
                platform_post_id = lookup(
                    row.platform, row.account_id, row.idempotency_key, post.content_hash
                )
            except Exception:
                logger.exception("Reconcile lookup failed for target %s", row.id)
                platform_post_id = None
        if platform_post_id is not None:
            row.status = TargetStatus.PUBLISHED.value
            row.platform_post_id = platform_post_id
            row.claimed_at = None
            row.last_error = None
            queue_mod._refresh_post_status(session, row.post_id)
            report.reconciled_published.append(row.id)
            logger.info(
                "Recovered target %s as published (platform id %s)",
                row.id,
                platform_post_id,
            )
        else:
            row.status = TargetStatus.SCHEDULED.value
            row.next_attempt_at = current
            row.claimed_at = None
            row.last_error = (
                "Recovered from interrupted publish; will retry with the same idempotency key."
            )
            report.requeued.append(row.id)
            logger.warning(
                "Target %s was stuck in publishing; requeued for retry", row.id
            )
    session.flush()
    return report
