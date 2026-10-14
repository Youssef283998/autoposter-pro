"""Retry classification, backoff and circuit breaker tests."""

from __future__ import annotations

import random

from autoposter.core.models import ErrorCategory
from autoposter.core.retry import (
    CircuitBreaker,
    CircuitState,
    RetryPolicy,
    compute_backoff,
    should_retry,
)


def test_backoff_exponential_no_jitter() -> None:
    policy = RetryPolicy(base_delay_seconds=2.0, max_delay_seconds=300.0, jitter=0.0)
    assert compute_backoff(1, policy) == 2.0
    assert compute_backoff(2, policy) == 4.0
    assert compute_backoff(3, policy) == 8.0
    assert compute_backoff(10, policy) == 300.0  # capped


def test_backoff_honors_retry_after() -> None:
    policy = RetryPolicy(base_delay_seconds=2.0, jitter=0.0)
    assert compute_backoff(1, policy, retry_after=42.0) == 42.0


def test_backoff_jitter_within_bounds() -> None:
    policy = RetryPolicy(base_delay_seconds=10.0, jitter=0.25)
    rng = random.Random(42)
    for _ in range(50):
        delay = compute_backoff(1, policy, rng=rng)
        assert 7.5 <= delay <= 12.5


def test_should_retry_only_retryable_and_rate_limited() -> None:
    assert should_retry(ErrorCategory.RETRYABLE, attempts=1, max_attempts=5)
    assert should_retry(ErrorCategory.RATE_LIMITED, attempts=4, max_attempts=5)
    assert not should_retry(ErrorCategory.RETRYABLE, attempts=5, max_attempts=5)
    assert not should_retry(ErrorCategory.PERMANENT, attempts=1, max_attempts=5)
    assert not should_retry(ErrorCategory.AUTH_EXPIRED, attempts=1, max_attempts=5)
    assert not should_retry(ErrorCategory.AMBIGUOUS, attempts=1, max_attempts=5)
    assert not should_retry(None, attempts=1, max_attempts=5)


def test_circuit_breaker_opens_after_threshold() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60, clock=lambda: clock[0])
    assert breaker.allow("x")
    breaker.record_failure("x")
    breaker.record_failure("x")
    assert breaker.state("x") is CircuitState.CLOSED
    breaker.record_failure("x")
    assert breaker.state("x") is CircuitState.OPEN
    assert not breaker.allow("x")


def test_circuit_breaker_half_open_after_cooldown() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60, clock=lambda: clock[0])
    breaker.record_failure("x")
    assert not breaker.allow("x")
    clock[0] = 61.0
    assert breaker.state("x") is CircuitState.HALF_OPEN
    assert breaker.allow("x")  # single probe
    assert not breaker.allow("x")  # probe already in flight


def test_circuit_breaker_success_closes() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60, clock=lambda: clock[0])
    breaker.record_failure("x")
    clock[0] = 61.0
    assert breaker.allow("x")
    breaker.record_success("x")
    assert breaker.state("x") is CircuitState.CLOSED
    assert breaker.failures("x") == 0


def test_circuit_breaker_half_open_failure_reopens() -> None:
    clock = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60, clock=lambda: clock[0])
    breaker.record_failure("x")
    clock[0] = 61.0
    assert breaker.allow("x")
    breaker.record_failure("x")
    assert breaker.state("x") is CircuitState.OPEN
    assert not breaker.allow("x")
    clock[0] = 61.0 + 61.0
    assert breaker.allow("x")


def test_circuit_breaker_independent_platforms() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure("x")
    assert not breaker.allow("x")
    assert breaker.allow("telegram")


def test_circuit_breaker_reset() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    breaker.record_failure("x")
    breaker.reset("x")
    assert breaker.allow("x")
    assert breaker.state("x") is CircuitState.CLOSED
