"""autoposter import — CSV/JSON bulk import with validation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import typer

from autoposter.cli.rich_helpers import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
    validation_table,
)
from autoposter.core import queue as q
from autoposter.core.db import init_database, make_engine, session_scope
from autoposter.core.errors import ConfigError
from autoposter.utils.timeutil import parse_user_datetime


def import_command(
    *,
    file: Path,
    config_path: Path = Path("config.yaml"),
    validate_only: bool = False,
    skip_invalid: bool = False,
    dry_run: bool = False,
) -> None:
    """Import posts from CSV or JSON."""
    from autoposter.config.settings import load_settings

    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()
    engine = make_engine(paths.db_path)
    init_database(engine)

    try:
        rows = _load_file(file)
        if not rows:
            print_warning("No rows found in file")
            return

        print_info(f"Loaded {len(rows)} row(s) from {file}")

        # Validate.
        problems = []
        valid_rows = []
        for i, row in enumerate(rows, start=2):  # row 1 is header
            row_problems = _validate_row(row, i)
            if row_problems:
                problems.extend(row_problems)
            else:
                valid_rows.append(row)

        if problems:
            console.print(validation_table(problems))
            if not skip_invalid:
                print_error(
                    ConfigError(
                        f"{len(problems)} validation problem(s) found.",
                        fix="Fix the file or pass --skip-invalid to import valid rows only.",
                    )
                )
                raise typer.Exit(1)

        if validate_only:
            print_success(f"All {len(rows)} row(s) valid")
            return

        if dry_run:
            print_info(f"Dry run — would schedule {len(valid_rows)} post(s)")
            for row in valid_rows:
                dt = row.get("datetime", "?")
                plats = row.get("platforms", "?")
                text = row.get("text", "")[:50]
                console.print(f"  {dt} | {plats} | {text}")
            return

        # Schedule.
        from autoposter.auth.crypto import resolve_encryption_key
        from autoposter.auth.tokens import TokenStore

        key = resolve_encryption_key(settings.secrets.encryption_key, settings.paths.data_dir)
        store = TokenStore(key)

        scheduled = 0
        with session_scope(engine) as session:
            for row in valid_rows:
                platforms = [
                    p.strip()
                    for p in str(row.get("platforms", "")).split(",")
                    if p.strip()
                ]
                targets = []
                for platform in platforms:
                    account = store.get_account(session, platform)
                    if account is None:
                        print_warning(f"No account for {platform}, skipping row")
                        continue
                    targets.append(q.TargetSpec(platform=platform, account_id=account.id))

                if not targets:
                    continue

                tz = str(row.get("timezone", settings.config.default_timezone))
                dt_str = str(row.get("datetime", ""))
                try:
                    scheduled_time = parse_user_datetime(dt_str, tz)
                except ConfigError as exc:
                    print_warning(f"Row skipped: {exc.message}")
                    continue

                q.add_post(
                    session,
                    body=str(row.get("text", "")),
                    scheduled_at_utc=scheduled_time,
                    timezone=tz,
                    targets=targets,
                )
                scheduled += 1

        print_success(f"Scheduled {scheduled} post(s)")
    finally:
        engine.dispose()


def _load_file(file: Path) -> list[dict]:
    """Load rows from CSV or JSON."""
    suffix = file.suffix.lower()
    if suffix == ".csv":
        with open(file, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            return list(reader)
    if suffix == ".json":
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "posts" in data:
            return data["posts"]
        raise ConfigError("JSON must be a list of posts or an object with a 'posts' key")
    raise ConfigError(f"Unsupported file type: {suffix}. Use .csv or .json")


def _validate_row(row: dict, row_num: int) -> list[dict]:
    """Validate one row. Returns list of problems."""
    problems = []
    text = str(row.get("text", "")).strip()
    if not text:
        problems.append({"row": row_num, "field": "text", "message": "empty text"})

    platforms = str(row.get("platforms", "")).strip()
    if not platforms:
        problems.append({"row": row_num, "field": "platforms", "message": "no platforms"})

    dt_str = str(row.get("datetime", "")).strip()
    if not dt_str:
        problems.append({"row": row_num, "field": "datetime", "message": "no datetime"})

    return problems
