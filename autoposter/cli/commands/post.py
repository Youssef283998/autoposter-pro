"""autoposter post — queue management."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import typer

from autoposter.cli.rich_helpers import (
    console,
    post_table,
    print_error,
    print_info,
    print_success,
    print_warning,
    target_table,
)
from autoposter.core import queue as q
from autoposter.core.db import init_database, make_engine, session_scope
from autoposter.core.errors import ConfigError
from autoposter.core.models import PostStatus
from autoposter.utils.timeutil import parse_user_datetime, utcnow


def _get_engine(config_path: Path):
    from autoposter.config.settings import load_settings

    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()
    engine = make_engine(paths.db_path)
    init_database(engine)
    return engine, settings


def post_add(
    *,
    config_path: Path = Path("config.yaml"),
    body: str | None = None,
    platforms: list[str] | None = None,
    datetime_str: str | None = None,
    timezone: str | None = None,
    media: list[str] | None = None,
    dry_run: bool = False,
    non_interactive: bool = False,
) -> None:
    """Add a post to the queue."""
    engine, settings = _get_engine(config_path)
    try:
        # Gather inputs.
        if body is None:
            body = typer.prompt("Post text")
        if not platforms:
            platforms_str = typer.prompt("Platforms (comma-separated)", default="fake")
            platforms = [p.strip() for p in platforms_str.split(",") if p.strip()]
        if datetime_str is None:
            datetime_str = typer.prompt("When (YYYY-MM-DD HH:MM)", default="in 1 hour")
        if timezone is None:
            timezone = typer.prompt("Timezone", default=settings.config.default_timezone)

        # Parse time.
        if datetime_str.startswith("in "):
            # Relative time: "in 2 hours", "in 30 minutes"
            parts = datetime_str[3:].strip().split()
            if len(parts) == 2:
                amount, unit = parts
                amount = int(amount)
                if unit.startswith("minute"):
                    delta = timedelta(minutes=amount)
                elif unit.startswith("hour"):
                    delta = timedelta(hours=amount)
                elif unit.startswith("day"):
                    delta = timedelta(days=amount)
                else:
                    delta = timedelta(hours=amount)
                scheduled = utcnow() + delta
            else:
                raise ConfigError(f"Cannot parse relative time: {datetime_str}")
        else:
            scheduled = parse_user_datetime(datetime_str, timezone)

        # Resolve accounts.
        from autoposter.auth.crypto import resolve_encryption_key
        from autoposter.auth.tokens import TokenStore

        key = resolve_encryption_key(settings.secrets.encryption_key, settings.paths.data_dir)
        store = TokenStore(key)
        targets = []
        with session_scope(engine) as session:
            for platform in platforms:
                account = store.get_account(session, platform)
                if account is None:
                    print_warning(f"No account for {platform}. Run: autoposter setup {platform}")
                    continue
                targets.append(q.TargetSpec(platform=platform, account_id=account.id))

        if not targets:
            print_error(ConfigError("No valid platform accounts found."))
            raise typer.Exit(1)

        # Build media items.
        media_items = []
        if media:
            from autoposter.media.store import MediaStore

            store_path = MediaStore(settings.paths.media_dir)
            for m in media:
                path = Path(m).expanduser().resolve()
                if not path.exists():
                    print_warning(f"Media file not found: {path}")
                    continue
                item = store_path.store(path)
                media_items.append(item)

        if dry_run:
            print_info("Dry run — would schedule:")
            console.print(f"  Body: {body[:80]}")
            console.print(f"  Time: {scheduled.isoformat()} ({timezone})")
            console.print(f"  Platforms: {', '.join(platforms)}")
            console.print(f"  Media: {len(media_items)} file(s)")
            return

        with session_scope(engine) as session:
            post = q.add_post(
                session,
                body=body,
                scheduled_at_utc=scheduled,
                timezone=timezone,
                targets=targets,
                media=media_items,
            )
        print_success(f"Post {post.id} scheduled")
    finally:
        engine.dispose()


def post_list(
    *,
    config_path: Path = Path("config.yaml"),
    status: str | None = None,
    platform: str | None = None,
    limit: int = 50,
) -> None:
    """List posts."""
    engine, _settings = _get_engine(config_path)
    try:
        post_status = PostStatus(status) if status else None
        with session_scope(engine) as session:
            posts = q.list_posts(
                session,
                status=post_status,
                platform=platform,
                limit=limit,
            )
        if not posts:
            print_info("No posts found")
            return
        console.print(post_table(posts))
    finally:
        engine.dispose()


def post_show(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Show one post with targets."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            post = q.get_post(session, post_id)
        if post is None:
            print_error(ConfigError(f"Post {post_id} not found"))
            raise typer.Exit(1)
        console.print(post_table([post]))
        console.print(target_table(post))
    finally:
        engine.dispose()


def post_edit(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
    body: str | None = None,
) -> None:
    """Edit a post body."""
    engine, _ = _get_engine(config_path)
    try:
        if body is None:
            body = typer.prompt("New text")
        with session_scope(engine) as session:
            post = q.update_post_body(session, post_id, body=body)
        print_success(f"Post {post.id} updated")
    finally:
        engine.dispose()


def post_cancel(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Cancel a post."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            q.set_post_status(session, post_id, PostStatus.CANCELLED)
        print_success(f"Post {post_id} cancelled")
    finally:
        engine.dispose()


def post_pause(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Pause a post."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            q.set_post_status(session, post_id, PostStatus.PAUSED)
        print_success(f"Post {post_id} paused")
    finally:
        engine.dispose()


def post_resume(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Resume a paused post."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            q.set_post_status(session, post_id, PostStatus.SCHEDULED)
        print_success(f"Post {post_id} resumed")
    finally:
        engine.dispose()


def post_retry(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Retry failed targets of a post."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            count = q.retry_failed_targets_for_post(session, post_id)
        print_success(f"Re-queued {count} target(s)")
    finally:
        engine.dispose()


def post_reschedule(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
    datetime_str: str | None = None,
    timezone: str | None = None,
) -> None:
    """Reschedule a post."""
    engine, settings = _get_engine(config_path)
    try:
        if datetime_str is None:
            datetime_str = typer.prompt("New time (YYYY-MM-DD HH:MM)")
        if timezone is None:
            timezone = typer.prompt("Timezone", default=settings.config.default_timezone)
        new_time = parse_user_datetime(datetime_str, timezone)
        with session_scope(engine) as session:
            q.reschedule_post(session, post_id, new_time)
        print_success(f"Post {post_id} rescheduled")
    finally:
        engine.dispose()


def post_duplicate(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
) -> None:
    """Duplicate a post."""
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            original = q.get_post(session, post_id)
            if original is None:
                print_error(ConfigError(f"Post {post_id} not found"))
                raise typer.Exit(1)
            post = q.add_post(
                session,
                body=original.body,
                scheduled_at_utc=utcnow() + timedelta(hours=1),
                timezone=original.timezone,
                targets=[
                    q.TargetSpec(
                        platform=t.platform,
                        account_id=t.account_id,
                        variant_body=t.variant_body,
                    )
                    for t in original.targets
                ],
                media=original.media,
                recurrence_rule=original.recurrence_rule,
            )
        print_success(f"Post {post.id} created (duplicate of {post_id})")
    finally:
        engine.dispose()


def post_delete(
    *,
    config_path: Path = Path("config.yaml"),
    post_id: int,
    force: bool = False,
) -> None:
    """Delete a post."""
    if not force:
        typer.confirm(f"Delete post {post_id}?", abort=True)
    engine, _ = _get_engine(config_path)
    try:
        with session_scope(engine) as session:
            q.delete_post(session, post_id)
        print_success(f"Post {post_id} deleted")
    finally:
        engine.dispose()
