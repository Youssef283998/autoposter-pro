"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from autoposter.core.orm import UTCDateTime

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("token_expires_at", UTCDateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "label", name="uq_accounts_platform_label"),
    )
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("scheduled_at_utc", UTCDateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recurrence_rule", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_posts_status_scheduled", "posts", ["status", "scheduled_at_utc"])
    op.create_index("ix_posts_group_id", "posts", ["group_id"])
    op.create_index("ix_posts_content_hash", "posts", ["content_hash"])
    op.create_table(
        "post_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("variant_body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("claimed_at", UTCDateTime(), nullable=True),
        sa.Column("platform_post_id", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "platform", "account_id", name="uq_targets_post_platform_account"),
    )
    op.create_index("ix_targets_status_next", "post_targets", ["status", "next_attempt_at"])
    op.create_index("ix_targets_platform_status", "post_targets", ["platform", "status"])
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=True),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("platform_media_ids", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("redacted_response", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["target_id"], ["post_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attempts_target_id", "attempts", ["target_id"])
    op.create_table(
        "settings_kv",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "schema_version",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("version"),
    )


def downgrade() -> None:
    op.drop_table("schema_version")
    op.drop_table("settings_kv")
    op.drop_index("ix_attempts_target_id", table_name="attempts")
    op.drop_table("attempts")
    op.drop_table("media")
    op.drop_index("ix_targets_platform_status", table_name="post_targets")
    op.drop_index("ix_targets_status_next", table_name="post_targets")
    op.drop_table("post_targets")
    op.drop_index("ix_posts_content_hash", table_name="posts")
    op.drop_index("ix_posts_group_id", table_name="posts")
    op.drop_index("ix_posts_status_scheduled", table_name="posts")
    op.drop_table("posts")
    op.drop_table("accounts")
