"""Add durable closure state for one service interaction lifecycle.

Revision ID: 0004_service_interaction_closure
Revises: 0003_telegram_outbound_pause
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_service_interaction_closure"
down_revision: str | None = "0003_telegram_outbound_pause"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Persist the single lifecycle closure fence without changing historical rows."""

    op.add_column(
        "services",
        sa.Column("interaction_closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove only the additive interaction closure fence."""

    op.drop_column("services", "interaction_closed_at")
