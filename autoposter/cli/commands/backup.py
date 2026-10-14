"""autoposter backup / restore — full queue and config backup."""

from __future__ import annotations

import json
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import typer

from autoposter.cli.rich_helpers import print_error, print_info, print_success
from autoposter.config.settings import load_settings
from autoposter.core.errors import ConfigError

UTC = timezone.utc


def backup_command(
    *,
    config_path: Path = Path("config.yaml"),
    out: Path | None = None,
) -> None:
    """Create a full backup (database + config + media)."""
    settings = load_settings(config_path)
    paths = settings.paths

    if out is None:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out = Path(f"autoposter-backup-{stamp}.tar.gz")

    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    # Create staging directory.
    staging = out.parent / f".backup-staging-{datetime.now(UTC).timestamp()}"
    staging.mkdir(parents=True, exist_ok=True)

    try:
        # Copy database.
        shutil.copy2(paths.db_path, staging / "autoposter.db")
        if (paths.db_path.parent / "autoposter.db-wal").exists():
            shutil.copy2(paths.db_path.parent / "autoposter.db-wal", staging / "autoposter.db-wal")

        # Copy config.
        config_file = settings.config_path or (Path.cwd() / "config.yaml")
        if config_file.exists():
            shutil.copy2(config_file, staging / "config.yaml")

        # Copy media.
        if paths.media_dir.exists():
            shutil.copytree(paths.media_dir, staging / "media")

        # Write manifest.
        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "version": "1.0",
            "data_dir": str(paths.data_dir),
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Create tarball.
        with tarfile.open(out, "w:gz") as tar:
            tar.add(staging, arcname=".")

        print_success(f"Backup created: {out}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_command(
    *,
    archive: Path,
    config_path: Path = Path("config.yaml"),
    force: bool = False,
) -> None:
    """Restore from a backup archive."""
    settings = load_settings(config_path)
    paths = settings.paths

    if not force:
        typer.confirm("This will overwrite current data. Continue?", abort=True)

    archive = archive.expanduser().resolve()
    if not archive.exists():
        print_error(ConfigError(f"Archive not found: {archive}"))
        raise typer.Exit(1)

    # Extract to staging.
    staging = archive.parent / f".restore-staging-{datetime.now(UTC).timestamp()}"
    staging.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if os.path.isabs(member.name) or ".." in member.name.split("/"):
                    raise ConfigError(f"Unsafe path in archive: {member.name}")
            tar.extractall(staging)

        # Restore database.
        db_backup = staging / "autoposter.db"
        if db_backup.exists():
            paths.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_backup, paths.db_path)
            wal_backup = staging / "autoposter.db-wal"
            if wal_backup.exists():
                shutil.copy2(wal_backup, paths.db_path.parent / "autoposter.db-wal")

        # Restore config.
        config_backup = staging / "config.yaml"
        if config_backup.exists():
            target = settings.config_path or (Path.cwd() / "config.yaml")
            shutil.copy2(config_backup, target)

        # Restore media.
        media_backup = staging / "media"
        if media_backup.exists():
            if paths.media_dir.exists():
                shutil.rmtree(paths.media_dir)
            shutil.copytree(media_backup, paths.media_dir)

        print_success("Restore complete")
        print_info("Run `autoposter doctor` to verify")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
