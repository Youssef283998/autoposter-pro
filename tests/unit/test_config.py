"""Configuration loading and validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoposter.config.schema import Config
from autoposter.config.settings import load_settings, load_yaml_config
from autoposter.core.errors import ConfigError

GOOD_YAML = """
default_timezone: "Europe/Berlin"
tick_seconds: 10
retry:
  max_attempts: 3
platforms:
  telegram:
    hourly: 20
    daily: 100
    delay_between_posts_seconds: [0.5, 2.0]
media:
  max_upload_mb: 8
data_dir: "{data_dir}"
"""


def test_load_good_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(GOOD_YAML.format(data_dir=(tmp_path / "d").as_posix()), encoding="utf-8")
    config = load_yaml_config(path)
    assert config.default_timezone == "Europe/Berlin"
    assert config.retry.max_attempts == 3
    assert config.platform_caps("telegram").hourly == 20
    assert config.media.max_upload_mb == 8


def test_defaults_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)
    settings = load_settings(env_file=tmp_path / "missing.env")
    assert settings.config.default_timezone == "UTC"
    assert settings.config.tick_seconds == 5
    assert settings.config_path is None


def test_require_config_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)
    with pytest.raises(ConfigError, match=r"No config\.yaml"):
        load_settings(env_file=tmp_path / "missing.env", require_config=True)


def test_unknown_timezone_message(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text('default_timezone: "Mars/Olympus"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_yaml_config(path)
    assert "Mars/Olympus" in str(exc_info.value)
    assert exc_info.value.fix is not None


def test_invalid_platform_caps(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "platforms:\n  x:\n    delay_between_posts_seconds: [10, 1]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_yaml_config(path)
    assert "platforms.x.delay_between_posts_seconds" in str(exc_info.value)


def test_missing_file_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_yaml_config(tmp_path / "nope.yaml")


def test_invalid_yaml_syntax(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("default_timezone: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_yaml_config(path)


def test_extra_keys_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("totally_unknown_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="totally_unknown_key"):
        load_yaml_config(path)


def test_platform_caps_defaults() -> None:
    config = Config()
    caps = config.platform_caps("unknown-platform")
    assert caps.hourly == 30
    assert caps.daily == 500


def test_quiet_hours_validation(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text('quiet_hours:\n  start: "25:00"\n  end: "07:00"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="quiet_hours"):
        load_yaml_config(path)
