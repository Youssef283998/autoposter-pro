"""Retry classification, exponential backoff with jitter, circuit breaker."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from autoposter.core.models import ErrorCategory


@dataclass(frozen=True)
class RetryPolicy:
    """Backoff configuration for retryable and rate-limited failures."""

    max_attempts: int = 5
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0
    jitter: float = 0.25


def compute_backoff(
    attempt: int,
    policy: RetryPolicy,
    *,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before retry number ``attempt`` (1-based: attempt just failed).

    When the platform supplied a ``Retry-After`` hint it wins as the base
    delay; otherwise exponential backoff is used. Jitter is applied as a
    ±``policy.jitter`` fraction of the base delay.
    """
    if retry_after is not None and retry_after >= 0:
        base = float(retry_after)
    else:
        base = min(
            policy.base_delay_seconds * (2 ** max(0, attempt - 1)),
            policy.max_delay_seconds,
        )
    if policy.jitter > 0:
        spread = base * policy.jitter
        chooser = rng.uniform if rng is not None else random.uniform
        base = base + chooser(-spread, spread)
    return max(0.0, base)


def should_retry(category: ErrorCategory | None, attempts: int, max_attempts: int) -> bool:
    """Retry only retryable and rate-limited failures, until attempts run out.

    ``attempts`` is the number of attempts already made (including the one
    that just failed). AUTH_EXPIRED, PERMANENT and AMBIGUOUS are never
    blindly retried here — the dispatcher handles those specially.
    """
    if category not in (ErrorCategory.RETRYABLE, ErrorCategory.RATE_LIMITED):
        return False
    return attempts < max_attempts


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-platform circuit breaker.

    After ``failure_threshold`` consecutive failures the circuit opens and
    ``allow`` returns False for that platform. After ``cooldown_seconds`` the
    circuit becomes half-open and lets a single probe through; success closes
    it again, failure re-opens it with a fresh cooldown.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 120.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._half_open_probe: set[str] = set()

    def state(self, platform: str) -> CircuitState:
        opened_at = self._opened_at.get(platform)
        if opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - opened_at >= self.cooldown_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def allow(self, platform: str) -> bool:
        """Whether a publish to ``platform`` may proceed right now."""
        state = self.state(platform)
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.HALF_OPEN:
            if platform in self._half_open_probe:
                return False
            self._half_open_probe.add(platform)
            return True
        return False

    def record_success(self, platform: str) -> None:
        """Close the circuit and clear failure counts."""
        self._failures.pop(platform, None)
        self._opened_at.pop(platform, None)
        self._half_open_probe.discard(platform)

    def record_failure(self, platform: str) -> None:
        """Count a consecutive failure; open the circuit at the threshold."""
        self._half_open_probe.discard(platform)
        failures = self._failures.get(platform, 0) + 1
        self._failures[platform] = failures
        state = self.state(platform)
        if state is CircuitState.HALF_OPEN or failures >= self.failure_threshold:
            self._opened_at[platform] = self._clock()

    def failures(self, platform: str) -> int:
        return self._failures.get(platform, 0)

    def reset(self, platform: str | None = None) -> None:
        """Forget breaker state for one platform or all."""
        platforms = [platform] if platform else list(self._failures | self._opened_at)
        for name in platforms:
            self._failures.pop(name, None)
            self._opened_at.pop(name, None)
            self._half_open_probe.discard(name)
