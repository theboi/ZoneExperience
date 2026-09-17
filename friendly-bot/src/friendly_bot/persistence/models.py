"""Private ORM records and PostgreSQL constraints for F01 durable state."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from friendly_bot.persistence.base import Base

_NIL_UUID = text("'00000000-0000-0000-0000-000000000000'::uuid")


class OperationalRole(StrEnum):
    """The only persisted role classification, stored on :class:`User`."""

    NBNC = "nbnc"
    SERVER = "server"
    LEADER = "leader"
    STAFF = "staff"


class FlowScopeKind(StrEnum):
    """The ownership scope of one immutable flow publication."""

    SYSTEM = "system"
    SERVICE = "service"
    TIMESTAMP = "timestamp"


class ServiceAudience(StrEnum):
    """The seven durable audience values accepted by service timestamps."""

    ALL_NBNCS = "all_nbncs"
    ALL_SERVERS = "all_servers"
    ALL_LEADERS = "all_leaders"
    SERVICE_NBNCS = "service_nbncs"
    SERVICE_SERVERS = "service_servers"
    SERVICE_LEADERS = "service_leaders"
    ALL_SERVICE_ATTENDEES = "all_service_attendees"


OperationalRoleType = Enum(
    OperationalRole,
    name="operational_role",
    native_enum=True,
    inherit_schema=True,
    values_callable=lambda enum_class: [member.value for member in enum_class],
)
FlowScopeKindType = Enum(
    FlowScopeKind,
    name="flow_scope_kind",
    native_enum=True,
    inherit_schema=True,
    values_callable=lambda enum_class: [member.value for member in enum_class],
)
ServiceAudienceType = Enum(
    ServiceAudience,
    name="service_audience",
    native_enum=True,
    inherit_schema=True,
    values_callable=lambda enum_class: [member.value for member in enum_class],
)


class User(Base):
    """A Telegram identity and the application's only role source."""

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[OperationalRole] = mapped_column(
        OperationalRoleType, nullable=False, server_default=text("'nbnc'")
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OperationalProfile(Base):
    """Operational matching details linked to a user without duplicating its role."""

    __tablename__ = "operational_profiles"
    __table_args__ = (
        UniqueConstraint(
            "normalized_name",
            "dob",
            name="uq_operational_profiles_login_identity",
        ),
        CheckConstraint(
            "capacity >= 0", name="ck_operational_profiles_capacity_nonnegative"
        ),
        CheckConstraint(
            "reserved_capacity >= 0",
            name="ck_operational_profiles_reserved_capacity_nonnegative",
        ),
        CheckConstraint(
            "reserved_capacity <= capacity",
            name="ck_operational_profiles_reserved_capacity_within_capacity",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    normalized_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    interests: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    cg_name: Mapped[str | None] = mapped_column(String(256))
    telegram_contact_url: Mapped[str | None] = mapped_column(Text)
    always_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    reserved_capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OperationalLogin(Base):
    """An auditable, exclusive active attachment between profile and user."""

    __tablename__ = "operational_logins"
    __table_args__ = (
        UniqueConstraint(
            "operational_profile_id",
            "user_id",
            "attached_at",
            name="uq_operational_logins_attachment_history",
        ),
        Index(
            "uq_operational_logins_active_profile",
            "operational_profile_id",
            unique=True,
            postgresql_where=text("detached_at IS NULL"),
        ),
        Index(
            "uq_operational_logins_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("detached_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    operational_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("operational_profiles.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    detached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationalLoginAttempt(Base):
    """The short-lived state for one local operational-account command."""

    __tablename__ = "operational_login_attempts"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Service(Base):
    """One real-world gathering and its lifecycle boundaries."""

    __tablename__ = "services"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    highkey: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    doors_open_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    doors_close_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    interaction_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    interaction_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class FlowVersion(Base):
    """An immutable validated flow definition published to PostgreSQL JSONB."""

    __tablename__ = "flow_versions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    scope_kind: Mapped[FlowScopeKind] = mapped_column(FlowScopeKindType, nullable=False)
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id"))
    root_flow_key: Mapped[str] = mapped_column(String(256), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        Index(
            "uq_flow_versions_scope_service_root_content",
            "scope_kind",
            func.coalesce(service_id, _NIL_UUID),
            "root_flow_key",
            "content_hash",
            unique=True,
        ),
    )


class ServiceTimestamp(Base):
    """A scheduled service flow root and its persisted audience."""

    __tablename__ = "service_timestamps"
    __table_args__ = (
        UniqueConstraint(
            "service_id",
            "key",
            "flow_version_id",
            name="uq_service_timestamps_service_key_flow_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(256), nullable=False)
    occurs_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    audience: Mapped[ServiceAudience] = mapped_column(
        ServiceAudienceType, nullable=False
    )
    flow_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("flow_versions.id"), nullable=False
    )
    root_flow_key: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ServiceAttendance(Base):
    """A user's historical attendance with a database-enforced active singleton."""

    __tablename__ = "service_attendances"
    __table_args__ = (
        UniqueConstraint(
            "service_id", "user_id", name="uq_service_attendances_service_user"
        ),
        Index(
            "uq_service_attendances_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attendee_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OpenFlowSelection(Base):
    """The normalized mutable branch state for one published flow version."""

    __tablename__ = "open_flow_selections"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    flow_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("flow_versions.id"), nullable=False
    )
    parent_flow_key: Mapped[str] = mapped_column(String(256), nullable=False)
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_global_interruptive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ancestor_flow_keys: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    checkpoint_flow_keys: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_focused_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "uq_open_flow_selections_user_version_parent_service",
            "user_id",
            "flow_version_id",
            "parent_flow_key",
            func.coalesce(service_id, _NIL_UUID),
            unique=True,
        ),
    )


class ConversationMessage(Base):
    """Normalized incoming conversation history with source-message idempotency."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        Index(
            "uq_conversation_messages_source",
            "user_id",
            "source_kind",
            "source_message_id",
            unique=True,
            postgresql_where=text("source_message_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    replied_to_body: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonaCursor(Base):
    """One durable persona state cursor per user."""

    __tablename__ = "persona_cursors"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, unique=True
    )
    persona: Mapped[str] = mapped_column(Text, nullable=False)
    last_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id"),
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PendingFlowIntent(Base):
    """One bounded, ordered interactive flow reference awaiting later resumption."""

    __tablename__ = "pending_flow_intents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "flow_version_id",
            "flow_key",
            name="uq_pending_flow_intents_user_version_key",
        ),
        Index("ix_pending_flow_intents_user_position", "user_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    flow_key: Mapped[str] = mapped_column(String(512), nullable=False)
    flow_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("flow_versions.id"), nullable=False
    )
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id"))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TelegramPollState(Base):
    """The singleton durable Telegram polling offset."""

    __tablename__ = "telegram_poll_state"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_telegram_poll_state_singleton"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_update_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProcessedTelegramUpdate(Base):
    """A claimed incoming Telegram update, keyed by Telegram's update identifier."""

    __tablename__ = "processed_telegram_updates"

    telegram_update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)


class UserProcessingLock(Base):
    """The row that repositories lock to serialize user-scoped state changes."""

    __tablename__ = "user_processing_locks"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TimestampDeliveryClaim(Base):
    """One durable scheduler claim per timestamp and recipient."""

    __tablename__ = "timestamp_delivery_claims"
    __table_args__ = (
        UniqueConstraint(
            "service_timestamp_id",
            "user_id",
            name="uq_timestamp_delivery_claims_timestamp_user",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    service_timestamp_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_timestamps.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64), nullable=False)


class HumanMatchRequest(Base):
    """One request for a temporary human connection."""

    __tablename__ = "human_match_requests"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id"))
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    interest: Mapped[str | None] = mapped_column(Text)
    meeting_preference: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CapacityReservation(Base):
    """A reserve/release ledger row guarded against duplicate live capacity usage."""

    __tablename__ = "capacity_reservations"
    __table_args__ = (
        Index(
            "uq_capacity_reservations_active_profile_request",
            "operational_profile_id",
            "request_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    operational_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("operational_profiles.id"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_match_requests.id"), nullable=False
    )
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanMatchAssignment(Base):
    """A request assignment that permits at most one active responder."""

    __tablename__ = "human_match_assignments"
    __table_args__ = (
        UniqueConstraint(
            "capacity_reservation_id",
            name="uq_human_match_assignments_capacity_reservation",
        ),
        Index(
            "uq_human_match_assignments_active_request",
            "request_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_match_requests.id"), nullable=False
    )
    responder_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("operational_profiles.id"), nullable=False
    )
    capacity_reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("capacity_reservations.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)


class HumanMatchExclusion(Base):
    """A durable candidate exclusion for one matching request."""

    __tablename__ = "human_match_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "responder_profile_id",
            name="uq_human_match_exclusions_request_responder",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_match_requests.id"), nullable=False
    )
    responder_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("operational_profiles.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DiagnosticRecord(Base):
    """Sanitized operational diagnostics correlated without source secrets."""

    __tablename__ = "diagnostic_records"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    severity: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    safe_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
