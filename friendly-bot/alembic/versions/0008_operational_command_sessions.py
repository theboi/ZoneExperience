"""Generalize local operational-login state into a direct command session.

Revision ID: 0008_operational_commands
Revises: 0007_operational_login_attempts
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_operational_commands"
down_revision: str | None = "0007_operational_login_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Track the local command step while allowing the name prompt to be blank."""

    op.add_column(
        "operational_login_attempts",
        sa.Column(
            "step",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'login_dob'"),
        ),
    )
    op.alter_column(
        "operational_login_attempts", "normalized_name", existing_type=sa.String(256), nullable=True
    )
    op.alter_column("operational_login_attempts", "step", server_default=None)


def downgrade() -> None:
    """Restore the prior name-only login-attempt representation."""

    op.execute(
        "DELETE FROM operational_login_attempts WHERE normalized_name IS NULL"
    )
    op.alter_column(
        "operational_login_attempts", "normalized_name", existing_type=sa.String(256), nullable=False
    )
    op.drop_column("operational_login_attempts", "step")
