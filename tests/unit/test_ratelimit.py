"""Rate limiter tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from autoposter.config.schema import PlatformCaps
from autoposter.core.ratelimit import RateLimiter


def _caps(hourly: int | None = 2, daily: int | None = 10) -> PlatformCaps:
    return PlatformCaps(hourly=hourly, daily=daily, delay_between_posts_seconds=(0.0, 0.0))


def test_allows_under_caps() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    limiter = RateLimiter(lambda p: _caps(hourly=5), wall_clock=lambda: now)
    assert limiter.seconds_until_allowed("x") == 0.0
    assert limiter.try_record("x")
    assert limiter.remaining_hourly("x") == 4


def test_blocks_at_hourly_cap_then_window_rolls() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    limiter = RateLimiter(lambda p: _caps(hourly=2, daily=None), wall_clock=lambda: now)
    assert limiter.try_record("x")
    assert limiter.try_record("x")
    assert not limiter.try_record("x")
    assert limiter.seconds_until_allowed("x") > 0
    # Next hour resets the window.
    later = now + timedelta(hours=1)
    limiter = RateLimiter(
        lambda p: _caps(hourly=2, daily=None),
        wall_clock=lambda: later,
    )
    # Share state by mutating clock of same limiter instead:
    state = RateLimiter(lambda p: _caps(hourly=2, daily=None), wall_clock=lambda: now)
    state.try_record("x")
    state.try_record("x")
    assert not state.try_record("x")
    # Move the wall clock forward by re-binding — rebuild with same dicts is
    # not possible; simulate by creating limiter with mutable now.
    clock = {"now": now}
    lim = RateLimiter(lambda p: _caps(hourly=2), wall_clock=lambda: clock["now"])
    assert lim.try_record("x")
    assert lim.try_record("x")
    assert not lim.try_record("x")
    clock["now"] = now + timedelta(hours=1)
    assert lim.try_record("x")


def test_daily_cap() -> None:
    clock = {"now": datetime(2026, 10, 14, 10, 0, tzinfo=UTC)}
    lim = RateLimiter(lambda p: _caps(hourly=None, daily=3), wall_clock=lambda: clock["now"])
    for _ in range(3):
        assert lim.try_record("x")
    assert not lim.try_record("x")
    clock["now"] = clock["now"] + timedelta(days=1)
    assert lim.try_record("x")


def test_inter_post_delay_uses_low_bound() -> None:
    mono = {"t": 0.0}
    caps = PlatformCaps(hourly=None, daily=None, delay_between_posts_seconds=(2.0, 5.0))
    lim = RateLimiter(lambda p: caps, mono_clock=lambda: mono["t"])
    assert lim.seconds_until_allowed("x") == 0.0
    lim.record_publish("x")
    # Immediately after, low bound (2s) must wait.
    assert lim.seconds_until_allowed("x") == pytest.approx(2.0, abs=0.1)
    mono["t"] = 2.5
    assert lim.seconds_until_allowed("x") == 0.0


async def test_acquire_sleeps_until_slot() -> None:
    mono = {"t": 0.0}
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        mono["t"] += seconds

    caps = PlatformCaps(hourly=None, daily=None, delay_between_posts_seconds=(0.2, 0.2))
    lim = RateLimiter(lambda p: caps, mono_clock=lambda: mono["t"], sleep=fake_sleep)
    await lim.acquire("x")
    assert lim.try_record.__self__ is lim  # sanity
    await lim.acquire("x")  # must sleep at least once
    assert slept, "expected acquire to sleep for the inter-post delay"


def test_unlimited_caps_always_allow() -> None:
    caps = PlatformCaps(hourly=None, daily=None, delay_between_posts_seconds=(0.0, 0.0))
    lim = RateLimiter(lambda p: caps)
    for _ in range(100):
        assert lim.try_record("fake")
    assert lim.remaining_hourly("fake") is None
    assert lim.remaining_daily("fake") is None
