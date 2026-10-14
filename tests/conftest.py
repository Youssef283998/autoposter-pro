"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from autoposter.auth.crypto import resolve_encryption_key
from autoposter.auth.tokens import TokenStore
from autoposter.config.settings import AppSettings, load_settings
from autoposter.core.db import init_database, make_engine


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def app_settings(tmp_path: Path, data_dir: Path, env_file: Path) -> AppSettings:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "default_timezone: UTC\ntick_seconds: 5\n"
        f'data_dir: "{data_dir.as_posix()}"\n',
        encoding="utf-8",
    )
    return load_settings(config_path, env_file=env_file)


@pytest.fixture
def engine(app_settings: AppSettings) -> Engine:
    paths = app_settings.paths
    paths.ensure()
    eng = make_engine(paths.db_path)
    init_database(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def encryption_key(data_dir: Path) -> bytes:
    return resolve_encryption_key(None, data_dir)


@pytest.fixture
def token_store(encryption_key: bytes) -> TokenStore:
    return TokenStore(encryption_key)
