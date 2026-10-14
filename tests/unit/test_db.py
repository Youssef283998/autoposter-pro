"""Database, migration, WAL and instance-lock tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from autoposter.core.db import (
    DataPaths,
    InstanceLock,
    check_wal_mode,
    init_database,
    make_engine,
    schema_version,
    session_scope,
    sqlite_url,
)
from autoposter.core.errors import LockHeldError
from autoposter.core.orm import AccountRow, PostRow
from autoposter.utils.timeutil import utcnow


def test_data_paths_layout(tmp_path: Path) -> None:
    paths = DataPaths(tmp_path / "d")
    paths.ensure()
    assert paths.db_path.name == "autoposter.db"
    assert paths.media_dir.is_dir()
    assert paths.log_dir.is_dir()


def test_sqlite_url_absolute_posix() -> None:
    url = sqlite_url(Path("/tmp/autoposter-test/x.db"))
    assert url.startswith("sqlite+pysqlite:///")


def test_init_database_creates_all_tables(engine: Engine) -> None:
    names = set(inspect(engine).get_table_names())
    assert {
        "accounts",
        "posts",
        "post_targets",
        "media",
        "attempts",
        "settings_kv",
        "schema_version",
        "alembic_version",
    } <= names


def test_wal_mode_enabled(engine: Engine) -> None:
    assert check_wal_mode(engine) == "wal"


def test_schema_version_is_0001(engine: Engine) -> None:
    assert schema_version(engine) == "0001_initial"


def test_foreign_keys_on(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1


def test_session_scope_commit_and_rollback(engine: Engine) -> None:
    with session_scope(engine) as session:
        session.add(
            PostRow(
                body="committed",
                scheduled_at_utc=utcnow(),
                timezone="UTC",
                status="scheduled",
                content_hash="a" * 64,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    with session_scope(engine) as session:
        count = session.execute(text("SELECT COUNT(*) FROM posts")).scalar_one()
        assert count == 1

    with pytest.raises(RuntimeError), session_scope(engine) as session:
        session.add(
            PostRow(
                body="rolled back",
                scheduled_at_utc=utcnow(),
                timezone="UTC",
                status="scheduled",
                content_hash="b" * 64,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        raise RuntimeError("boom")

    with session_scope(engine) as session:
        count = session.execute(text("SELECT COUNT(*) FROM posts")).scalar_one()
        assert count == 1


def test_account_target_restriction_cascade(engine: Engine) -> None:
    from autoposter.core.orm import PostTargetRow

    with session_scope(engine) as session:
        account = AccountRow(platform="fake", label="default", encrypted_credentials="x")
        session.add(account)
        session.flush()
        post = PostRow(
            body="b",
            scheduled_at_utc=utcnow(),
            timezone="UTC",
            status="scheduled",
            content_hash="c" * 64,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(post)
        session.flush()
        session.add(
            PostTargetRow(
                post_id=post.id,
                platform="fake",
                account_id=account.id,
                status="scheduled",
                idempotency_key=f"{post.id}:fake:{account.id}",
            )
        )
        session.flush()
        post_id, account_id = post.id, account.id

    with session_scope(engine) as session:
        post = session.get(PostRow, post_id)
        assert post is not None
        session.delete(post)  # cascades to targets

    with session_scope(engine) as session:
        remaining = session.execute(
            text("SELECT COUNT(*) FROM post_targets WHERE post_id = :pid"), {"pid": post_id}
        ).scalar_one()
        assert remaining == 0
        # account must still exist (RESTRICT only blocks account deletion)
        assert session.get(AccountRow, account_id) is not None


def test_instance_lock_acquire_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "autoposter.lock"
    lock = InstanceLock(lock_path)
    lock.acquire()
    assert lock_path.is_file()
    assert lock_path.read_text(encoding="utf-8") == str(os.getpid())
    lock.release()
    assert not lock_path.exists()


def test_instance_lock_conflict(tmp_path: Path) -> None:
    lock_path = tmp_path / "autoposter.lock"
    first = InstanceLock(lock_path)
    first.acquire()
    second = InstanceLock(lock_path)
    with pytest.raises(LockHeldError, match="Another AutoPoster"):
        second.acquire()
    first.release()


def test_instance_lock_reclaims_stale(tmp_path: Path) -> None:
    lock_path = tmp_path / "autoposter.lock"
    # PID 2^22 is almost certainly dead / invalid on Linux
    lock_path.write_text("999999999", encoding="utf-8")
    lock = InstanceLock(lock_path)
    lock.acquire()
    assert lock.held
    lock.release()


def test_make_engine_and_upgrade_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "sub" / "x.db"
    eng = make_engine(db_path)
    try:
        init_database(eng)
        init_database(eng)  # second run must not fail
        assert check_wal_mode(eng) == "wal"
    finally:
        eng.dispose()
