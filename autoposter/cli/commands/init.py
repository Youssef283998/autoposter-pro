"""autoposter init — create folders, config, database, encryption key."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from autoposter.cli.rich_helpers import print_info, print_success, print_warning
from autoposter.core.db import init_database, make_engine
from autoposter.utils.logging import setup_logging


def init_command(
    *,
    config_path: Path = Path("config.yaml"),
    data_dir: Path | None = None,
    force: bool = False,
) -> None:
    """Initialize AutoPoster: config, database, encryption key."""
    from autoposter.auth.crypto import resolve_encryption_key
    from autoposter.config.schema import Config
    from autoposter.config.settings import load_yaml_config

    config_file = config_path.expanduser().resolve()

    if config_file.exists() and not force:
        print_warning(f"Config already exists: {config_file}")
        typer.echo("Use --force to overwrite.")
        raise typer.Exit(1)

    # Load or create config.
    config = load_yaml_config(config_file) if config_file.exists() else Config()

    if data_dir is not None:
        config.data_dir = data_dir.expanduser().resolve()

    paths = config.data_dir

    print_info(f"Data directory: {paths}")

    # Create directories.
    paths.mkdir(parents=True, exist_ok=True)
    (paths / "media").mkdir(exist_ok=True)
    (paths / "logs").mkdir(exist_ok=True)

    # Create or update config.yaml.
    if not config_file.exists() or force:
        try:
            import yaml

            raw = yaml.safe_load(config.example_yaml()) if hasattr(config, "example_yaml") else None
        except Exception:
            raw = None
        if raw is None:
            # Write a minimal commented config.
            config_file.write_text(_minimal_config(), encoding="utf-8")
        print_success(f"Created {config_file}")

    # Create .env if missing.
    env_file = config_file.parent / ".env"
    if not env_file.exists():
        env_file.write_text(_minimal_env(), encoding="utf-8")
        if os.name == "posix":
            env_file.chmod(0o600)
        print_success(f"Created {env_file}")

    # Create encryption key.
    resolve_encryption_key(None, paths)
    print_success("Encryption key ready")

    # Initialize database.
    engine = make_engine(paths / "autoposter.db")
    init_database(engine)
    engine.dispose()
    print_success("Database initialized")

    # Set up logging.
    setup_logging(log_dir=paths / "logs", level=config.log_level, json_lines=config.log_json)
    print_success("Logging configured")

    print_success(f"AutoPoster initialized in {config_file.parent}")
    print_info("Next: autoposter setup <platform> to connect a platform")


def _minimal_config() -> str:
    return """\
# AutoPoster Pro — BEHAVIOR CONFIG.
# Secrets (tokens, keys, passwords) do NOT go here; they live in .env.

default_timezone: "UTC"
tick_seconds: 5

retry:
  max_attempts: 5
  base_delay_seconds: 2.0
  max_delay_seconds: 300.0
  jitter: 0.25

circuit_breaker:
  failure_threshold: 5
  cooldown_seconds: 120.0

dedup:
  window_hours: 24
  mode: "warn"

missed:
  post_late_within_minutes: 30
  max_catchup_posts_per_tick: 1

platforms: {}

quiet_hours: null

media:
  auto_resize: true
  max_upload_mb: 25
  keep_originals: true
  image_max_width: 2048
  image_quality: 85

alerts: []

log_level: "INFO"
log_json: false

data_dir: "~/.autoposter"

web:
  host: "127.0.0.1"
  port: 8300
"""


def _minimal_env() -> str:
    return """\
# AutoPoster Pro — SECRETS ONLY. Never commit this file.

# Fernet key for encryptating stored OAuth tokens. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# If left blank, AutoPoster generates a key on first run.
ENCRYPTION_KEY=

# --- Telegram ---
TELEGRAM_BOT_TOKEN=

# --- Discord ---
DISCORD_BOT_TOKEN=
DISCORD_WEBHOOK_URL=

# --- Mastodon ---
MASTODON_API_BASE=https://mastodon.social
MASTODON_CLIENT_ID=
MASTODON_CLIENT_SECRET=
MASTODON_ACCESS_TOKEN=

# --- Bluesky ---
BLUESKY_HANDLE=
BLUESKY_APP_PASSWORD=

# --- X (Twitter) ---
X_CLIENT_ID=
X_CLIENT_SECRET=

# --- LinkedIn ---
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=

# --- Alerts (optional) ---
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
ALERT_TELEGRAM_CHAT_ID=
ALERT_WEBHOOK_URL=
WEB_PASSWORD=
"""
