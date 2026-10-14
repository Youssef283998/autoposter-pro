"""Crash recovery tests: stuck publishing targets are reconciled."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.engine import Engine

from autoposter.core import queue as q
from autoposter.core.db import session_scope
from autoposter.core.models import TargetStatus
from autoposter.core.orm import PostTargetRow
from autoposter.core.recovery import reconcile_stuck_publishing
from autoposter.utils.timeutil import utcnow


def _account(engine: Engine) -> int:

    from autoposter.core.orm import AccountRow

    with session_scope(engine) as session:
        account = AccountRow(platform="fake", label="default", encrypted_credentials="x")
        session.add(account)
        session.flush()
        return int(account.id)


def _claim_and_get_ids(engine: Engine, account_id: int) -> tuple[int, str]:
    with session_scope(engine) as session:
        q.add_post(
            session,
            body="crash me",
            scheduled_at_utc=utcnow() - timedelta(seconds=1),
            targets=[q.TargetSpec(platform="fake", account_id=account_id)],
        )
        claimed = q.claim_due_targets(session, now=utcnow(), limit=1)
        assert claimed
        return claimed[0].target_id, claimed[0].idempotency_key


def test_stuck_target_requeued_when_not_found(engine: Engine) -> None:
    account_id = _account(engine)
    tid, _key = _claim_and_get_ids(engine, account_id)
    # Age the claim beyond the threshold.
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        row.claimed_at = utcnow() - timedelta(seconds=600)
    with session_scope(engine) as session:
        report = reconcile_stuck_publishing(
            session, lookup=lambda *a: None, stuck_threshold_seconds=300
        )
        assert tid in report.requeued
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == TargetStatus.SCHEDULED.value
        assert row.claimed_at is None
        assert "idempotency key" in (row.last_error or "")


def test_stuck_target_marked_published_when_platform_has_post(engine: Engine) -> None:
    account_id = _account(engine)
    tid, key = _claim_and_get_ids(engine, account_id)
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        row.claimed_at = utcnow() - timedelta(seconds=600)

    def lookup(platform: str, acct: int, idem: str, content_hash: str) -> str | None:
        assert idem == key
        return "platform-42"

    with session_scope(engine) as session:
        report = reconcile_stuck_publishing(
            session, lookup=lookup, stuck_threshold_seconds=300
        )
        assert tid in report.reconciled_published
    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == TargetStatus.PUBLISHED.value
        assert row.platform_post_id == "platform-42"


def test_fresh_claim_left_alone(engine: Engine) -> None:
    account_id = _account(engine)
    tid, _ = _claim_and_get_ids(engine, account_id)
    with session_scope(engine) as session:
        report = reconcile_stuck_publishing(
            session, lookup=lambda *a: None, stuck_threshold_seconds=300
        )
        assert report.requeued == []
        assert report.reconciled_published == []
        row = session.get(PostTargetRow, tid)
        assert row is not None
        assert row.status == TargetStatus.PUBLISHING.value


def test_lookup_exception_falls_back_to_requeue(engine: Engine) -> None:
    account_id = _account(engine)
    tid, _ = _claim_and_get_ids(engine, account_id)

    def bad_lookup(*_args: object) -> str | None:
        raise RuntimeError("api down")

    with session_scope(engine) as session:
        row = session.get(PostTargetRow, tid)
        assert row is not None
        row.claimed_at = utcnow() - timedelta(seconds=600)
    with session_scope(engine) as session:
        report = reconcile_stuck_publishing(
            session, lookup=bad_lookup, stuck_threshold_seconds=300
        )
        assert tid in report.requeued
