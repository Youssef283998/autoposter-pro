"""autoposter run — start the scheduler in the foreground."""

from __future__ import annotations

from pathlib import Path

import typer

from autoposter.cli.rich_helpers import print_error, print_info, print_success
from autoposter.core.db import InstanceLock, init_database, make_engine
from autoposter.core.dispatcher import Dispatcher
from autoposter.core.errors import ConfigError, LockHeldError
from autoposter.core.ratelimit import RateLimiter
from autoposter.core.retry import CircuitBreaker, RetryPolicy
from autoposter.core.scheduler import Scheduler
from autoposter.utils.logging import setup_logging


def run_command(
    *,
    config_path: Path = Path("config.yaml"),
    dry_run: bool = False,
    once: bool = False,
    debug: bool = False,
) -> None:
    """Start the scheduler."""
    import asyncio

    from autoposter.config.settings import load_settings

    try:
        settings = load_settings(config_path, require_config=True)
    except ConfigError as exc:
        print_error(exc)
        raise typer.Exit(1) from exc

    paths = settings.paths
    paths.ensure()

    setup_logging(
        log_dir=paths.log_dir,
        level="DEBUG" if debug else settings.config.log_level,
        json_lines=settings.config.log_json,
    )

    engine = make_engine(paths.db_path)
    init_database(engine)

    lock = InstanceLock(paths.lock_path)
    try:
        lock.acquire()
    except LockHeldError as exc:
        print_error(exc)
        engine.dispose()
        raise typer.Exit(1) from exc

    try:
        dispatcher = Dispatcher(
            adapter_factory=_build_adapter_factory(settings),
            session_factory=_session_factory(engine),
            retry_policy=RetryPolicy(
                max_attempts=settings.config.retry.max_attempts,
                base_delay_seconds=settings.config.retry.base_delay_seconds,
                max_delay_seconds=settings.config.retry.max_delay_seconds,
                jitter=settings.config.retry.jitter,
            ),
            circuit_breaker=CircuitBreaker(
                failure_threshold=settings.config.circuit_breaker.failure_threshold,
                cooldown_seconds=settings.config.circuit_breaker.cooldown_seconds,
            ),
            rate_acquire=_rate_acquire(settings),
        )

        scheduler = Scheduler(
            session_factory=_session_factory(engine),
            config=settings.config,
            dispatcher=dispatcher,
        )

        if dry_run:
            print_info("Dry run mode — no posts will be published")

        if once:
            print_info("Running one tick...")
            report = asyncio.run(scheduler.tick())
            print_success(f"Tick complete: {report.claimed} claimed")
            return

        print_success("Scheduler running (Ctrl+C to stop)")
        try:
            scheduler.run_forever_sync()
        except KeyboardInterrupt:
            print_info("Shutting down...")
    finally:
        lock.release()
        engine.dispose()


def _build_adapter_factory(settings):
    """Build the adapter factory for the dispatcher."""
    from autoposter.auth.crypto import resolve_encryption_key
    from autoposter.auth.tokens import TokenStore
    from autoposter.platforms.registry import build_adapter

    key = resolve_encryption_key(settings.secrets.encryption_key, settings.paths.data_dir)
    store = TokenStore(key)

    def factory(platform: str, account_id: int):
        with _session_factory(settings.paths.db_path)() as session:
            account = store.get_account_by_id(session, account_id)
            if account is None:
                raise ConfigError(f"Account {account_id} not found for {platform}")
            caps = settings.config.platform_caps(platform)
            return build_adapter(platform, account, caps)

    return factory


def _session_factory(engine):
    """Create a session context manager."""
    from contextlib import contextmanager

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def _sessions():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _sessions


def _rate_acquire(settings):
    """Build the rate limiter acquire function."""
    limiter = RateLimiter(
        caps_for=lambda p: settings.config.platform_caps(p),
    )

    async def acquire(platform: str) -> None:
        await limiter.acquire(platform)

    return acquire
