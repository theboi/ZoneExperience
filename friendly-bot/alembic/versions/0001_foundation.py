"""Create the complete F01 durable PostgreSQL foundation.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NIL_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"


def upgrade() -> None:
    """Create every F01 database-owned object in dependency order."""

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        "CREATE TYPE operational_role AS ENUM ('nbnc', 'server', 'leader', 'staff')"
    )
    op.execute("CREATE TYPE flow_scope_kind AS ENUM ('system', 'service', 'timestamp')")
    op.execute(
        "CREATE TYPE service_audience AS ENUM ("
        "'all_nbncs', 'all_servers', 'all_leaders', 'service_nbncs', "
        "'service_servers', 'service_leaders', 'all_service_attendees')"
    )

    operational_role = postgresql.ENUM(
        "nbnc", "server", "leader", "staff", name="operational_role", create_type=False
    )
    flow_scope_kind = postgresql.ENUM(
        "system", "service", "timestamp", name="flow_scope_kind", create_type=False
    )
    service_audience = postgresql.ENUM(
        "all_nbncs",
        "all_servers",
        "all_leaders",
        "service_nbncs",
        "service_servers",
        "service_leaders",
        "all_service_attendees",
        name="service_audience",
        create_type=False,
    )

    op.create_table(
        "diagnostic_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(length=64), nullable=False),
        sa.Column("safe_summary", sa.Text(), nullable=False),
        sa.Column(
            "safe_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diagnostic_records_correlation_id",
        "diagnostic_records",
        ["correlation_id"],
        unique=False,
    )

    op.create_table(
        "processed_telegram_updates",
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("telegram_update_id"),
    )

    op.create_table(
        "services",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=256), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=128), nullable=False),
        sa.Column(
            "highkey", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("doors_open_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("doors_close_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interaction_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "telegram_poll_state",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("next_update_offset", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton_id = 1", name="ck_telegram_poll_state_singleton"),
        sa.PrimaryKeyConstraint("singleton_id"),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "role",
            operational_role,
            server_default=sa.text("'nbnc'"),
            nullable=False,
        ),
        sa.Column(
            "is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "operational_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("dob", sa.Date(), nullable=False),
        sa.Column("interests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cg_name", sa.String(length=256), nullable=True),
        sa.Column("telegram_contact_url", sa.Text(), nullable=True),
        sa.Column(
            "always_available",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "capacity", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "reserved_capacity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capacity >= 0", name="ck_operational_profiles_capacity_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved_capacity >= 0",
            name="ck_operational_profiles_reserved_capacity_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_capacity <= capacity",
            name="ck_operational_profiles_reserved_capacity_within_capacity",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_name", "dob", name="uq_operational_profiles_login_identity"
        ),
    )
    op.create_index(
        "ix_operational_profiles_user_id",
        "operational_profiles",
        ["user_id"],
        unique=True,
    )

    op.create_table(
        "operational_logins",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "operational_profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detached_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operational_profile_id"], ["operational_profiles.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operational_profile_id",
            "user_id",
            "attached_at",
            name="uq_operational_logins_attachment_history",
        ),
    )
    op.create_index(
        "uq_operational_logins_active_profile",
        "operational_logins",
        ["operational_profile_id"],
        unique=True,
        postgresql_where=sa.text("detached_at IS NULL"),
    )
    op.create_index(
        "uq_operational_logins_active_user",
        "operational_logins",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("detached_at IS NULL"),
    )

    op.create_table(
        "flow_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("scope_kind", flow_scope_kind, nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_flow_key", sa.String(length=256), nullable=False),
        sa.Column(
            "definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_flow_versions_scope_service_root_content",
        "flow_versions",
        [
            "scope_kind",
            sa.text(f"coalesce(service_id, {_NIL_UUID})"),
            "root_flow_key",
            "content_hash",
        ],
        unique=True,
    )

    op.create_table(
        "service_timestamps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("occurs_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("audience", service_audience, nullable=False),
        sa.Column("flow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_flow_key", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.ForeignKeyConstraint(["flow_version_id"], ["flow_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_id",
            "key",
            "flow_version_id",
            name="uq_service_timestamps_service_key_flow_version",
        ),
    )

    op.create_table(
        "service_attendances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attendee_kind", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_id", "user_id", name="uq_service_attendances_service_user"
        ),
    )
    op.create_index(
        "uq_service_attendances_active_user",
        "service_attendances",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "open_flow_selections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flow_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_flow_key", sa.String(length=256), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("is_global_interruptive", sa.Boolean(), nullable=False),
        sa.Column("ancestor_flow_keys", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "checkpoint_flow_keys", postgresql.ARRAY(sa.String()), nullable=False
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_focused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["flow_version_id"], ["flow_versions.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_open_flow_selections_user_id",
        "open_flow_selections",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_open_flow_selections_user_version_parent_service",
        "open_flow_selections",
        [
            "user_id",
            "flow_version_id",
            "parent_flow_key",
            sa.text(f"coalesce(service_id, {_NIL_UUID})"),
        ],
        unique=True,
    )

    op.create_table(
        "conversation_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("replied_to_body", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_messages_user_id",
        "conversation_messages",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_conversation_messages_source",
        "conversation_messages",
        ["user_id", "source_kind", "source_message_id"],
        unique=True,
        postgresql_where=sa.text("source_message_id IS NOT NULL"),
    )

    op.create_table(
        "persona_cursors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True
        ),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("last_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["last_message_id"], ["conversation_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_processing_locks",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "timestamp_delivery_claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "service_timestamp_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["service_timestamp_id"], ["service_timestamps.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_timestamp_id",
            "user_id",
            name="uq_timestamp_delivery_claims_timestamp_user",
        ),
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
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
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
        "human_match_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("interest", sa.Text(), nullable=True),
        sa.Column("meeting_preference", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "capacity_reservations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "operational_profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operational_profile_id"], ["operational_profiles.id"]
        ),
        sa.ForeignKeyConstraint(["request_id"], ["human_match_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_capacity_reservations_active_profile_request",
        "capacity_reservations",
        ["operational_profile_id", "request_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "human_match_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "responder_profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "capacity_reservation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["human_match_requests.id"]),
        sa.ForeignKeyConstraint(["responder_profile_id"], ["operational_profiles.id"]),
        sa.ForeignKeyConstraint(
            ["capacity_reservation_id"], ["capacity_reservations.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "capacity_reservation_id",
            name="uq_human_match_assignments_capacity_reservation",
        ),
    )
    op.create_index(
        "uq_human_match_assignments_active_request",
        "human_match_assignments",
        ["request_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "human_match_exclusions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "responder_profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["request_id"], ["human_match_requests.id"]),
        sa.ForeignKeyConstraint(["responder_profile_id"], ["operational_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            "responder_profile_id",
            name="uq_human_match_exclusions_request_responder",
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


def downgrade() -> None:
    """Drop exactly the F01-owned objects in reverse dependency order."""

    op.drop_table("admin_notification_deliveries")
    op.drop_table("human_match_exclusions")
    op.drop_index(
        "uq_human_match_assignments_active_request", "human_match_assignments"
    )
    op.drop_table("human_match_assignments")
    op.drop_index(
        "uq_capacity_reservations_active_profile_request", "capacity_reservations"
    )
    op.drop_table("capacity_reservations")
    op.drop_table("human_match_requests")
    op.drop_table("outbound_delivery_attempts")
    op.drop_table("outbound_deliveries")
    op.drop_table("timestamp_delivery_claims")
    op.drop_table("user_processing_locks")
    op.drop_table("persona_cursors")
    op.drop_index("uq_conversation_messages_source", "conversation_messages")
    op.drop_index("ix_conversation_messages_user_id", "conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index(
        "uq_open_flow_selections_user_version_parent_service", "open_flow_selections"
    )
    op.drop_index("ix_open_flow_selections_user_id", "open_flow_selections")
    op.drop_table("open_flow_selections")
    op.drop_index("uq_service_attendances_active_user", "service_attendances")
    op.drop_table("service_attendances")
    op.drop_table("service_timestamps")
    op.drop_index("uq_flow_versions_scope_service_root_content", "flow_versions")
    op.drop_table("flow_versions")
    op.drop_index("uq_operational_logins_active_user", "operational_logins")
    op.drop_index("uq_operational_logins_active_profile", "operational_logins")
    op.drop_table("operational_logins")
    op.drop_index("ix_operational_profiles_user_id", "operational_profiles")
    op.drop_table("operational_profiles")
    op.drop_table("users")
    op.drop_table("telegram_poll_state")
    op.drop_table("services")
    op.drop_table("processed_telegram_updates")
    op.drop_index("ix_diagnostic_records_correlation_id", "diagnostic_records")
    op.drop_table("diagnostic_records")

    op.execute("DROP TYPE service_audience")
    op.execute("DROP TYPE flow_scope_kind")
    op.execute("DROP TYPE operational_role")
