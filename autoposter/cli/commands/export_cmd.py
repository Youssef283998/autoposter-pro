"""autoposter export — export queue to CSV/JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from autoposter.cli.rich_helpers import print_success
from autoposter.core import queue as q
from autoposter.core.db import init_database, make_engine, session_scope
from autoposter.core.models import PostStatus


def export_command(
    *,
    config_path: Path = Path("config.yaml"),
    format: str = "csv",
    out: Path | None = None,
    status: str | None = None,
    platform: str | None = None,
) -> None:
    """Export the queue to CSV or JSON."""
    from autoposter.config.settings import load_settings

    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()
    engine = make_engine(paths.db_path)
    init_database(engine)

    try:
        post_status = PostStatus(status) if status else None
        with session_scope(engine) as session:
            posts = q.list_posts(session, status=post_status, platform=platform, limit=10000)

        if not posts:
            print_success("No posts to export")
            return

        if out is None:
            out = Path(f"autoposter-export.{format}")

        if format == "csv":
            _export_csv(posts, out)
        elif format == "json":
            _export_json(posts, out)
        else:
            raise ValueError(f"Unknown format: {format}")

        print_success(f"Exported {len(posts)} post(s) to {out}")
    finally:
        engine.dispose()


def _export_csv(posts: list, out: Path) -> None:
    """Export posts to CSV."""
    with open(out, "w", newline="", encoding="utf-8") as fh:
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


def _export_json(posts: list, out: Path) -> None:
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
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
