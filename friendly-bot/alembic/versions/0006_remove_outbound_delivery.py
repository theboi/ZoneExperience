"""Remove durable outbound Telegram delivery state.

Revision ID: 0006_remove_outbound_delivery
Revises: 0005_pending_flow_intents
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_remove_outbound_delivery"
down_revision: str | None = "0005_pending_flow_intents"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Drop retired durable queues without affecting timestamp recipient claims."""

    op.drop_table("admin_notification_deliveries")
    op.drop_table("outbound_delivery_attempts")
    op.drop_table("outbound_deliveries")
    op.drop_table("telegram_outbound_pauses")


def downgrade() -> None:
    """Restore the historical queue schema for a one-revision rollback."""

    op.create_table(
        "telegram_outbound_pauses",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("pause_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1", name="ck_telegram_outbound_pauses_singleton"
        ),
        sa.PrimaryKeyConstraint("singleton_id"),
    )
    op.execute(
        "INSERT INTO telegram_outbound_pauses (singleton_id, pause_until) "
        "VALUES (1, TIMESTAMPTZ '1970-01-01 00:00:00+00')"
    )
    op.create_table(
        "outbound_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "idempotency_key", sa.String(length=256), nullable=False, unique=True
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_telegram_message_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbound_deliveries_due",
        "outbound_deliveries",
        ["eligible_at", "created_at", "id"],
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )
    op.create_index(
        "ix_outbound_deliveries_expired_claim",
        "outbound_deliveries",
        ["claim_expires_at", "created_at", "id"],
        postgresql_where=sa.text("status = 'claimed'"),
    )
    op.create_table(
        "outbound_delivery_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["delivery_id"], ["outbound_deliveries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_outbound_delivery_attempts_delivery_number",
        ),
    )
    op.create_table(
        "admin_notification_deliveries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("diagnostic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["diagnostic_id"], ["diagnostic_records.id"]),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "diagnostic_id",
            "admin_user_id",
            name="uq_admin_notification_deliveries_diagnostic_admin",
        ),
    )
