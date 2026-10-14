"""Export the queue to CSV or JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from autoposter.core.models import Post


def export_csv(posts: list[Post], path: Path) -> None:
    """Export posts to CSV."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "body", "scheduled_at", "timezone", "status", "platforms"])
        for post in posts:
            platforms = ",".join(t.platform for t in post.targets)
            writer.writerow([
                post.id,
                post.body,
                post.scheduled_at_utc.isoformat(),
                post.timezone,
                post.status.value,
                platforms,
            ])


def export_json(posts: list[Post], path: Path) -> None:
    """Export posts to JSON."""
    data = []
    for post in posts:
        data.append({
            "id": post.id,
            "body": post.body,
            "scheduled_at": post.scheduled_at_utc.isoformat(),
            "timezone": post.timezone,
            "status": post.status.value,
            "platforms": [t.platform for t in post.targets],
        })
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
