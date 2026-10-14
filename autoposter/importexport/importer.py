"""Bulk import: CSV and JSON with validation-then-schedule."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path

from autoposter.core.errors import ConfigError


def load_posts_file(path: Path) -> list[dict[str, str]]:
    """Load posts from a CSV or JSON file.

    CSV columns: text, platforms, datetime, timezone, media, thread_group, variants
    JSON: list of objects with the same keys.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path)
    if suffix == ".json":
        return _load_json(path)
    raise ConfigError(
        f"Unsupported file type: {suffix}",
        fix="Use .csv or .json. See docs/csv-format.md for the expected format.",
    )


def _load_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _load_json(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [dict(item) for item in data]
    if isinstance(data, dict) and "posts" in data:
        return [dict(item) for item in data["posts"]]
    raise ConfigError(
        "JSON must be a list of posts or an object with a 'posts' key.",
        fix='Example: [{"text": "Hello", "platforms": "telegram", "datetime": "2026-10-14 09:00"}]',
    )


def validate_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """Validate all rows. Returns a list of problems (empty = all valid)."""
    problems: list[dict[str, str]] = []
    for i, row in enumerate(rows, start=2):
        text = (row.get("text") or "").strip()
        if not text:
            problems.append({"row": str(i), "field": "text", "message": "empty text"})
        platforms = (row.get("platforms") or "").strip()
        if not platforms:
            problems.append({"row": str(i), "field": "platforms", "message": "no platforms"})
        dt = (row.get("datetime") or "").strip()
        if not dt:
            problems.append({"row": str(i), "field": "datetime", "message": "no datetime"})
    return problems


def parse_row(row: dict[str, str], *, default_timezone: str) -> dict[str, object]:
    """Parse one valid row into scheduling parameters."""
    from autoposter.utils.timeutil import parse_user_datetime

    tz = (row.get("timezone") or "").strip() or default_timezone
    dt_str = (row.get("datetime") or "").strip()
    scheduled = parse_user_datetime(dt_str, tz)
    platforms = [p.strip() for p in (row.get("platforms") or "").split(",") if p.strip()]
    media = [m.strip() for m in (row.get("media") or "").split(";") if m.strip()]
    variants: dict[str, str] = {}
    variants_str = (row.get("variants") or "").strip()
    if variants_str:
        # Format: "telegram:Hello|discord:Hi there"
        for pair in variants_str.split("|"):
            if ":" in pair:
                key, value = pair.split(":", 1)
                variants[key.strip()] = value.strip()
    return {
        "body": (row.get("text") or "").strip(),
        "scheduled_at_utc": scheduled,
        "timezone": tz,
        "platforms": platforms,
        "media": media,
        "thread_group": (row.get("thread_group") or "").strip() or None,
        "variants": variants,
    }
