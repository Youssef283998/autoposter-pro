"""SQLite engine, sessions, migrations and the single-instance lock file."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import alembic.command
import alembic.config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from autoposter.core.errors import DatabaseError, LockHeldError, MigrationError

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@dataclass(frozen=True)
class DataPaths:
    """All filesystem locations derived from ``data_dir``."""

    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "autoposter.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "autoposter.lock"

    def ensure(self) -> None:
        """Create the data, media and log directories if missing."""
        for path in (self.data_dir, self.media_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


def sqlite_url(db_path: Path) -> str:
    """Build a SQLAlchemy SQLite URL for an absolute path (cross-platform)."""
    resolved = db_path.resolve()
    return f"sqlite+pysqlite:///{resolved.as_posix()}"


def make_engine(db_path: Path) -> Engine:
    """Create a SQLite engine with WAL, foreign keys and a busy timeout."""
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        sqlite_url(db_path),
        future=True,
        connect_args={"check_same_thread": False, "timeout": 5.0},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional session: commit on success, roll back on error."""
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_wal_mode(engine: Engine) -> str:
    """Return the current journal mode; used by ``doctor`` and tests."""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode"))
        return str(result.scalar_one()).lower()


def upgrade_database(engine: Engine, *, scripts_dir: Path | None = None) -> None:
    """Bring the schema to head using the packaged alembic migrations."""
    scripts = scripts_dir or _MIGRATIONS_DIR
    if not scripts.is_dir():
        raise MigrationError(
            f"Migrations directory not found: {scripts}",
            fix="Reinstall the package; migrations ship inside the autoposter package.",
        )
    cfg = alembic.config.Config()
    cfg.set_main_option("script_location", str(scripts))
    db_url = sqlite_url(Path(str(engine.url.database or "autoposter.db")))
    cfg.set_main_option("sqlalchemy.url", db_url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            alembic.command.upgrade(cfg, "head")
    except MigrationError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MigrationError(
            f"Database migration failed: {exc}",
            fix="Check that data_dir is writable and no other AutoPoster is running.",
        ) from exc


def schema_version(engine: Engine) -> str | None:
    """Return the alembic revision id, or None if migrations never ran."""
    from sqlalchemy import inspect

    with engine.connect() as conn:
        if "alembic_version" not in inspect(conn).get_table_names():
            return None
        row = conn.execute(text("SELECT version_num FROM alembic_version"))
        value = row.scalar_one_or_none()
        return str(value) if value is not None else None


def init_database(engine: Engine, *, scripts_dir: Path | None = None) -> None:
    """Create/migrate the database and verify WAL is active."""
    upgrade_database(engine, scripts_dir=scripts_dir)
    mode = check_wal_mode(engine)
    if mode != "wal":
        raise DatabaseError(
            f"SQLite journal mode is {mode!r}, expected 'wal'.",
            fix="Ensure the database file is on a filesystem that supports WAL (not a network share).",  # noqa: E501
        )


# ---------------------------------------------------------------------------
# Single-instance lock file
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    """Best-effort cross-platform check that a PID is still running."""
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows only
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class InstanceLock:
    """PID lock file so two schedulers cannot share one database."""

    def __init__(self, lock_path: Path) -> None:
        self._path = lock_path
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self) -> None:
        """Acquire the lock or raise :class:`LockHeldError`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            existing = self._read_pid()
            if existing is not None and pid_alive(existing):
                raise LockHeldError(
                    f"Another AutoPoster instance (PID {existing}) is using {self._path.parent}.",
                    fix="Stop that instance first, or run with a different data_dir.",
                ) from exc
            # Stale lock from a crashed process: reclaim it.
            self._path.unlink(missing_ok=True)
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self._held = True

    def release(self) -> None:
        """Release the lock if held by us. Safe to call twice."""
        if not self._held:
            return
        self._held = False
        if self._path.exists() and self._read_pid() == os.getpid():
            self._path.unlink(missing_ok=True)

    def _read_pid(self) -> int | None:
        try:
            return int(self._path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
