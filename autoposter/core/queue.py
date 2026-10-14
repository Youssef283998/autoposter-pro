"""Queue operations: add, list, claim, and per-target state transitions.

The database is the single source of truth. ``claim_due_targets`` performs an
atomic ``UPDATE ... WHERE status='scheduled'`` inside one transaction so two
workers can never publish the same target.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from autoposter.core.errors import PostValidationError
from autoposter.core.models import (
    MediaItem,
    Post,
    PostStatus,
    PostTarget,
    TargetStatus,
    compute_content_hash,
    make_idempotency_key,
)
from autoposter.core.orm import AttemptRow, MediaRow, PostRow, PostTargetRow
from autoposter.utils.timeutil import utcnow

TERMINAL_TARGET_STATUSES = frozenset(
    {TargetStatus.PUBLISHED, TargetStatus.FAILED, TargetStatus.CANCELLED, TargetStatus.MISSED}
)
EDITABLE_POST_STATUSES = frozenset({PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.PAUSED})


@dataclass(frozen=True)
class TargetSpec:
    """One destination requested at schedule time."""

    platform: str
    account_id: int
    variant_body: str | None = None


@dataclass(frozen=True)
class ClaimedTarget:
    """A target atomically claimed for publishing."""

    target_id: int
    post_id: int
    platform: str
    account_id: int
    body: str
    idempotency_key: str
    attempts: int
    post: Post
    target: PostTarget


# ---------------------------------------------------------------------------
# Row <-> domain mappers
# ---------------------------------------------------------------------------


def media_row_to_domain(row: MediaRow) -> MediaItem:
    return MediaItem(
        source_path=Path(row.source_path),
        stored_path=Path(row.stored_path) if row.stored_path else None,
        mime=row.mime,
        size=row.size,
        sha256=row.sha256,
        alt_text=row.alt_text,
        platform_media_ids=dict(row.platform_media_ids or {}),
    )


def target_row_to_domain(row: PostTargetRow) -> PostTarget:
    return PostTarget(
        id=row.id,
        post_id=row.post_id,
        platform=row.platform,
        account_id=row.account_id,
        variant_body=row.variant_body,
        status=TargetStatus(row.status),
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        claimed_at=row.claimed_at,
        platform_post_id=row.platform_post_id,
        last_error=row.last_error,
        idempotency_key=row.idempotency_key,
    )


def post_row_to_domain(row: PostRow) -> Post:
    return Post(
        id=row.id,
        group_id=row.group_id,
        body=row.body,
        scheduled_at_utc=row.scheduled_at_utc,
        timezone=row.timezone,
        status=PostStatus(row.status),
        recurrence_rule=row.recurrence_rule,
        content_hash=row.content_hash,
        media=[
            MediaItem(
                source_path=Path(media.source_path),
                stored_path=Path(media.stored_path) if media.stored_path else None,
                mime=media.mime,
                size=media.size,
                sha256=media.sha256,
                alt_text=media.alt_text,
                platform_media_ids=dict(media.platform_media_ids or {}),
            )
            for media in row.media
        ],
        targets=[target_row_to_domain(t) for t in row.targets],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _load_post_row(session: Session, post_id: int) -> PostRow | None:
    stmt = (
        select(PostRow)
        .where(PostRow.id == post_id)
        .options(selectinload(PostRow.targets), selectinload(PostRow.media))
    )
    return session.execute(stmt).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


def add_post(
    session: Session,
    *,
    body: str,
    scheduled_at_utc: datetime,
    timezone: str = "UTC",
    targets: Sequence[TargetSpec],
    media: Sequence[MediaItem] = (),
    recurrence_rule: str | None = None,
    variants: dict[str, str] | None = None,
    status: PostStatus = PostStatus.SCHEDULED,
    group_id: str | None = None,
    content_hash: str | None = None,
) -> Post:
    """Create a post and one target row per destination."""
    if not body.strip() and not media:
        raise PostValidationError(
            "Post body is empty and no media was provided.",
            fix="Add text or at least one media file.",
        )
    if not targets:
        raise PostValidationError(
            "A post needs at least one platform target.",
            fix="Pass --platform (repeatable) or include a platforms column in your import.",
        )
    now = utcnow()
    if scheduled_at_utc.tzinfo is None:
        raise PostValidationError(
            "scheduled_at_utc must be timezone-aware.",
            fix="Pass an aware UTC datetime (see autoposter.utils.timeutil.ensure_aware_utc).",
        )
    digest = content_hash or compute_content_hash(
        body, [m.sha256 for m in media]
    )

    row = PostRow(
        group_id=group_id,
        body=body,
        scheduled_at_utc=scheduled_at_utc,
        timezone=timezone,
        status=status.value,
        recurrence_rule=recurrence_rule,
        content_hash=digest,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()

    seen: set[tuple[str, int]] = set()
    for spec in targets:
        key = (spec.platform, spec.account_id)
        if key in seen:
            raise PostValidationError(
                f"Duplicate target {spec.platform}/{spec.account_id}.",
                fix="List each platform+account only once per post.",
            )
        seen.add(key)
        session.add(
            PostTargetRow(
                post_id=row.id,
                platform=spec.platform,
                account_id=spec.account_id,
                variant_body=spec.variant_body,
                status=(
                    status.value
                    if status in (PostStatus.DRAFT, PostStatus.PAUSED)
                    else TargetStatus.SCHEDULED.value
                ),
                attempts=0,
                next_attempt_at=(
                    scheduled_at_utc if status is PostStatus.SCHEDULED else None
                ),
                idempotency_key=make_idempotency_key(row.id, spec.platform, spec.account_id),
            )
        )

    for item in media:
        session.add(
            MediaRow(
                post_id=row.id,
                source_path=str(item.source_path),
                stored_path=str(item.stored_path) if item.stored_path else None,
                mime=item.mime,
                size=item.size,
                sha256=item.sha256,
                alt_text=item.alt_text,
                platform_media_ids=dict(item.platform_media_ids),
            )
        )
    session.flush()
    loaded = _load_post_row(session, row.id)
    if loaded is None:
        raise PostValidationError(f"Post {row.id} not found after insert.")
    return post_row_to_domain(loaded)


def get_post(session: Session, post_id: int) -> Post | None:
    row = _load_post_row(session, post_id)
    return post_row_to_domain(row) if row else None


def list_posts(
    session: Session,
    *,
    status: PostStatus | None = None,
    platform: str | None = None,
    scheduled_before: datetime | None = None,
    scheduled_after: datetime | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list[Post]:
    """List posts with optional filters, newest schedule first."""
    stmt = select(PostRow).options(selectinload(PostRow.targets), selectinload(PostRow.media))
    if status is not None:
        stmt = stmt.where(PostRow.status == status.value)
    if platform is not None:
        stmt = stmt.where(
            PostRow.id.in_(select(PostTargetRow.post_id).where(PostTargetRow.platform == platform))
        )
    if scheduled_before is not None:
        stmt = stmt.where(PostRow.scheduled_at_utc <= scheduled_before)
    if scheduled_after is not None:
        stmt = stmt.where(PostRow.scheduled_at_utc >= scheduled_after)
    stmt = stmt.order_by(PostRow.scheduled_at_utc).limit(limit).offset(offset)
    rows = session.execute(stmt).scalars().all()
    return [post_row_to_domain(row) for row in rows]


# ---------------------------------------------------------------------------
# Mutations (edit / pause / cancel / reschedule)
# ---------------------------------------------------------------------------


def update_post_body(
    session: Session,
    post_id: int,
    *,
    body: str | None = None,
    variants: dict[str, str] | None = None,
) -> Post:
    """Edit body/variants — only allowed before publishing starts."""
    row = _load_post_row(session, post_id)
    if row is None:
        raise PostValidationError(
            f"Post {post_id} not found.",
            fix="Run `autoposter post list` to see existing posts.",
        )
    if PostStatus(row.status) not in EDITABLE_POST_STATUSES:
        raise PostValidationError(
            f"Post {post_id} is {row.status} and can no longer be edited.",
            fix="Cancel it and create a new post instead.",
        )
    if body is not None:
        row.body = body
        row.content_hash = compute_content_hash(
            body, [m.sha256 for m in row.media]
        )
    row.updated_at = utcnow()
    session.flush()
    loaded = _load_post_row(session, post_id)
    if loaded is None:
        raise PostValidationError(f"Post {post_id} not found.")
    return post_row_to_domain(loaded)


def reschedule_post(session: Session, post_id: int, new_time_utc: datetime) -> Post:
    """Move a post to a new time; resets target next_attempt_at."""
    row = _load_post_row(session, post_id)
    if row is None:
        raise PostValidationError(
            f"Post {post_id} not found.",
            fix="Run `autoposter post list` to see existing posts.",
        )
    if PostStatus(row.status) in (PostStatus.PUBLISHED, PostStatus.CANCELLED):
        raise PostValidationError(
            f"Post {post_id} is {row.status} and cannot be rescheduled.",
            fix="Create a new post instead.",
        )
    if new_time_utc.tzinfo is None:
        raise PostValidationError("new_time_utc must be timezone-aware.")
    row.scheduled_at_utc = new_time_utc
    row.status = PostStatus.SCHEDULED.value
    row.updated_at = utcnow()
    for target in row.targets:
        if TargetStatus(target.status) in (
            TargetStatus.SCHEDULED,
            TargetStatus.PAUSED,
            TargetStatus.MISSED,
            TargetStatus.FAILED,
        ):
            target.status = TargetStatus.SCHEDULED.value
            target.next_attempt_at = new_time_utc
            target.claimed_at = None
    session.flush()
    loaded = _load_post_row(session, post_id)
    assert loaded is not None
    return post_row_to_domain(loaded)


def set_post_status(session: Session, post_id: int, status: PostStatus) -> bool:
    """Set the post-level status; also mirrors pause/cancel onto open targets."""
    row = session.get(PostRow, post_id)
    if row is None:
        return False
    row.status = status.value
    row.updated_at = utcnow()
    if status in (PostStatus.PAUSED, PostStatus.CANCELLED):
        target_status = (
            TargetStatus.PAUSED if status is PostStatus.PAUSED else TargetStatus.CANCELLED
        )
        for target in row.targets:
            if TargetStatus(target.status) in (
                TargetStatus.SCHEDULED,
                TargetStatus.MISSED,
                TargetStatus.FAILED,
            ):
                target.status = target_status.value
                if target_status is TargetStatus.PAUSED:
                    target.next_attempt_at = None
    elif status is PostStatus.SCHEDULED:
        for target in row.targets:
            if TargetStatus(target.status) in (
                TargetStatus.PAUSED,
                TargetStatus.MISSED,
                TargetStatus.FAILED,
            ):
                target.status = TargetStatus.SCHEDULED.value
                target.next_attempt_at = row.scheduled_at_utc
    session.flush()
    return True


def delete_post(session: Session, post_id: int) -> bool:
    """Hard-delete a post and its targets/media (cascade)."""
    row = session.get(PostRow, post_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def retry_target(session: Session, target_id: int, *, when: datetime | None = None) -> bool:
    """Re-queue a failed/cancelled/missed target for another attempt."""
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return False
    if TargetStatus(row.status) in (TargetStatus.PUBLISHED, TargetStatus.PUBLISHING):
        return False
    row.status = TargetStatus.SCHEDULED.value
    row.next_attempt_at = when or utcnow()
    row.claimed_at = None
    row.last_error = None
    session.flush()
    return True


def retry_failed_targets_for_post(session: Session, post_id: int) -> int:
    """Re-queue every non-published target of a post. Returns count."""
    rows = session.execute(
        select(PostTargetRow).where(PostTargetRow.post_id == post_id)
    ).scalars().all()
    count = 0
    now = utcnow()
    for row in rows:
        if TargetStatus(row.status) in (
            TargetStatus.FAILED,
            TargetStatus.MISSED,
            TargetStatus.CANCELLED,
        ):
            row.status = TargetStatus.SCHEDULED.value
            row.next_attempt_at = now
            row.claimed_at = None
            count += 1
    if count:
        post = session.get(PostRow, post_id)
        if post is not None:
            post.status = PostStatus.SCHEDULED.value
            post.updated_at = now
    session.flush()
    return count


# ---------------------------------------------------------------------------
# Atomic claim + publish state transitions
# ---------------------------------------------------------------------------


def claim_due_targets(
    session: Session,
    *,
    now: datetime,
    limit: int = 20,
) -> list[ClaimedTarget]:
    """Atomically claim up to ``limit`` due targets (single UPDATE).

    Uses ``UPDATE ... WHERE id IN (SELECT ... LIMIT n) RETURNING id`` so the
    whole claim is one SQLite statement and therefore atomic across writers.
    """
    if limit <= 0:
        return []
    due = (
        select(PostTargetRow.id)
        .where(
            PostTargetRow.status == TargetStatus.SCHEDULED.value,
            PostTargetRow.next_attempt_at.is_not(None),
            PostTargetRow.next_attempt_at <= now,
        )
        .order_by(PostTargetRow.next_attempt_at)
        .limit(limit)
    )
    stmt = (
        update(PostTargetRow)
        .where(
            PostTargetRow.status == TargetStatus.SCHEDULED.value,
            PostTargetRow.id.in_(due),
        )
        .values(status=TargetStatus.PUBLISHING.value, claimed_at=now)
        .returning(PostTargetRow.id)
    )
    claimed_ids = list(session.execute(stmt).scalars())
    if not claimed_ids:
        return []
    # Mirror onto the parent posts so edit/cancel rules see "publishing".
    post_ids = session.execute(
        select(PostTargetRow.post_id).where(PostTargetRow.id.in_(claimed_ids))
    ).scalars().all()
    if post_ids:
        session.execute(
            update(PostRow)
            .where(
                PostRow.id.in_(list(post_ids)),
                PostRow.status == PostStatus.SCHEDULED.value,
            )
            .values(status=PostStatus.PUBLISHING.value, updated_at=now)
        )
    rows = session.execute(
        select(PostTargetRow)
        .where(PostTargetRow.id.in_(claimed_ids))
        .options(
            selectinload(PostTargetRow.post).selectinload(PostRow.media),
            selectinload(PostTargetRow.post).selectinload(PostRow.targets),
        )
    ).scalars().all()
    results: list[ClaimedTarget] = []
    for row in rows:
        post = post_row_to_domain(row.post)
        target = target_row_to_domain(row)
        body = resolve_body(post, target)
        results.append(
            ClaimedTarget(
                target_id=row.id,
                post_id=row.post_id,
                platform=row.platform,
                account_id=row.account_id,
                body=body,
                idempotency_key=row.idempotency_key,
                attempts=row.attempts,
                post=post,
                target=target,
            )
        )
    return results


def resolve_body(post: Post, target: PostTarget) -> str:
    """Per-platform variant, then platform map, then the main body."""
    if target.variant_body is not None:
        return target.variant_body
    return post.variants.get(target.platform, post.body)


def mark_target_published(
    session: Session,
    target_id: int,
    *,
    platform_post_id: str | None = None,
) -> None:
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return
    row.status = TargetStatus.PUBLISHED.value
    row.platform_post_id = platform_post_id
    row.claimed_at = None
    row.last_error = None
    row.attempts += 1
    _refresh_post_status(session, row.post_id)
    session.flush()


def mark_target_failed(session: Session, target_id: int, error: str) -> None:
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return
    row.status = TargetStatus.FAILED.value
    row.last_error = error
    row.claimed_at = None
    row.attempts += 1
    _refresh_post_status(session, row.post_id)
    session.flush()


def mark_target_missed(session: Session, target_id: int, error: str) -> None:
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return
    row.status = TargetStatus.MISSED.value
    row.last_error = error
    row.claimed_at = None
    _refresh_post_status(session, row.post_id)
    session.flush()


def requeue_target(
    session: Session,
    target_id: int,
    *,
    next_attempt_at: datetime,
    error: str | None = None,
    increment_attempts: bool = True,
) -> None:
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return
    row.status = TargetStatus.SCHEDULED.value
    row.next_attempt_at = next_attempt_at
    row.claimed_at = None
    if error is not None:
        row.last_error = error
    if increment_attempts:
        row.attempts += 1
    session.flush()


def record_attempt(
    session: Session,
    target_id: int,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
    http_status: int | None = None,
    error_category: str | None = None,
    redacted_response: str | None = None,
) -> int:
    """Append one audit row; returns the attempt id."""
    row = AttemptRow(
        target_id=target_id,
        started_at=started_at,
        finished_at=finished_at or utcnow(),
        http_status=http_status,
        error_category=error_category,
        redacted_response=redacted_response,
    )
    session.add(row)
    session.flush()
    return int(row.id)


def pause_target(session: Session, target_id: int) -> bool:
    row = session.get(PostTargetRow, target_id)
    if row is None:
        return False
    if TargetStatus(row.status) not in (
        TargetStatus.SCHEDULED,
        TargetStatus.MISSED,
        TargetStatus.FAILED,
    ):
        return False
    row.status = TargetStatus.PAUSED.value
    row.next_attempt_at = None
    session.flush()
    return True


def resume_target(session: Session, target_id: int, *, when: datetime | None = None) -> bool:
    row = session.get(PostTargetRow, target_id)
    if row is None or TargetStatus(row.status) is not TargetStatus.PAUSED:
        return False
    row.status = TargetStatus.SCHEDULED.value
    post = session.get(PostRow, row.post_id)
    row.next_attempt_at = when or (post.scheduled_at_utc if post else utcnow())
    session.flush()
    return True


def _refresh_post_status(session: Session, post_id: int) -> None:
    """Roll target terminal states up into the post status."""
    post = session.get(PostRow, post_id)
    if post is None:
        return
    statuses = {TargetStatus(t.status) for t in post.targets}
    if not statuses:
        return
    if TargetStatus.PUBLISHING in statuses or TargetStatus.SCHEDULED in statuses:
        if PostStatus(post.status) != PostStatus.PUBLISHING:
            post.status = PostStatus.PUBLISHING.value
    elif statuses <= {TargetStatus.PUBLISHED}:
        post.status = PostStatus.PUBLISHED.value
    elif statuses <= TERMINAL_TARGET_STATUSES | {TargetStatus.PAUSED}:
        non_paused = statuses - {TargetStatus.PAUSED}
        if non_paused <= {TargetStatus.PUBLISHED}:
            post.status = PostStatus.PUBLISHED.value
        elif TargetStatus.MISSED in non_paused:
            post.status = PostStatus.MISSED.value
        elif TargetStatus.FAILED in non_paused:
            post.status = PostStatus.FAILED.value
        else:
            post.status = PostStatus.CANCELLED.value
    post.updated_at = utcnow()


@dataclass
class QueueStats:
    """Counts by target status — used by ``post list`` and the dashboard."""

    by_status: dict[str, int] = field(default_factory=dict)
