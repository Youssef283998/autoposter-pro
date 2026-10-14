"""Dispatcher integration tests with a fake adapter (no network)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.engine import Engine

from autoposter.auth.tokens import TokenStore
from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.dispatcher import Dispatcher
from autoposter.core.models import (
    ErrorCategory,
    HealthStatus,
    Post,
    PostTarget,
    PublishResult,
)
from autoposter.core.orm import AccountRow, PostTargetRow
from autoposter.core.retry import CircuitBreaker, RetryPolicy
from autoposter.utils.timeutil import utcnow


class FakeAdapter:
    """Scriptable in-memory platform adapter."""

    name = "fake"

    def __init__(self) -> None:
        self.published: list[str] = []
        self.results: list[PublishResult] = []
        self.refresh_calls = 0
        self.fail_refresh = False
        self.raise_on_publish: Exception | None = None
        self.find_hash_result: str | None = None

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        if self.results:
            return self.results.pop(0)
        self.published.append(idempotency_key)
        return PublishResult(success=True, platform_post_id=f"p-{idempotency_key}", url="http://x/1")

    async def refresh_auth(self) -> None:
        self.refresh_calls += 1
        if self.fail_refresh:
            raise RuntimeError("refresh failed")

    async def healthcheck(self) -> HealthStatus:
        return HealthStatus(ok=True, detail="fake ok")

    async def find_recent_by_hash(self, content_hash: str) -> str | None:
        return self.find_hash_result


def _make_session_factory(engine: Engine):
    from contextlib import contextmanager

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def _session():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _session


def _account_id(engine: Engine) -> int:
    with session_scope(engine) as session:
        account = AccountRow(platform="fake", label="default", encrypted_credentials="x")
        session.add(account)
        session.flush()
        return int(account.id)


def _due_post(engine: Engine, account_id: int, body: str = "hello") -> tuple[int, int]:
    with session_scope(engine) as session:
        post = q.add_post(
            session,
            body=body,
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        return int(post.id), int(post.targets[0].id)


def _dispatcher(engine: Engine, adapter: FakeAdapter, **kwargs: Any) -> Dispatcher:
    return Dispatcher(
        adapter_factory=lambda p, a: adapter,
        session_factory=_make_session_factory(engine),
        **kwargs,
    )


async def test_dispatch_success_marks_published(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.published == 1
    assert adapter.published
    with session_scope(engine) as session:
        from sqlalchemy import select

        rows = session.execute(select(PostTargetRow)).scalars().all()
        assert rows[0].status == "published"
        assert rows[0].platform_post_id is not None


async def test_dispatch_retryable_requeues_with_backoff(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(
            success=False,
            error_category=ErrorCategory.RETRYABLE,
            raw_response_redacted="503 unavailable",
        )
    ]
    _due_post(engine, account_id)
    dispatcher = _dispatcher(
        engine, adapter, retry_policy=RetryPolicy(max_attempts=5, base_delay_seconds=2, jitter=0)
    )
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.requeued == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "scheduled"
        assert row.attempts == 1
        assert row.next_attempt_at is not None
        assert row.next_attempt_at > utcnow()


async def test_dispatch_rate_limit_honors_retry_after(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(
            success=False,
            error_category=ErrorCategory.RATE_LIMITED,
            retry_after_seconds=120.0,
            raw_response_redacted="429 slow down",
        )
    ]
    _due_post(engine, account_id)
    dispatcher = _dispatcher(
        engine, adapter, retry_policy=RetryPolicy(max_attempts=5, base_delay_seconds=1, jitter=0)
    )
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.rate_limited == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "scheduled"
        # next_attempt should be ~120s out, not the 2s exponential default
        delta = (row.next_attempt_at - utcnow()).total_seconds()
        assert 100 <= delta <= 130


async def test_dispatch_max_attempts_marks_failed(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(success=False, error_category=ErrorCategory.RETRYABLE)
        for _ in range(3)
    ]
    _pid, tid = _due_post(engine, account_id)
    with session_scope(engine) as session:
        # Pretend 2 attempts already happened.
        row = session.get(PostTargetRow, tid)
        assert row is not None
        row.attempts = 2
    dispatcher = _dispatcher(
        engine, adapter, retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=1, jitter=0)
    )
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.failed == 1
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == "failed"
        assert "max attempts" in (row.last_error or "")


async def test_dispatch_permanent_fails_immediately(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(
            success=False,
            error_category=ErrorCategory.PERMANENT,
            raw_response_redacted="400 bad request",
        )
    ]
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.failed == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "failed"
        assert row.attempts == 1


async def test_auth_expired_refresh_then_success(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(success=False, error_category=ErrorCategory.AUTH_EXPIRED),
        PublishResult(success=True, platform_post_id="after-refresh"),
    ]
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.published == 1
    assert adapter.refresh_calls == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "published"
        assert row.platform_post_id == "after-refresh"


async def test_auth_expired_refresh_fails_needs_reauth(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(success=False, error_category=ErrorCategory.AUTH_EXPIRED)
        for _ in range(5)
    ]
    adapter.fail_refresh = True
    alerts: list[tuple[str, str]] = []
    _due_post(engine, account_id)
    # Second post on same account to verify it gets paused.
    _due_post(engine, account_id, body="second")
    dispatcher = _dispatcher(
        engine,
        adapter,
        token_store=TokenStore(b"unused-placeholder-key-32bytes!!"),
        on_alert=lambda kind, msg: alerts.append((kind, msg)),
    )
    # Fix token store key properly
    from autoposter.auth.crypto import generate_key

    key = generate_key()
    dispatcher._token_store = TokenStore(key)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
        # seed account row already exists
    stats = await dispatcher.dispatch_claimed(claimed[:1])
    assert stats.needs_reauth == 1
    assert any(kind == "needs_reauth" for kind, _ in alerts)
    with session_scope(engine) as session:
        rows = session.execute(select(PostTargetRow)).scalars().all()
        statuses = sorted(r.status for r in rows)
        assert "failed" in statuses
        assert "paused" in statuses


async def test_ambiguous_reconciles_when_lookup_finds_post(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(success=False, error_category=ErrorCategory.AMBIGUOUS)
    ]
    adapter.find_hash_result = "recovered-id"
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.published == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "published"
        assert row.platform_post_id == "recovered-id"


async def test_ambiguous_without_lookup_fails_safe(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.results = [
        PublishResult(success=False, error_category=ErrorCategory.AMBIGUOUS)
    ]
    adapter.find_hash_result = None
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.failed == 1
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "failed"
        assert "duplicate" in (row.last_error or "").lower()


async def test_adapter_exception_treated_as_retryable(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    adapter.raise_on_publish = RuntimeError("network exploded")
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.requeued == 1


async def test_circuit_open_requeues_without_attempt(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
    breaker.record_failure("fake")
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter, circuit_breaker=breaker)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    stats = await dispatcher.dispatch_claimed(claimed)
    assert stats.skipped_breaker == 1
    assert adapter.published == []
    with session_scope(engine) as session:
        row = session.execute(select(PostTargetRow)).scalars().one()
        assert row.status == "scheduled"
        assert row.attempts == 0  # breaker skip does not burn an attempt


async def test_attempts_audit_written(engine: Engine) -> None:
    account_id = _account_id(engine)
    adapter = FakeAdapter()
    _due_post(engine, account_id)
    dispatcher = _dispatcher(engine, adapter)
    with session_scope(engine) as session:
        claimed = q.claim_due_targets(session, now=utcnow(), limit=10)
    await dispatcher.dispatch_claimed(claimed)
    with session_scope(engine) as session:
        from sqlalchemy import select

        from autoposter.core.orm import AttemptRow

        attempts = session.execute(select(AttemptRow)).scalars().all()
        assert len(attempts) == 1
        assert attempts[0].finished_at is not None


# Late import for select used in multiple tests
from sqlalchemy import select  # noqa: E402
