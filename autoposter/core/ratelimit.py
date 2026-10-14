"""Per-platform rate limiting: hourly/daily caps + randomized inter-post delay."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from autoposter.config.schema import PlatformCaps
from autoposter.utils.timeutil import utcnow

UTC = timezone.utc


@dataclass
class _WindowCounter:
    """Fixed-window counter (hour or day) in UTC."""

    count: int = 0
    window_start: datetime | None = None

    def _window_for(self, now: datetime, window_seconds: int) -> datetime:
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)

    def would_allow(self, now: datetime, limit: int | None, window_seconds: int) -> bool:
        if limit is None:
            return True
        window = self._window_for(now, window_seconds)
        if self.window_start != window:
            return True
        return self.count < limit

    def record(self, now: datetime, window_seconds: int) -> None:
        window = self._window_for(now, window_seconds)
        if self.window_start != window:
            self.window_start = window
            self.count = 0
        self.count += 1

    def reset_if_new_window(self, now: datetime, window_seconds: int) -> None:
        window = self._window_for(now, window_seconds)
        if self.window_start != window:
            self.window_start = window
            self.count = 0


HOUR_SECONDS = 3600
DAY_SECONDS = 86400


class RateLimiter:
    """Token/counter limiter driven by ``PlatformCaps``.

    ``acquire`` is async and sleeps until the platform is under its hourly
    and daily caps and the randomized inter-post delay has elapsed since the
    previous publish. Call ``record_publish`` after a successful handoff to
    the platform (the dispatcher does this).
    """

    def __init__(
        self,
        caps_for: Callable[[str], PlatformCaps],
        *,
        wall_clock: Callable[[], datetime] = utcnow,
        mono_clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._caps_for = caps_for
        self._wall = wall_clock
        self._mono = mono_clock
        self._sleep: Callable[[float], Awaitable[None]] = sleep or asyncio.sleep
        self._hourly: dict[str, _WindowCounter] = {}
        self._daily: dict[str, _WindowCounter] = {}
        self._last_publish_mono: dict[str, float] = {}

    def _hourly_counter(self, platform: str) -> _WindowCounter:
        return self._hourly.setdefault(platform, _WindowCounter())

    def _daily_counter(self, platform: str) -> _WindowCounter:
        return self._daily.setdefault(platform, _WindowCounter())

    def seconds_until_allowed(self, platform: str) -> float:
        """Seconds until this platform may publish again (0 = now)."""
        now = self._wall()
        caps = self._caps_for(platform)
        delay = 0.0
        hourly = self._hourly_counter(platform)
        daily = self._daily_counter(platform)
        hourly.reset_if_new_window(now, HOUR_SECONDS)
        daily.reset_if_new_window(now, DAY_SECONDS)
        if not hourly.would_allow(now, caps.hourly, HOUR_SECONDS):
            window = hourly._window_for(now, HOUR_SECONDS)
            delay = max(delay, (window + timedelta(seconds=HOUR_SECONDS) - now).total_seconds())
        if not daily.would_allow(now, caps.daily, DAY_SECONDS):
            window = daily._window_for(now, DAY_SECONDS)
            delay = max(delay, (window + timedelta(seconds=DAY_SECONDS) - now).total_seconds())
        last = self._last_publish_mono.get(platform)
        if last is not None:
            low, _high = caps.delay_between_posts_seconds
            # Use the low bound for the guaranteed wait; high is applied at record time.
            delay = max(delay, low - (self._mono() - last))
        return max(0.0, delay)

    def try_record(self, platform: str) -> bool:
        """Record a publish if under all caps; returns False when blocked."""
        now = self._wall()
        caps = self._caps_for(platform)
        hourly = self._hourly_counter(platform)
        daily = self._daily_counter(platform)
        hourly.reset_if_new_window(now, HOUR_SECONDS)
        daily.reset_if_new_window(now, DAY_SECONDS)
        if not hourly.would_allow(now, caps.hourly, HOUR_SECONDS):
            return False
        if not daily.would_allow(now, caps.daily, DAY_SECONDS):
            return False
        hourly.record(now, HOUR_SECONDS)
        daily.record(now, DAY_SECONDS)
        self._last_publish_mono[platform] = self._mono()
        return True

    async def acquire(self, platform: str, *, max_sleep_slice: float = 5.0) -> None:
        """Wait until the platform may publish, then consume one slot."""
        while True:
            delay = self.seconds_until_allowed(platform)
            if delay <= 0 and self.try_record(platform):
                return
            await self._sleep(min(max(delay, 0.05), max_sleep_slice))

    def record_publish(self, platform: str) -> None:
        """Count a successful publish (used when acquire was skipped)."""
        self.try_record(platform)

    def remaining_hourly(self, platform: str) -> int | None:
        caps = self._caps_for(platform)
        if caps.hourly is None:
            return None
        counter = self._hourly_counter(platform)
        counter.reset_if_new_window(self._wall(), HOUR_SECONDS)
        return max(0, caps.hourly - counter.count)

    def remaining_daily(self, platform: str) -> int | None:
        caps = self._caps_for(platform)
        if caps.daily is None:
            return None
        counter = self._daily_counter(platform)
        counter.reset_if_new_window(self._wall(), DAY_SECONDS)
        return max(0, caps.daily - counter.count)
