"""Typer CLI application. All commands wired here."""

from __future__ import annotations

from pathlib import Path

import typer

from autoposter.cli.commands.backup import backup_command, restore_command
from autoposter.cli.commands.demo import demo_command
from autoposter.cli.commands.doctor import doctor_command
from autoposter.cli.commands.export_cmd import export_command
from autoposter.cli.commands.import_cmd import import_command
from autoposter.cli.commands.init import init_command
from autoposter.cli.commands.post import (
    post_add,
    post_cancel,
    post_delete,
    post_duplicate,
    post_edit,
    post_list,
    post_pause,
    post_reschedule,
    post_resume,
    post_retry,
    post_show,
)
from autoposter.cli.commands.run import run_command
from autoposter.cli.commands.setup import setup_command
from autoposter.cli.commands.version import version_command
from autoposter.cli.commands.web import web_command
from autoposter.cli.service import install_service, service_status, uninstall_service

app = typer.Typer(
    name="autoposter",
    help="AutoPoster Pro — self-hosted social media auto-poster & scheduler.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """AutoPoster Pro — self-hosted social media auto-poster & scheduler."""


@app.command()
def version() -> None:
    """Print the AutoPoster version."""
    version_command()


@app.command()
def init(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Create folders, config, database and encryption key."""
    init_command(config_path=config, data_dir=data_dir, force=force)


@app.command()
def setup(
    platform: str = typer.Argument(..., help="Platform to set up"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    account: str = typer.Option("default", "--account"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
) -> None:
    """Guided credential entry / OAuth browser flow for a platform."""
    setup_command(
        platform=platform,
        config_path=config,
        account_label=account,
        non_interactive=non_interactive,
    )


@app.command()
def doctor(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    platform: str | None = typer.Option(None, "--platform"),
) -> None:
    """Test every connection, config, permissions, clock."""
    doctor_command(config_path=config, platform=platform)


@app.command()
def post(
    action: str = typer.Argument(
        ..., help="add|list|show|edit|cancel|pause|resume|retry|reschedule|duplicate|delete"
    ),
    post_id: int | None = typer.Option(None, "--id"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    body: str | None = typer.Option(None, "--body"),
    platforms: str | None = typer.Option(None, "--platforms"),
    datetime_str: str | None = typer.Option(None, "--datetime"),
    timezone: str | None = typer.Option(None, "--timezone"),
    media: str | None = typer.Option(None, "--media"),
    status: str | None = typer.Option(None, "--status"),
    platform_filter: str | None = typer.Option(None, "--platform"),
    limit: int = typer.Option(50, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Manage posts in the queue."""
    if action == "add":
        post_add(
            config_path=config,
            body=body,
            platforms=[platforms] if platforms else None,
            datetime_str=datetime_str,
            timezone=timezone,
            media=[media] if media else None,
            dry_run=dry_run,
        )
    elif action == "list":
        post_list(config_path=config, status=status, platform=platform_filter, limit=limit)
    elif action == "show":
        if post_id is None:
            typer.echo("Error: --id is required for show")
            raise typer.Exit(1)
        post_show(config_path=config, post_id=post_id)
    elif action == "edit":
        if post_id is None:
            typer.echo("Error: --id is required for edit")
            raise typer.Exit(1)
        post_edit(config_path=config, post_id=post_id, body=body)
    elif action == "cancel":
        if post_id is None:
            typer.echo("Error: --id is required for cancel")
            raise typer.Exit(1)
        post_cancel(config_path=config, post_id=post_id)
    elif action == "pause":
        if post_id is None:
            typer.echo("Error: --id is required for pause")
            raise typer.Exit(1)
        post_pause(config_path=config, post_id=post_id)
    elif action == "resume":
        if post_id is None:
            typer.echo("Error: --id is required for resume")
            raise typer.Exit(1)
        post_resume(config_path=config, post_id=post_id)
    elif action == "retry":
        if post_id is None:
            typer.echo("Error: --id is required for retry")
            raise typer.Exit(1)
        post_retry(config_path=config, post_id=post_id)
    elif action == "reschedule":
        if post_id is None:
            typer.echo("Error: --id is required for reschedule")
            raise typer.Exit(1)
        post_reschedule(
            config_path=config,
            post_id=post_id,
            datetime_str=datetime_str,
            timezone=timezone,
        )
    elif action == "duplicate":
        if post_id is None:
            typer.echo("Error: --id is required for duplicate")
            raise typer.Exit(1)
        post_duplicate(config_path=config, post_id=post_id)
    elif action == "delete":
        if post_id is None:
            typer.echo("Error: --id is required for delete")
            raise typer.Exit(1)
        post_delete(config_path=config, post_id=post_id, force=force)
    else:
        typer.echo(f"Error: unknown action '{action}'")
        raise typer.Exit(1)


@app.command(name="import")
def import_cmd(
    file: Path = typer.Argument(..., help="CSV or JSON file to import"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    validate_only: bool = typer.Option(False, "--validate-only"),
    skip_invalid: bool = typer.Option(False, "--skip-invalid"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Import posts from a CSV or JSON file."""
    import_command(
        file=file,
        config_path=config,
        validate_only=validate_only,
        skip_invalid=skip_invalid,
        dry_run=dry_run,
    )


@app.command(name="export")
def export_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    format: str = typer.Option("csv", "--format", "-f"),
    out: Path | None = typer.Option(None, "--out", "-o"),
    status: str | None = typer.Option(None, "--status"),
    platform: str | None = typer.Option(None, "--platform"),
) -> None:
    """Export the queue to CSV or JSON."""
    export_command(config_path=config, format=format, out=out, status=status, platform=platform)


@app.command()
def backup(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    out: Path | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Create a full backup (database + config + media)."""
    backup_command(config_path=config, out=out)


@app.command()
def restore(
    archive: Path = typer.Argument(..., help="Backup archive to restore"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Restore from a backup archive."""
    restore_command(archive=archive, config_path=config, force=force)


@app.command()
def run(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    once: bool = typer.Option(False, "--once"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Start the scheduler in the foreground."""
    run_command(config_path=config, dry_run=dry_run, once=once, debug=debug)


@app.command()
def demo(
    posts: int = typer.Option(3, "--posts", "-n"),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
) -> None:
    """Run the full pipeline against the fake platform with sample content."""
    demo_command(posts=posts, config_path=config)


@app.command()
def web(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    password: str | None = typer.Option(None, "--password"),
) -> None:
    """Start the optional local web dashboard."""
    web_command(config_path=config, host=host, port=port, password=password)


@app.command(name="service")
def service_cmd(
    action: str = typer.Argument("install", help="install|uninstall|status"),
) -> None:
    """Install, uninstall or check the background service."""
    if action == "install":
        result = install_service(workdir=Path.cwd())
        typer.echo(result)
    elif action == "uninstall":
        result = uninstall_service()
        typer.echo(result)
    elif action == "status":
        result = service_status()
        typer.echo(result)
    else:
        typer.echo(f"Error: unknown action '{action}'")
        raise typer.Exit(1)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    main()
