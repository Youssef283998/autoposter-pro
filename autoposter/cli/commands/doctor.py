"""autoposter doctor — test every connection, config, permissions, clock."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from autoposter.auth.crypto import resolve_encryption_key
from autoposter.auth.tokens import TokenStore
from autoposter.cli.rich_helpers import console, print_success, print_warning
from autoposter.config.settings import load_settings
from autoposter.core.db import check_wal_mode, make_engine, schema_version, session_scope
from autoposter.core.errors import ConfigError
from autoposter.utils.redact import redact_text

UTC = timezone.utc


def doctor_command(
    *,
    config_path: Path = Path("config.yaml"),
    platform: str | None = None,
) -> None:
    """Run all health checks."""
    checks: list[tuple[str, bool, str]] = []

    # Python version.
    py_ok = sys.version_info >= (3, 10)
    checks.append(
        (
            "Python version",
            py_ok,
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )

    # Config validity.
    try:
        settings = load_settings(config_path)
        checks.append(("Config file", True, str(settings.config_path or "defaults")))
    except ConfigError as exc:
        checks.append(("Config file", False, exc.message))
        _print_checks(checks)
        raise typer.Exit(1) from exc

    # DB access and migrations.
    try:
        engine = make_engine(settings.paths.db_path)
        version = schema_version(engine)
        wal = check_wal_mode(engine)
        engine.dispose()
        checks.append(("Database", version is not None, f"schema {version}, {wal}"))
    except Exception as exc:
        checks.append(("Database", False, redact_text(str(exc))))

    # File permissions.
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        mode = env_file.stat().st_mode
        owner_only = not (mode & 0o077)
        checks.append((".env permissions", owner_only, f"mode {oct(mode & 0o777)}"))
    else:
        checks.append((".env file", False, "not found"))

    # System clock.
    now = datetime.now(UTC)
    checks.append(("System clock", True, now.isoformat()))

    # Disk space.
    try:
        usage = shutil.disk_usage(settings.paths.data_dir)
        free_mb = usage.free // (1024 * 1024)
        checks.append(("Disk space", free_mb > 100, f"{free_mb} MB free"))
    except Exception as exc:
        checks.append(("Disk space", False, redact_text(str(exc))))

    # Accounts.
    key = resolve_encryption_key(settings.secrets.encryption_key, settings.paths.data_dir)
    store = TokenStore(key)
    engine = make_engine(settings.paths.db_path)
    try:
        with session_scope(engine) as session:
            accounts = store.list_accounts(session)
        if not accounts:
            checks.append(("Accounts", False, "none configured"))
        else:
            for account in accounts:
                status_icon = "✓" if account.status.value == "active" else "!"
                checks.append(
                    (
                        f"  {account.platform}/{account.label}",
                        account.status.value == "active",
                        f"{status_icon} {account.status.value}",
                    )
                )
    except Exception as exc:
        redacted = redact_text(str(exc))
        checks.append(("Accounts", False, redacted))
    finally:
        engine.dispose()

    _print_checks(checks)

    failed = [c for c in checks if not c[1]]
    if failed:
        print_warning(f"{len(failed)} check(s) failed")
        raise typer.Exit(1)
    print_success("All checks passed")


def _print_checks(checks: list[tuple[str, bool, str]]) -> None:
    """Print check results as a table."""
    from rich.table import Table

    table = Table(title="Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Detail", style="white")
    for name, ok, detail in checks:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        table.add_row(name, status, detail)
    console.print(table)
