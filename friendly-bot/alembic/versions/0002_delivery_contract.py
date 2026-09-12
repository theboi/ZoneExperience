"""Add fenced durable delivery scheduling and attempt correlation.

Revision ID: 0002_delivery_contract
Revises: 0001_foundation
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_delivery_contract"
down_revision: str | None = "0001_foundation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add only the durable fields and query indexes used by the delivery contract."""

    op.add_column(
        "outbound_deliveries",
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE outbound_deliveries SET eligible_at = created_at "
        "WHERE eligible_at IS NULL"
    )
    op.alter_column("outbound_deliveries", "eligible_at", nullable=False)
    op.add_column(
        "outbound_deliveries",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "outbound_deliveries",
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbound_deliveries",
        sa.Column("confirmed_telegram_message_id", sa.BigInteger(), nullable=True),
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

    op.add_column(
        "outbound_delivery_attempts",
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE outbound_delivery_attempts SET correlation_id = gen_random_uuid() "
        "WHERE correlation_id IS NULL"
    )
    op.alter_column("outbound_delivery_attempts", "correlation_id", nullable=False)


def downgrade() -> None:
    """Restore the exact 0001 delivery schema without touching its rows or tables."""

    op.drop_column("outbound_delivery_attempts", "correlation_id")
    op.drop_index("ix_outbound_deliveries_expired_claim", "outbound_deliveries")
    op.drop_index("ix_outbound_deliveries_due", "outbound_deliveries")
    op.drop_column("outbound_deliveries", "confirmed_telegram_message_id")
    op.drop_column("outbound_deliveries", "claim_expires_at")
    op.drop_column("outbound_deliveries", "claim_token")
    op.drop_column("outbound_deliveries", "eligible_at")
