"""Load and validate ``.env`` (secrets) and ``config.yaml`` (behavior)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from autoposter.config.schema import Config
from autoposter.core.db import DataPaths
from autoposter.core.errors import ConfigError

DEFAULT_CONFIG_FILENAME = "config.yaml"
CONFIG_ENV_VAR = "AUTOPOSTER_CONFIG"


class SecretSettings(BaseSettings):
    """Secrets from the environment / ``.env``. Never logged, never in YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    encryption_key: str | None = None

    telegram_bot_token: str | None = None

    discord_bot_token: str | None = None
    discord_webhook_url: str | None = None

    mastodon_api_base: str | None = None
    mastodon_client_id: str | None = None
    mastodon_client_secret: str | None = None
    mastodon_access_token: str | None = None

    bluesky_handle: str | None = None
    bluesky_app_password: str | None = None

    x_client_id: str | None = None
    x_client_secret: str | None = None
    x_access_token: str | None = None
    x_refresh_token: str | None = None
    x_token_expires_at: str | None = None

    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_access_token: str | None = None
    linkedin_refresh_token: str | None = None
    linkedin_token_expires_at: str | None = None
    linkedin_person_urn: str | None = None
    linkedin_org_urn: str | None = None

    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    alert_telegram_chat_id: str | None = None
    alert_webhook_url: str | None = None

    web_password: str | None = None


@dataclass(frozen=True)
class AppSettings:
    """Fully resolved runtime settings."""

    config: Config
    secrets: SecretSettings
    config_path: Path | None

    @property
    def paths(self) -> DataPaths:
        return DataPaths(self.config.data_dir.expanduser().resolve())


def resolve_config_path(explicit: Path | None = None) -> Path | None:
    """Find ``config.yaml``: explicit arg > $AUTOPOSTER_CONFIG > ./config.yaml."""
    import os

    if explicit is not None:
        return explicit.expanduser().resolve()
    env_value = os.environ.get(CONFIG_ENV_VAR)
    if env_value:
        return Path(env_value).expanduser().resolve()
    default = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if default.is_file():
        return default.resolve()
    return None


def load_yaml_config(path: Path) -> Config:
    """Parse and validate ``config.yaml``; errors are plain English."""
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}",
            fix="Run `autoposter init` to create config.yaml, or pass --config PATH.",
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"Config file {path} is not valid YAML: {exc}",
            fix="Fix the YAML syntax (check indentation) or run `autoposter init` for a fresh file.",  # noqa: E501
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file {path} must contain a YAML mapping at the top level.",
            fix=f"Compare {path} with config.example.yaml shipped with AutoPoster.",
        )
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, path), fix=_validation_fix(exc)) from exc


def _format_validation_error(exc: ValidationError, path: Path) -> str:
    lines = [f"Invalid settings in {path}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        message = error["msg"]
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        lines.append(f"  - {location}: {message}")
    return "\n".join(lines)


def _validation_fix(exc: ValidationError) -> str:
    locs = {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
    if "default_timezone" in locs:
        return 'Set default_timezone to an IANA zone such as "UTC" or "Europe/Berlin".'
    if any(loc.startswith("platforms.") for loc in locs):
        return "Check the platforms: section against config.example.yaml."
    return "Compare your file with config.example.yaml and fix the fields listed above."


def load_settings(
    config_path: Path | None = None,
    *,
    env_file: Path | None = None,
    require_config: bool = False,
) -> AppSettings:
    """Load secrets from the environment and behavior from YAML.

    Args:
        config_path: Explicit path to ``config.yaml``.
        env_file: Explicit ``.env`` path (defaults to ``./.env`` if present).
        require_config: When True, a missing config file is an error. When
            False (default), a missing file yields schema defaults so commands
            like ``init`` and ``version`` still work.
    """
    resolved = resolve_config_path(config_path)
    if require_config and resolved is None:
        raise ConfigError(
            "No config.yaml found.",
            fix="Run `autoposter init` to create one, or pass --config PATH.",
        )
    config = load_yaml_config(resolved) if resolved is not None else Config()

    chosen_env = env_file
    if chosen_env is None:
        candidate = Path.cwd() / ".env"
        chosen_env = candidate if candidate.is_file() else None

    if chosen_env is not None:  # noqa: SIM108
        # pydantic-settings accepts _env_file at runtime; hide it from mypy.
        secrets = SecretSettings(_env_file=chosen_env)  # type: ignore[call-arg]
    else:
        secrets = SecretSettings()
    return AppSettings(config=config, secrets=secrets, config_path=resolved)
