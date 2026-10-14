"""autoposter demo — run the full pipeline against the fake platform."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from autoposter.cli.rich_helpers import console, print_info, print_success
from autoposter.core import queue as q
from autoposter.core.db import init_database, make_engine, session_scope
from autoposter.core.dispatcher import Dispatcher
from autoposter.core.models import (
    Post,
    PostTarget,
    PublishResult,
)
from autoposter.core.orm import AccountRow
from autoposter.core.retry import RetryPolicy
from autoposter.core.scheduler import Scheduler
from autoposter.utils.timeutil import utcnow


class DemoAdapter:
    """Fake adapter that prints instead of publishing."""

    name = "fake"

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(
        self, post: Post, target: PostTarget, idempotency_key: str
    ) -> PublishResult:
        body = target.variant_body or post.variants.get(target.platform, post.body)
        self.published.append(idempotency_key)
        console.print(f"  [green]✓[/green] Would publish to {target.platform}: {body[:60]}")
        return PublishResult(
            success=True,
            platform_post_id=f"demo-{len(self.published)}",
            url=f"https://demo.example/p/{len(self.published)}",
        )

    async def refresh_auth(self) -> None:
        return None

    async def healthcheck(self):
        from autoposter.core.models import HealthStatus

        return HealthStatus(ok=True, detail="demo adapter")


def demo_command(
    *,
    posts: int = 3,
    config_path: Path = Path("config.yaml"),
) -> None:
    """Run a demo with sample content against the fake platform."""
    import asyncio

    from autoposter.config.settings import load_settings

    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()
    engine = make_engine(paths.db_path)
    init_database(engine)

    try:
        # Create demo account (or reuse existing).
        with session_scope(engine) as session:
            from sqlalchemy import select

            existing = session.execute(
                select(AccountRow).where(
                    AccountRow.platform == "fake", AccountRow.label == "default"
                )
            ).scalar_one_or_none()
            if existing is None:
                account = AccountRow(
                    platform="fake", label="default", encrypted_credentials="demo"
                )
                session.add(account)
                session.flush()
                account_id = int(account.id)
            else:
                account_id = int(existing.id)

        # Create sample posts.
        sample_bodies = [
            "Launching our new product today! 🚀 #launch #startup",
            "50% off all plans this weekend. Don't miss out! 🎉",
            "Thanks for 10k followers! Here's what's next for us. 💙",
            "New blog post: How we scaled to 1M users in 6 months.",
            "We're hiring! Join our remote-first team. 🌍",
        ]

        with session_scope(engine) as session:
            for i in range(min(posts, len(sample_bodies))):
                q.add_post(
                    session,
                    body=sample_bodies[i],
                    scheduled_at_utc=utcnow() - timedelta(seconds=1),
                    timezone="UTC",
                    targets=[q.TargetSpec(platform="fake", account_id=account_id)],
                )

        print_info(f"Created {posts} demo post(s)")

        # Run one tick.
        adapter = DemoAdapter()
        dispatcher = Dispatcher(
            adapter_factory=lambda p, a: adapter,
            session_factory=_session_factory(engine),
            retry_policy=RetryPolicy(),
        )
        scheduler = Scheduler(
            session_factory=_session_factory(engine),
            config=settings.config,
            dispatcher=dispatcher,
        )

        print_info("Running scheduler tick...")
        report = asyncio.run(scheduler.tick())

        console.print("\n[bold]Demo complete:[/bold]")
        console.print(f"  Claimed: {report.claimed}")
        console.print(f"  Published: {report.dispatched.published if report.dispatched else 0}")
        console.print(f"  Failed: {report.dispatched.failed if report.dispatched else 0}")

        print_success("Demo finished — no real posts were made")
    finally:
        engine.dispose()


def _session_factory(engine):
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
