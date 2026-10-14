"""Duplicate prevention: same content hash per account within a window."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoposter.config.schema import DedupConfig
from autoposter.core.errors import DuplicatePostError
from autoposter.core.orm import PostRow, PostTargetRow


@dataclass(frozen=True)
class DuplicateHit:
    """A recently published post with the same content hash on the same account."""

    post_id: int
    target_id: int
    platform: str
    account_id: int
    published_at: datetime | None


def find_recent_duplicate(
    session: Session,
    *,
    platform: str,
    account_id: int,
    content_hash: str,
    since: datetime,
    exclude_post_id: int | None = None,
) -> DuplicateHit | None:
    """Return the most recent published duplicate on this account, if any."""
    stmt = (
        select(PostTargetRow, PostRow)
        .join(PostRow, PostTargetRow.post_id == PostRow.id)
        .where(
            PostTargetRow.platform == platform,
            PostTargetRow.account_id == account_id,
            PostTargetRow.status == "published",
            PostRow.content_hash == content_hash,
            PostRow.updated_at >= since,
        )
        .order_by(PostRow.updated_at.desc())
        .limit(1)
    )
    if exclude_post_id is not None:
        stmt = stmt.where(PostRow.id != exclude_post_id)
    row = session.execute(stmt).first()
    if row is None:
        return None
    target, post = row
    return DuplicateHit(
        post_id=post.id,
        target_id=target.id,
        platform=platform,
        account_id=account_id,
        published_at=post.updated_at,
    )


@dataclass(frozen=True)
class DedupVerdict:
    """Result of a dedup check for one target."""

    blocked: bool
    hit: DuplicateHit | None = None
    warning: str | None = None


class DuplicateGuard:
    """Applies ``DedupConfig`` (warn or block) at scheduling and publish time."""

    def __init__(
        self,
        config: DedupConfig,
        *,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._now = now_fn or _default_now

    def check(
        self,
        session: Session,
        *,
        platform: str,
        account_id: int,
        content_hash: str,
        exclude_post_id: int | None = None,
    ) -> DedupVerdict:
        if self.config.window_hours <= 0:
            return DedupVerdict(blocked=False)
        now = self._now()
        since = now - timedelta(hours=self.config.window_hours)
        hit = find_recent_duplicate(
            session,
            platform=platform,
            account_id=account_id,
            content_hash=content_hash,
            since=since,
            exclude_post_id=exclude_post_id,
        )
        if hit is None:
            return DedupVerdict(blocked=False)
        warning = (
            f"Identical content was already published to {platform} account "
            f"{account_id} (post {hit.post_id}) within the last "
            f"{self.config.window_hours}h."
        )
        if self.config.mode == "block":
            return DedupVerdict(blocked=True, hit=hit, warning=warning)
        return DedupVerdict(blocked=False, hit=hit, warning=warning)

    def enforce(
        self,
        session: Session,
        *,
        platform: str,
        account_id: int,
        content_hash: str,
        exclude_post_id: int | None = None,
    ) -> DedupVerdict:
        """Like :meth:`check` but raises :class:`DuplicatePostError` when blocking."""
        verdict = self.check(
            session,
            platform=platform,
            account_id=account_id,
            content_hash=content_hash,
            exclude_post_id=exclude_post_id,
        )
        if verdict.blocked and verdict.warning:
            raise DuplicatePostError(
                verdict.warning,
                fix=(
                    "Change the text slightly, wait out the dedup window, "
                    "or set dedup.mode to 'warn'."
                ),
            )
        return verdict


def _default_now() -> datetime:
    from autoposter.utils.timeutil import utcnow

    return utcnow()
