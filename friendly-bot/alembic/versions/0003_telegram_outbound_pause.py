"""Add the singleton durable outbound Telegram rate-limit pause.

Revision ID: 0003_telegram_outbound_pause
Revises: 0002_delivery_contract
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_telegram_outbound_pause"
down_revision: str | None = "0002_delivery_contract"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create and initialize the one row that serializes outbound Telegram claims."""

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


def downgrade() -> None:
    """Remove only the additive rate-limit pause state."""

    op.drop_table("telegram_outbound_pauses")
