"""Persist bounded pending interactive flow references.

Revision ID: 0005_pending_flow_intents
Revises: 0004_service_interaction_closure
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_pending_flow_intents"
down_revision: str | None = "0004_service_interaction_closure"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the locked, per-user deferred interactive-flow queue."""

    op.create_table(
        "pending_flow_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flow_key", sa.String(length=512), nullable=False),
        sa.Column("flow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_version_id"], ["flow_versions.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "flow_version_id",
            "flow_key",
            name="uq_pending_flow_intents_user_version_key",
        ),
    )
    op.create_index(
        "ix_pending_flow_intents_user_id",
        "pending_flow_intents",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_pending_flow_intents_user_position",
        "pending_flow_intents",
        ["user_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the additive pending interactive-flow queue."""

    op.drop_index(
        "ix_pending_flow_intents_user_position", table_name="pending_flow_intents"
    )
    op.drop_index("ix_pending_flow_intents_user_id", table_name="pending_flow_intents")
    op.drop_table("pending_flow_intents")
