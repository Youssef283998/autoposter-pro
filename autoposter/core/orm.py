"""SQLAlchemy 2.x ORM tables (SQLite).

Status columns are plain strings; enums are enforced at the pydantic layer so
the database stays readable and migration-friendly. All datetimes go through
:class:`UTCDateTime`, which stores naive-UTC and returns aware-UTC values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator[datetime]):
    """DateTime column that always binds/returns aware UTC datetimes.

    SQLite has no native tz-aware type; we normalize to naive-UTC on write
    and attach ``timezone.utc`` on read so callers never see naive values.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all AutoPoster tables."""


class AccountRow(Base):
    """A stored platform account (credentials encrypted at rest)."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "label", name="uq_accounts_platform_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    targets: Mapped[list[PostTargetRow]] = relationship(back_populates="account")


class PostRow(Base):
    """A scheduled piece of content."""

    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_status_scheduled", "status", "scheduled_at_utc"),
        Index("ix_posts_group_id", "group_id"),
        Index("ix_posts_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at_utc: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    recurrence_rule: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    targets: Mapped[list[PostTargetRow]] = relationship(
        back_populates="post", cascade="all, delete-orphan", lazy="selectin"
    )
    media: Mapped[list[MediaRow]] = relationship(
        back_populates="post", cascade="all, delete-orphan", lazy="selectin"
    )


class PostTargetRow(Base):
    """One (post, platform, account) destination with its own retry state."""

    __tablename__ = "post_targets"
    __table_args__ = (
        UniqueConstraint(
            "post_id", "platform", "account_id", name="uq_targets_post_platform_account"
        ),
        Index("ix_targets_status_next", "status", "next_attempt_at"),
        Index("ix_targets_platform_status", "platform", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    variant_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    post: Mapped[PostRow] = relationship(back_populates="targets")
    account: Mapped[AccountRow] = relationship(back_populates="targets")
    attempts_log: Mapped[list[AttemptRow]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )


class MediaRow(Base):
    """A media file copied into the internal hash-named store."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_media_ids: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    post: Mapped[PostRow] = relationship(back_populates="media")


class AttemptRow(Base):
    """Audit log of every publish attempt (secrets already redacted)."""

    __tablename__ = "attempts"
    __table_args__ = (Index("ix_attempts_target_id", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("post_targets.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    redacted_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    target: Mapped[PostTargetRow] = relationship(back_populates="attempts_log")


class SettingsKVRow(Base):
    """Small key/value store for runtime settings (schema bumps, flags)."""

    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class SchemaVersionRow(Base):
    """Single-row schema version marker (alembic is the source of truth)."""

    __tablename__ = "schema_version"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
