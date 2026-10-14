"""Encryption and token storage tests."""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from autoposter.auth.crypto import (
    decrypt_json,
    encrypt_json,
    generate_key,
    resolve_encryption_key,
)
from autoposter.auth.tokens import TokenStore
from autoposter.core.db import session_scope
from autoposter.core.errors import AuthError
from autoposter.core.models import AccountStatus


def test_encrypt_decrypt_roundtrip() -> None:
    key = generate_key()
    payload = {"access_token": "tok-abc", "refresh_token": "ref-def"}
    token = encrypt_json(payload, key)
    assert decrypt_json(token, key) == payload
    assert "tok-abc" not in token


def test_decrypt_wrong_key_raises() -> None:
    key1, key2 = generate_key(), generate_key()
    token = encrypt_json({"a": "value-12345"}, key1)
    with pytest.raises(AuthError, match="decrypt"):
        decrypt_json(token, key2)


def test_key_file_created_owner_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    key = resolve_encryption_key(None, data_dir)
    key_file = data_dir / ".encryption_key"
    assert key_file.is_file()
    assert key_file.read_text(encoding="utf-8").encode("ascii") == key
    if os.name == "posix":
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600


def test_env_key_preferred(tmp_path: Path) -> None:
    key = generate_key()
    resolved = resolve_encryption_key(key.decode("ascii"), tmp_path)
    assert resolved == key


def test_invalid_env_key_raises(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="Invalid Fernet key"):
        resolve_encryption_key("not-a-fernet-key", tmp_path)


def test_missing_key_no_create(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="No encryption key"):
        resolve_encryption_key(None, tmp_path, create=False)


def test_same_key_reused_from_file(tmp_path: Path) -> None:
    key1 = resolve_encryption_key(None, tmp_path)
    key2 = resolve_encryption_key(None, tmp_path)
    assert key1 == key2


def test_token_store_roundtrip(engine: Engine, token_store: TokenStore) -> None:
    with session_scope(engine) as session:
        account_id = token_store.save_account(
            session,
            platform="telegram",
            secrets={"bot_token": "123456789:AAHsecrettokenvaluehere01234567890"},
            kind="bot_token",
        )
        assert account_id > 0
        creds = token_store.get_account(session, "telegram")
        assert creds is not None
        assert creds.secrets["bot_token"].startswith("123456789:")
        assert creds.kind == "bot_token"
        assert creds.status is AccountStatus.ACTIVE


def test_token_store_update_in_place(engine: Engine, token_store: TokenStore) -> None:
    with session_scope(engine) as session:
        first = token_store.save_account(
            session, platform="x", secrets={"access_token": "old-token-000000"}
        )
        second = token_store.save_account(
            session, platform="x", secrets={"access_token": "new-token-111111"}
        )
        assert first == second
        creds = token_store.get_account(session, "x")
        assert creds is not None
        assert creds.secrets["access_token"] == "new-token-111111"


def test_token_store_status_and_expiry(engine: Engine, token_store: TokenStore) -> None:
    with session_scope(engine) as session:
        account_id = token_store.save_account(
            session, platform="linkedin", secrets={"access_token": "li-token-000000"}
        )
        token_store.set_status(session, account_id, AccountStatus.NEEDS_REAUTH)
        expires = datetime.now(UTC) + timedelta(hours=1)
        token_store.set_expiry(session, account_id, expires)
        creds = token_store.get_account_by_id(session, account_id)
        assert creds is not None
        assert creds.status is AccountStatus.NEEDS_REAUTH
        assert creds.token_expires_at is not None
        assert not TokenStore.is_expired(creds)
        # Within the 5-minute proactive-refresh margin counts as expired.
        soon = expires - timedelta(minutes=2)
        assert TokenStore.is_expired(creds, now=soon)


def test_token_store_list_filter(engine: Engine, token_store: TokenStore) -> None:
    with session_scope(engine) as session:
        token_store.save_account(session, platform="telegram", secrets={"bot_token": "t" * 40})
        token_store.save_account(
            session, platform="telegram", secrets={"bot_token": "u" * 40}, label="brand2"
        )
        token_store.save_account(session, platform="bluesky", secrets={"app_password": "pw-123456"})
        all_accounts = token_store.list_accounts(session)
        assert len(all_accounts) == 3
        only_tg = token_store.list_accounts(session, platform="telegram")
        assert len(only_tg) == 2


def test_get_missing_account_returns_none(engine: Engine, token_store: TokenStore) -> None:
    with session_scope(engine) as session:
        assert token_store.get_account(session, "nope") is None
