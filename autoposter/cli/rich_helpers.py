"""Rich output helpers for the CLI: tables, human times, error rendering."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console
from rich.table import Table

from autoposter.core.errors import AutoposterError
from autoposter.core.models import Post, PostStatus, TargetStatus
from autoposter.utils.timeutil import format_in_zone, humanize_in

console = Console()


def print_error(error: AutoposterError) -> None:
    """Print a user-friendly error with fix hint."""
    console.print(f"[red]Error:[/red] {error.message}")
    if error.fix:
        console.print(f"[yellow]Fix:[/yellow] {error.fix}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]✓[/green] {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"[yellow]![/yellow] {message}")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]i[/blue] {message}")


def post_table(posts: list[Post], *, now: datetime | None = None) -> Table:
    """Build a rich table of posts."""
    table = Table(title="Posts")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Status", style="bold")
    table.add_column("When", style="green")
    table.add_column("Platforms", style="magenta")
    table.add_column("Body", style="white", max_width=50)
    for post in posts:
        status_style = _status_style(post.status)
        platforms = ", ".join(t.platform for t in post.targets)
        when = format_in_zone(post.scheduled_at_utc, post.timezone, now=now)
        table.add_row(
            str(post.id or "?"),
            f"[{status_style}]{post.status.value}[/{status_style}]",
            when,
            platforms,
            post.body[:50] + ("…" if len(post.body) > 50 else ""),
        )
    return table


def target_table(post: Post) -> Table:
    """Build a rich table of targets for one post."""
    table = Table(title=f"Targets for post {post.id}")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Platform", style="magenta")
    table.add_column("Status", style="bold")
    table.add_column("Attempts", justify="right")
    table.add_column("Next attempt", style="green")
    table.add_column("Error", style="red", max_width=40)
    for target in post.targets:
        status_style = _target_status_style(target.status)
        next_attempt = (
            humanize_in(target.next_attempt_at, now=datetime.now(target.next_attempt_at.tzinfo))
            if target.next_attempt_at
            else "-"
        )
        table.add_row(
            str(target.id or "?"),
            target.platform,
            f"[{status_style}]{target.status.value}[/{status_style}]",
            str(target.attempts),
            next_attempt,
            (target.last_error or "")[:40],
        )
    return table


def validation_table(rows: list[dict]) -> Table:
    """Build a rich table of import validation problems."""
    table = Table(title="Validation Report")
    table.add_column("Row", style="cyan", justify="right")
    table.add_column("Field", style="magenta")
    table.add_column("Problem", style="red")
    for row in rows:
        table.add_row(
            str(row.get("row", "?")),
            str(row.get("field", "?")),
            str(row.get("message", "")),
        )
    return table


def _status_style(status: PostStatus) -> str:
    styles = {
        PostStatus.DRAFT: "dim",
        PostStatus.SCHEDULED: "green",
        PostStatus.PUBLISHING: "yellow",
        PostStatus.PUBLISHED: "bold green",
        PostStatus.FAILED: "red",
        PostStatus.CANCELLED: "dim",
        PostStatus.MISSED: "yellow",
        PostStatus.PAUSED: "blue",
    }
    return styles.get(status, "white")


def _target_status_style(status: TargetStatus) -> str:
    styles = {
        TargetStatus.SCHEDULED: "green",
        TargetStatus.PUBLISHING: "yellow",
        TargetStatus.PUBLISHED: "bold green",
        TargetStatus.FAILED: "red",
        TargetStatus.CANCELLED: "dim",
        TargetStatus.MISSED: "yellow",
        TargetStatus.PAUSED: "blue",
    }
    return styles.get(status, "white")
