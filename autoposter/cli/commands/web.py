"""autoposter web — start the optional local dashboard."""

from __future__ import annotations

from pathlib import Path

import typer

from autoposter.cli.rich_helpers import print_error, print_success
from autoposter.core.errors import ConfigError


def web_command(
    *,
    config_path: Path = Path("config.yaml"),
    host: str | None = None,
    port: int | None = None,
    password: str | None = None,
) -> None:
    """Start the local web dashboard."""
    try:
        from autoposter.config.settings import load_settings

        settings = load_settings(config_path, require_config=True)
    except ConfigError as exc:
        print_error(exc)
        raise typer.Exit(1) from exc

    host = host or settings.config.web.host
    port = port or settings.config.web.port
    password = password or settings.secrets.web_password

    if host != "127.0.0.1" and not password:
        print_error(
            ConfigError(
                "Binding to a non-localhost address requires a password.",
                fix="Set WEB_PASSWORD in .env or use --password.",
            )
        )
        raise typer.Exit(1)

    try:
        from autoposter.web.app import create_app
    except ImportError as exc:
        print_error(
            ConfigError(
                "Web dashboard requires optional dependencies.",
                fix="Install with: pip install autoposter[web]",
            )
        )
        raise typer.Exit(1) from exc

    import uvicorn

    app = create_app(settings=settings, password=password)
    print_success(f"Dashboard starting at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
