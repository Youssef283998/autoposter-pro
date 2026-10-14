"""Timezone, DST and recurrence tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from autoposter.core.errors import ConfigError
from autoposter.utils.timeutil import (
    clock_jump_detected,
    ensure_aware_utc,
    format_in_zone,
    get_zone,
    humanize_in,
    is_within_quiet_hours,
    next_occurrence,
    parse_recurrence,
    parse_user_datetime,
    utcnow,
    zoned_wall_time,
)


def test_get_zone_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="Unknown timezone"):
        get_zone("Not/AZone")


def test_parse_user_datetime_local_to_utc() -> None:
    # 2026-10-14 09:00 in Berlin (CEST, UTC+2) = 07:00 UTC
    result = parse_user_datetime("2026-10-14 09:00", "Europe/Berlin")
    assert result == datetime(2026, 10, 14, 7, 0, tzinfo=UTC)


def test_parse_user_datetime_dst_spring_forward_gap() -> None:
    # Europe/Berlin springs forward 2026-03-29 02:00 -> 03:00.
    # 02:30 does not exist; zoneinfo resolves with fold=0 (CET, UTC+1) → 01:30 UTC.
    result = parse_user_datetime("2026-03-29 02:30", "Europe/Berlin")
    assert result == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_parse_user_datetime_dst_fall_back_ambiguous() -> None:
    # Europe/Berlin falls back 2026-10-25 03:00 -> 02:00; 02:30 happens twice.
    # fold=0 picks the first (CEST, UTC+2) → 00:30 UTC.
    result = parse_user_datetime("2026-10-25 02:30", "Europe/Berlin")
    assert result == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_parse_user_datetime_with_iso_offset() -> None:
    result = parse_user_datetime("2026-10-14T09:00:00+02:00", "UTC")
    assert result == datetime(2026, 10, 14, 7, 0, tzinfo=UTC)


def test_parse_user_datetime_invalid() -> None:
    with pytest.raises(ConfigError, match="Could not parse"):
        parse_user_datetime("tomorrow at noon", "UTC")


def test_parse_user_datetime_empty() -> None:
    with pytest.raises(ConfigError, match="Empty datetime"):
        parse_user_datetime("  ", "UTC")


def test_ensure_aware_utc_naive_with_zone() -> None:
    result = ensure_aware_utc(datetime(2026, 6, 1, 12, 0), tz_name="America/New_York")
    # EDT is UTC-4 in June
    assert result == datetime(2026, 6, 1, 16, 0, tzinfo=UTC)


def test_ensure_aware_utc_already_aware_converts() -> None:
    localized = datetime(2026, 6, 1, 12, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert ensure_aware_utc(localized) == datetime(2026, 6, 1, 16, 0, tzinfo=UTC)


def test_zoned_wall_time() -> None:
    wall = zoned_wall_time(datetime(2026, 10, 14, 7, 0, tzinfo=UTC), "Europe/Berlin")
    assert wall == datetime(2026, 10, 14, 9, 0)


def test_humanize_in_future_and_past() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert humanize_in(now + timedelta(hours=2), now=now) == "in 2 hours"
    assert humanize_in(now - timedelta(minutes=30), now=now) == "30 minutes ago"


def test_humanize_in_less_than_a_minute() -> None:
    now = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    assert humanize_in(now + timedelta(seconds=10), now=now) == "in less than a minute"


def test_format_in_zone() -> None:
    instant = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    now = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)
    out = format_in_zone(instant, "Europe/Berlin", now=now)
    # Berlin is CEST (UTC+2) in October
    assert "09:00" in out
    assert "Europe/Berlin" in out
    assert "in 2 hours" in out


def test_quiet_hours_wrapping_midnight() -> None:
    # 22:00-07:00 Europe/Berlin (UTC+2): 20:30 UTC = 22:30 local -> inside
    instant = datetime(2026, 10, 14, 20, 30, tzinfo=UTC)
    assert is_within_quiet_hours(instant, "Europe/Berlin", "22:00", "07:00")
    # 08:00 UTC = 10:00 local → outside
    assert not is_within_quiet_hours(
        datetime(2026, 10, 14, 8, 0, tzinfo=UTC), "Europe/Berlin", "22:00", "07:00"
    )


def test_quiet_hours_same_start_end_is_disabled() -> None:
    assert not is_within_quiet_hours(utcnow(), "UTC", "10:00", "10:00")


def test_daily_recurrence_expansion() -> None:
    anchor = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    times = parse_recurrence("daily", anchor_utc=anchor, tz_name="UTC")
    assert len(times) >= 2
    assert times[0] == datetime(2026, 10, 15, 7, 0, tzinfo=UTC)
    assert times[1] == datetime(2026, 10, 16, 7, 0, tzinfo=UTC)


def test_weekly_recurrence_defaults_to_anchor_weekday() -> None:
    anchor = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)  # a Wednesday
    times = parse_recurrence("weekly", anchor_utc=anchor, tz_name="UTC")
    assert times[0].weekday() == anchor.weekday()
    assert (times[0] - anchor).days == 7


def test_weekly_with_day_and_time() -> None:
    anchor = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    times = parse_recurrence("weekly:MON@09:00", anchor_utc=anchor, tz_name="UTC")
    # Next Monday after Wed Oct 14 is Oct 19
    assert times[0] == datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def test_weekly_invalid_day() -> None:
    with pytest.raises(ConfigError, match="weekday"):
        parse_recurrence("weekly:XXX@09:00", anchor_utc=utcnow(), tz_name="UTC")


def test_cron_rrule_expansion() -> None:
    anchor = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)  # Wednesday
    times = parse_recurrence(
        "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=9;BYMINUTE=0",
        anchor_utc=anchor,
        tz_name="UTC",
    )
    # Wednesday 09:00 is still ahead of the 07:00 anchor, so it comes first.
    assert times[0] == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    assert times[1] == datetime(2026, 10, 16, 9, 0, tzinfo=UTC)  # Friday
    assert all(t.hour == 9 for t in times[:5])


def test_daily_recurrence_dst_stable_wall_time() -> None:
    # Anchor the day before Europe/Berlin springs forward; daily local 09:00
    # should stay 09:00 wall time and shift its UTC offset after the change.
    anchor = datetime(2026, 3, 28, 8, 0, tzinfo=UTC)  # 09:00 Berlin (CET)
    times = parse_recurrence("daily", anchor_utc=anchor, tz_name="Europe/Berlin")
    before = times[0]  # Mar 29 09:00 CEST = 07:00 UTC (transition happened that morning)
    after_wall = before.astimezone(get_zone("Europe/Berlin"))
    assert after_wall.hour == 9


def test_next_occurrence() -> None:
    after = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
    result = next_occurrence("daily", after_utc=after, tz_name="UTC")
    assert result == datetime(2026, 10, 15, 7, 0, tzinfo=UTC)


def test_clock_jump_detection() -> None:
    previous = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
    jumped = previous + timedelta(hours=3)
    assert clock_jump_detected(previous, jumped, timedelta(minutes=10))
    normal = previous + timedelta(seconds=5)
    assert not clock_jump_detected(previous, normal, timedelta(minutes=10))
