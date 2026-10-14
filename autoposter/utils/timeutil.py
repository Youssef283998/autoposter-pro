"""Timezone-aware scheduling helpers (IANA zones, DST-safe parsing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from autoposter.core.errors import ConfigError

UTC = timezone.utc


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def get_zone(tz_name: str) -> ZoneInfo:
    """Resolve an IANA zone name with a friendly error on typos."""
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"Unknown timezone {tz_name!r}.",
            fix="Use an IANA name such as UTC, Europe/Berlin, America/New_York or Africa/Casablanca.",  # noqa: E501
        ) from exc


def ensure_aware_utc(value: datetime, *, tz_name: str | None = None) -> datetime:
    """Normalize a datetime to aware UTC.

    A naive datetime is interpreted as wall time in ``tz_name`` (or UTC when
    ``tz_name`` is None). Ambiguous DST fall-back times resolve with fold=0
    (the earlier offset); nonexistent spring-forward times resolve with the
    pre-transition offset (Python zoneinfo default).
    """
    if value.tzinfo is None:
        zone = get_zone(tz_name) if tz_name else timezone.utc
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC)


def zoned_wall_time(value_utc: datetime, tz_name: str) -> datetime:
    """Convert a UTC instant to naive wall time in ``tz_name``."""
    return value_utc.astimezone(get_zone(tz_name)).replace(tzinfo=None)


def parse_user_datetime(value: str, tz_name: str) -> datetime:
    """Parse a user-supplied local datetime string into aware UTC.

    Accepts ``YYYY-MM-DD HH:MM[:SS]`` and ISO-8601 with a zone offset
    (offset wins over ``tz_name``). Returns aware UTC.
    """
    text = value.strip()
    if not text:
        raise ConfigError(
            "Empty datetime.",
            fix='Use a format like "2026-10-14 09:00".',
        )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ConfigError(
                f"Could not parse datetime {value!r}.",
                fix='Use "YYYY-MM-DD HH:MM" (local to the post timezone) or full ISO-8601.',
            ) from None
    return ensure_aware_utc(parsed, tz_name=tz_name)


def humanize_in(
    target_utc: datetime,
    *,
    now: datetime | None = None,
    past_prefix: str = "",
) -> str:
    """Human phrase for a UTC instant relative to now, e.g. ``in 2 hours``."""
    current = now or utcnow()
    delta = target_utc - current
    seconds = delta.total_seconds()
    if seconds < 0:
        phrase = _duration_phrase(-seconds)
        return f"{past_prefix}{phrase} ago"
    return f"in {_duration_phrase(seconds)}"


def _duration_phrase(seconds: float) -> str:
    if seconds < 60:
        return "less than a minute"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute" + ("s" if minutes != 1 else "")
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour" + ("s" if hours != 1 else "")
    days = hours // 24
    return f"{days} day" + ("s" if days != 1 else "")


def format_in_zone(
    value_utc: datetime,
    tz_name: str,
    *,
    humanize_now: bool = True,
    now: datetime | None = None,
) -> str:
    """Format a UTC instant for humans, e.g. ``Oct 14 09:00 Europe/Berlin``."""
    local = value_utc.astimezone(get_zone(tz_name))
    stamp = local.strftime("%b %d %H:%M")
    if humanize_now:
        return f"{humanize_in(value_utc, now=now)}, {stamp} {tz_name}"
    return f"{stamp} {tz_name}"


def is_within_quiet_hours(
    value_utc: datetime,
    tz_name: str,
    start_hhmm: str,
    end_hhmm: str,
) -> bool:
    """Whether a UTC instant falls inside a daily quiet window in ``tz_name``.

    The window is half-open: ``[start, end)`` in local wall time. Windows that
    wrap midnight (e.g. 22:00-07:00) are supported.
    """
    local = value_utc.astimezone(get_zone(tz_name))
    current = local.hour * 60 + local.minute
    start = _parse_hhmm(start_hhmm)
    end = _parse_hhmm(end_hhmm)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _parse_hhmm(value: str) -> int:
    try:
        hour_s, minute_s = value.split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError as exc:
        raise ConfigError(
            f"Invalid time {value!r}; expected HH:MM.",
            fix='Use 24-hour HH:MM, for example "22:00".',
        ) from exc
    return hour * 60 + minute


def parse_recurrence(
    rule: str,
    *,
    anchor_utc: datetime,
    tz_name: str,
) -> list[datetime]:
    """Expand a recurrence rule into upcoming UTC fire times.

    Supported forms:
      * ``daily`` — every day at the anchor's local wall time
      * ``weekly`` — same weekday and wall time as the anchor
      * ``weekly:MON@09:00`` — Mondays at 09:00 local
      * any dateutil/rrule RRULE string, e.g.
        ``RRULE:FREQ=DAILY;BYDAY=MO,WE,FR;BYHOUR=9;BYMINUTE=0``

    Returns the next 100 occurrences strictly after ``anchor_utc``.
    """
    cleaned = rule.strip()
    zone = get_zone(tz_name)
    local_anchor = anchor_utc.astimezone(zone)

    if cleaned == "daily":
        return _expand_rrule(
            f"FREQ=DAILY;BYHOUR={local_anchor.hour};BYMINUTE={local_anchor.minute}",
            anchor_utc=anchor_utc,
            tz_name=tz_name,
        )
    if cleaned == "weekly":
        day_codes = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
        weekday = day_codes[local_anchor.weekday()]
        return _expand_rrule(
            f"FREQ=WEEKLY;BYDAY={weekday};BYHOUR={local_anchor.hour};"
            f"BYMINUTE={local_anchor.minute}",
            anchor_utc=anchor_utc,
            tz_name=tz_name,
        )
    if cleaned.startswith("weekly:"):
        remainder = cleaned[len("weekly:") :]
        try:
            day_code, time_code = remainder.split("@", 1)
            hour_s, minute_s = time_code.split(":", 1)
            # Accept "MO" or "MON"/"MONDAY" style codes.
            day = day_code.strip().upper()[:2]
            hour, minute = int(hour_s), int(minute_s)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid weekly rule {rule!r}.",
                fix='Use "weekly:MON@09:00" (day codes MO, TU, WE, TH, FR, SA, SU).',
            ) from exc
        if day not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}:
            raise ConfigError(
                f"Invalid weekday in rule {rule!r}.",
                fix='Day codes are MO, TU, WE, TH, FR, SA, SU — e.g. "weekly:FRI@18:30".',
            )
        return _expand_rrule(
            f"FREQ=WEEKLY;BYDAY={day};BYHOUR={hour};BYMINUTE={minute}",
            anchor_utc=anchor_utc,
            tz_name=tz_name,
        )

    rr = cleaned.upper()
    if not rr.startswith("RRULE:"):
        rr = f"RRULE:{rr}"
    return _expand_rrule(rr[len("RRULE:") :], anchor_utc=anchor_utc, tz_name=tz_name)


def _expand_rrule(
    body: str,
    *,
    anchor_utc: datetime,
    tz_name: str,
    count: int = 100,
) -> list[datetime]:
    zone = get_zone(tz_name)
    local_anchor = anchor_utc.astimezone(zone).replace(tzinfo=None)
    try:
        rule_obj = rrulestr(body, dtstart=local_anchor, cache=False)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"Invalid recurrence rule: {body!r}",
            fix="See https://dateutil.readthedocs.io/en/stable/rrule.html for RRULE syntax.",
        ) from exc
    results: list[datetime] = []
    for occurrence in rule_obj:
        if occurrence <= local_anchor:
            continue
        results.append(ensure_aware_utc(occurrence, tz_name=tz_name))
        if len(results) >= count:
            break
    return results


def next_occurrence(rule: str, *, after_utc: datetime, tz_name: str) -> datetime | None:
    """Single next fire time strictly after ``after_utc``, or None."""
    upcoming = parse_recurrence(rule, anchor_utc=after_utc, tz_name=tz_name)
    return upcoming[0] if upcoming else None


def clock_jump_detected(previous_utc: datetime, now: datetime, threshold: timedelta) -> bool:
    """Detect a system clock jump larger than ``threshold`` in either direction."""
    delta = now - previous_utc
    return abs(delta.total_seconds()) > threshold.total_seconds()
