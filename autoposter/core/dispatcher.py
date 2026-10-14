"""Dispatcher: publishes claimed targets via platform adapters.

Contains zero platform-specific code. Adapters are resolved through a
factory callable ``(platform, account_id) -> adapter`` provided by the
platform registry (Stage 3).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoposter.auth.tokens import TokenStore
from autoposter.core import queue as queue_mod
from autoposter.core.models import (
    AccountStatus,
    ErrorCategory,
    HealthStatus,
    Post,
    PostTarget,
    PublishResult,
)
from autoposter.core.orm import PostTargetRow
from autoposter.core.retry import CircuitBreaker, RetryPolicy, compute_backoff, should_retry
from autoposter.utils.logging import get_logger
from autoposter.utils.redact import redact_text
from autoposter.utils.timeutil import utcnow

logger = get_logger("dispatcher")

SessionFactory = Callable[[], AbstractContextManager[Session]]


@runtime_checkable
class PublishAdapter(Protocol):
    """Structural contract every platform adapter satisfies."""

    name: str

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult: ...

    async def refresh_auth(self) -> None: ...

    async def healthcheck(self) -> HealthStatus: ...


AdapterFactory = Callable[[str, int], PublishAdapter]


@dataclass
class DispatchOutcome:
    """What happened to one claimed target."""

    target_id: int
    platform: str
    success: bool
    error_category: ErrorCategory | None = None
    message: str | None = None
    platform_post_id: str | None = None
    url: str | None = None
    requeued: bool = False
    needs_reauth: bool = False
    attempts: int = 0


@dataclass
class DispatcherStats:
    published: int = 0
    failed: int = 0
    requeued: int = 0
    rate_limited: int = 0
    skipped_breaker: int = 0
    needs_reauth: int = 0
    outcomes: list[DispatchOutcome] = field(default_factory=list)


class Dispatcher:
    """Fan claimed targets out to adapters under per-platform semaphores."""

    def __init__(
        self,
        *,
        adapter_factory: AdapterFactory,
        session_factory: SessionFactory,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rate_acquire: Callable[[str], Awaitable[None]] | None = None,
        concurrency_per_platform: int = 2,
        token_store: TokenStore | None = None,
        on_alert: Callable[[str, str], None] | None = None,
    ) -> None:
        self._factory = adapter_factory
        self._sessions = session_factory
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit = circuit_breaker or CircuitBreaker()
        self._rate_acquire = rate_acquire
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._concurrency = max(1, concurrency_per_platform)
        self._token_store = token_store
        self._on_alert = on_alert
        self._adapters: dict[tuple[str, int], PublishAdapter] = {}

    def _semaphore(self, platform: str) -> asyncio.Semaphore:
        if platform not in self._semaphores:
            self._semaphores[platform] = asyncio.Semaphore(self._concurrency)
        return self._semaphores[platform]

    def _adapter(self, platform: str, account_id: int) -> PublishAdapter:
        key = (platform, account_id)
        if key not in self._adapters:
            self._adapters[key] = self._factory(platform, account_id)
        return self._adapters[key]

    async def dispatch_claimed(
        self, claimed: list[queue_mod.ClaimedTarget]
    ) -> DispatcherStats:
        """Publish all claimed targets; returns aggregate stats."""
        stats = DispatcherStats()
        if not claimed:
            return stats
        tasks = [self._dispatch_one(item, stats) for item in claimed]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.exception("Dispatch task failed: %s", result)
        return stats

    async def _dispatch_one(
        self, item: queue_mod.ClaimedTarget, stats: DispatcherStats
    ) -> None:
        platform = item.platform
        if not self.circuit.allow(platform):
            outcome = DispatchOutcome(
                target_id=item.target_id,
                platform=platform,
                success=False,
                message=f"Circuit open for {platform}; requeued without consuming an attempt.",
            )
            self._requeue_without_attempt(item, outcome)
            stats.skipped_breaker += 1
            stats.outcomes.append(outcome)
            logger.warning("Circuit open for %s; target %s requeued", platform, item.target_id)
            return

        async with self._semaphore(platform):
            if self._rate_acquire is not None:
                await self._rate_acquire(platform)
            outcome = await self._publish_pipeline(item)
            stats.outcomes.append(outcome)
            if outcome.success:
                stats.published += 1
            elif outcome.needs_reauth:
                stats.needs_reauth += 1
            elif outcome.requeued:
                stats.requeued += 1
                if outcome.error_category is ErrorCategory.RATE_LIMITED:
                    stats.rate_limited += 1
            else:
                stats.failed += 1

    async def _publish_pipeline(self, item: queue_mod.ClaimedTarget) -> DispatchOutcome:
        try:
            return await self._publish_pipeline_inner(item)
        except Exception as exc:
            logger.exception(
                "Unexpected error in publish pipeline for target %s", item.target_id
            )
            with self._sessions() as session:
                queue_mod.mark_target_failed(
                    session, item.target_id, f"internal error: {exc}"
                )
            return DispatchOutcome(
                target_id=item.target_id,
                platform=item.platform,
                success=False,
                error_category=ErrorCategory.RETRYABLE,
                message=str(exc),
            )

    async def _publish_pipeline_inner(
        self, item: queue_mod.ClaimedTarget
    ) -> DispatchOutcome:
        started = utcnow()
        adapter = self._adapter(item.platform, item.account_id)
        key = item.idempotency_key
        attempts_after = item.attempts + 1

        try:
            result = await adapter.publish(item.post, item.target, key)
        except Exception as exc:  # adapter blew up — treat as retryable
            logger.exception("Adapter %s raised for target %s", item.platform, item.target_id)
            result = PublishResult(
                success=False,
                error_category=ErrorCategory.RETRYABLE,
                raw_response_redacted=redact_text(str(exc)),
            )

        # Reactive auth refresh on 401-class outcomes.
        if (
            not result.success
            and result.error_category is ErrorCategory.AUTH_EXPIRED
        ):
            try:
                await adapter.refresh_auth()
                result = await adapter.publish(item.post, item.target, key)
            except Exception as refresh_exc:
                return await self._fail_auth(
                    item, started, redact_text(str(refresh_exc))
                )
            if not result.success and result.error_category is ErrorCategory.AUTH_EXPIRED:
                detail = result.raw_response_redacted
                return await self._fail_auth(
                    item,
                    started,
                    str(detail) if detail else "auth expired",
                )

        with self._sessions() as session:
            queue_mod.record_attempt(
                session,
                item.target_id,
                started_at=started,
                http_status=None,
                error_category=result.error_category.value if result.error_category else None,
                redacted_response=_redacted(result),
            )

        if result.success:
            self.circuit.record_success(item.platform)
            with self._sessions() as session:
                queue_mod.mark_target_published(
                    session, item.target_id, platform_post_id=result.platform_post_id
                )
            logger.info(
                "Published target %s on %s (post id %s)",
                item.target_id,
                item.platform,
                result.platform_post_id,
            )
            return DispatchOutcome(
                target_id=item.target_id,
                platform=item.platform,
                success=True,
                platform_post_id=result.platform_post_id,
                url=result.url,
                attempts=attempts_after,
            )

        category = result.error_category or ErrorCategory.PERMANENT

        if category is ErrorCategory.AMBIGUOUS:
            return await self._handle_ambiguous(item, adapter, started, result)

        if category is ErrorCategory.PERMANENT:
            self.circuit.record_failure(item.platform)
            message = str(result.raw_response_redacted or "permanent failure")
            with self._sessions() as session:
                queue_mod.mark_target_failed(
                    session, item.target_id, f"permanent: {message}"
                )
            return DispatchOutcome(
                target_id=item.target_id,
                platform=item.platform,
                success=False,
                error_category=category,
                message=message,
                attempts=attempts_after,
            )

        # RETRYABLE / RATE_LIMITED
        self.circuit.record_failure(item.platform)
        if should_retry(category, attempts_after, self.retry_policy.max_attempts):
            delay = compute_backoff(
                attempts_after, self.retry_policy, retry_after=result.retry_after_seconds
            )
            next_at = datetime.fromtimestamp(
                started.timestamp() + delay, tz=started.tzinfo
            )
            message = str(result.raw_response_redacted or category.value)
            with self._sessions() as session:
                queue_mod.requeue_target(
                    session,
                    item.target_id,
                    next_attempt_at=next_at,
                    error=f"{category.value}: {message}",
                )
            logger.info(
                "Target %s requeued in %.1fs (attempt %s/%s)",
                item.target_id,
                delay,
                attempts_after,
                self.retry_policy.max_attempts,
            )
            return DispatchOutcome(
                target_id=item.target_id,
                platform=item.platform,
                success=False,
                error_category=category,
                message=message,
                requeued=True,
                attempts=attempts_after,
            )

        message = (
            f"max attempts ({self.retry_policy.max_attempts}) exceeded: "
            f"{result.raw_response_redacted}"
        )
        with self._sessions() as session:
            queue_mod.mark_target_failed(session, item.target_id, message)
        return DispatchOutcome(
            target_id=item.target_id,
            platform=item.platform,
            success=False,
            error_category=category,
            message=message,
            attempts=attempts_after,
        )

    async def _handle_ambiguous(
        self,
        item: queue_mod.ClaimedTarget,
        adapter: PublishAdapter,
        started: datetime,
        result: PublishResult,
    ) -> DispatchOutcome:
        """Never blind-retry an ambiguous outcome; reconcile first."""
        lookup = getattr(adapter, "find_recent_by_hash", None)
        platform_post_id = None
        if callable(lookup):
            try:
                platform_post_id = await lookup(item.post.content_hash)
            except Exception:  # pragma: no cover - reconcile best-effort
                platform_post_id = None
        if platform_post_id is None and result.platform_post_id:
            platform_post_id = result.platform_post_id
        if platform_post_id is not None:
            with self._sessions() as session:
                queue_mod.mark_target_published(
                    session, item.target_id, platform_post_id=platform_post_id
                )
            self.circuit.record_success(item.platform)
            return DispatchOutcome(
                target_id=item.target_id,
                platform=item.platform,
                success=True,
                platform_post_id=platform_post_id,
                message="reconciled ambiguous outcome as published",
            )
        # Could not reconcile — fail loudly rather than risk a duplicate.
        message = (
            "Ambiguous publish outcome (timeout after send) and could not "
            "confirm on the platform; marked failed to avoid a duplicate. "
            "Check the platform, then retry manually."
        )
        with self._sessions() as session:
            queue_mod.mark_target_failed(session, item.target_id, message)
        return DispatchOutcome(
            target_id=item.target_id,
            platform=item.platform,
            success=False,
            error_category=ErrorCategory.AMBIGUOUS,
            message=message,
        )

    async def _fail_auth(
        self, item: queue_mod.ClaimedTarget, started: datetime, detail: str
    ) -> DispatchOutcome:
        needs_reauth = False
        with self._sessions() as session:
            if self._token_store is not None:
                self._token_store.set_status(
                    session, item.account_id, AccountStatus.NEEDS_REAUTH
                )
            # Pause only this account's open targets, not the whole platform.
            rows = session.execute(
                select(PostTargetRow).where(
                    PostTargetRow.account_id == item.account_id,
                    PostTargetRow.status.in_(["scheduled", "publishing"]),
                )
            ).scalars().all()
            for row in rows:
                if row.id == item.target_id:
                    row.status = "failed"
                    row.last_error = f"auth: {detail}"
                    row.claimed_at = None
                else:
                    row.status = "paused"
                    row.next_attempt_at = None
            needs_reauth = True
        if self._on_alert is not None:
            self._on_alert(
                "needs_reauth",
                f"Credentials for {item.platform} account {item.account_id} expired. "
                f"Run: autoposter setup {item.platform}",
            )
        logger.error(
            "Auth expired for %s account %s; targets paused", item.platform, item.account_id
        )
        return DispatchOutcome(
            target_id=item.target_id,
            platform=item.platform,
            success=False,
            error_category=ErrorCategory.AUTH_EXPIRED,
            message=detail,
            needs_reauth=needs_reauth,
        )

    def _requeue_without_attempt(
        self, item: queue_mod.ClaimedTarget, outcome: DispatchOutcome
    ) -> None:
        next_at = utcnow()
        with self._sessions() as session:
            queue_mod.requeue_target(
                session,
                item.target_id,
                next_attempt_at=next_at,
                error=outcome.message,
                increment_attempts=False,
            )


def _redacted(result: PublishResult) -> str | None:
    raw = result.raw_response_redacted
    if raw is None:
        return None
    if isinstance(raw, str):
        return redact_text(raw)
    return redact_text(json.dumps(raw, default=str))
