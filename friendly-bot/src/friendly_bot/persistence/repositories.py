"""Async PostgreSQL repositories that expose frozen persistence DTOs only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import Select, and_, delete, exists, false, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from friendly_bot.domain.publication import PublishedFlowDefinition
from friendly_bot.domain.state import (
    CheckpointReturnTransition,
    OpenSelectionState,
    SelectionTransition,
)
from friendly_bot.persistence.models import (
    AdminNotificationDelivery,
    CapacityReservation,
    ConversationMessage,
    FlowScopeKind,
    FlowVersion,
    HumanMatchAssignment,
    HumanMatchExclusion,
    HumanMatchRequest,
    OpenFlowSelection,
    OperationalLogin,
    OperationalProfile,
    OperationalRole,
    OutboundDelivery,
    OutboundDeliveryAttempt,
    PersonaCursor,
    ProcessedTelegramUpdate,
    Service,
    ServiceAttendance,
    ServiceAudience,
    ServiceTimestamp,
    TelegramPollState,
    TimestampDeliveryClaim,
    User,
)
from friendly_bot.persistence.models import (
    DiagnosticRecord as DiagnosticModel,
)

type LoginAttachmentResult = Literal["attached", "occupied", "not_found"]
type DeliveryOutcome = Literal["sent", "retry", "rejected", "uncertain"]
DEFAULT_SAFE_CLAIM_LEASE = timedelta(minutes=1)


class DeliveryClaimLostError(RuntimeError):
    """Raised when a worker no longer owns a safe outbound-delivery claim."""


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    telegram_user_id: int | None
    display_name: str | None
    role: OperationalRole
    is_admin: bool


@dataclass(frozen=True, slots=True)
class OperationalProfileRecord:
    id: UUID
    user_id: UUID
    normalized_name: str
    interests: tuple[str, ...]
    cg_name: str | None
    telegram_contact_url: str | None
    always_available: bool
    capacity: int
    reserved_capacity: int


@dataclass(frozen=True, slots=True)
class OperationalLoginRecord:
    id: UUID
    operational_profile_id: UUID
    user_id: UUID
    attached_at: datetime
    detached_at: datetime | None


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    id: UUID
    key: str
    highkey: bool
    doors_open_at: datetime
    doors_close_at: datetime
    interaction_ends_at: datetime


@dataclass(frozen=True, slots=True)
class AttendanceRecord:
    id: UUID
    service_id: UUID
    user_id: UUID
    attendee_kind: str
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class AttendanceStartResult:
    attendance: AttendanceRecord
    ended_previous: bool


@dataclass(frozen=True, slots=True)
class ServiceTimestampRecord:
    id: UUID
    service_id: UUID
    key: str
    occurs_at: datetime
    audience: ServiceAudience
    flow_version_id: UUID
    root_flow_key: str


@dataclass(frozen=True, slots=True)
class ConversationMessageRecord:
    id: UUID
    user_id: UUID
    source_kind: str
    source_message_id: int | None
    body: str
    replied_to_body: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PersonaCursorRecord:
    user_id: UUID
    persona: str
    last_message_id: UUID | None
    generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class MatchCandidateRecord:
    profile_id: UUID
    user_id: UUID
    role: OperationalRole
    interests: tuple[str, ...]
    cg_name: str | None
    always_available: bool
    capacity: int
    reserved_capacity: int


@dataclass(frozen=True, slots=True)
class MatchAssignmentRecord:
    id: UUID
    request_id: UUID
    responder_profile_id: UUID
    assigned_at: datetime


@dataclass(frozen=True, slots=True)
class PollStateRecord:
    next_update_offset: int


@dataclass(frozen=True, slots=True)
class NewOutboundDelivery:
    idempotency_key: str
    user_id: UUID
    telegram_chat_id: int
    kind: str
    payload: dict[str, JsonValue]
    status: str = "pending"
    eligible_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OutboundDeliveryMessage:
    """Provider-neutral durable send inputs reconstructed from the outbox row."""

    chat_id: int
    kind: str
    payload: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class OutboundDeliveryRecord:
    id: UUID
    idempotency_key: str
    status: str
    message: OutboundDeliveryMessage
    eligible_at: datetime
    claim_token: UUID | None
    claim_expires_at: datetime | None
    confirmed_telegram_message_id: int | None


@dataclass(frozen=True, slots=True)
class DeliveryAttemptRecord:
    id: UUID
    delivery_id: UUID
    attempt_number: int
    started_at: datetime
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    id: UUID
    correlation_id: UUID
    severity: str
    safe_summary: str


@dataclass(frozen=True, slots=True)
class FlowVersionRecord:
    id: UUID
    scope_kind: FlowScopeKind
    service_id: UUID | None
    root_flow_key: str
    definition: dict[str, JsonValue]
    content_hash: str
    published_at: datetime
    published_by_user_id: UUID | None


class UserRepository(Protocol):
    async def resolve_telegram_sender(
        self, telegram_user_id: int, *, received_at: datetime
    ) -> UserRecord: ...

    async def require_by_telegram_id(self, telegram_user_id: int) -> UserRecord: ...

    async def set_display_name(
        self, user_id: UUID, display_name: str, *, at: datetime
    ) -> UserRecord: ...


class OperationalProfileRepository(Protocol):
    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord | None: ...

    async def update_interests(
        self, profile_id: UUID, interests: list[str], *, at: datetime
    ) -> OperationalProfileRecord: ...


class OperationalLoginRepository(Protocol):
    async def attach(
        self, profile_id: UUID, user_id: UUID, *, at: datetime
    ) -> LoginAttachmentResult: ...

    async def detach_for_user(
        self, user_id: UUID, *, at: datetime
    ) -> OperationalLoginRecord | None: ...


class ServiceRepository(Protocol):
    async def get(self, service_id: UUID) -> ServiceRecord: ...

    async def list_ongoing(self, *, now: datetime) -> list[ServiceRecord]: ...

    async def list_due_timestamps(
        self, *, now: datetime
    ) -> list[ServiceTimestampRecord]: ...

    async def list_audience_user_ids(
        self, audience: ServiceAudience, service_id: UUID | None, *, now: datetime
    ) -> list[UUID]: ...


class AttendanceRepository(Protocol):
    async def start_or_switch(
        self,
        user_id: UUID,
        service_id: UUID,
        *,
        attendee_kind: str,
        started_at: datetime,
    ) -> AttendanceStartResult: ...

    async def active_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> AttendanceRecord | None: ...

    async def end_active_for_service(
        self, service_id: UUID, *, ended_at: datetime
    ) -> int: ...


class PollStateRepository(Protocol):
    async def get(self) -> PollStateRecord: ...

    async def advance_monotonically(
        self, next_update_offset: int, *, at: datetime
    ) -> PollStateRecord: ...


class UpdateRepository(Protocol):
    async def claim_update(
        self, telegram_update_id: int, *, received_at: datetime
    ) -> bool: ...


class ConversationRepository(Protocol):
    async def record_incoming(
        self,
        *,
        user_id: UUID,
        source_message_id: int,
        body: str,
        replied_to_body: str | None,
        occurred_at: datetime,
    ) -> ConversationMessageRecord: ...

    async def list_after(
        self, user_id: UUID, message_id: UUID | None
    ) -> list[ConversationMessageRecord]: ...


class PersonaRepository(Protocol):
    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord: ...

    async def advance(
        self,
        user_id: UUID,
        *,
        persona: str,
        last_message_id: UUID,
        generated_at: datetime,
    ) -> None: ...


class MatchRepository(Protocol):
    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]: ...

    async def list_eligible_safety(
        self, service_id: UUID | None, request_id: UUID
    ) -> list[MatchCandidateRecord]: ...

    async def reserve_ranked(
        self, request_id: UUID, ranked_profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord | None: ...

    async def release_and_exclude(
        self, request_id: UUID, profile_id: UUID, *, reason: str, now: datetime
    ) -> None: ...


class OpenSelectionRepository(Protocol):
    async def list_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]: ...

    async def apply(
        self, transition: SelectionTransition | CheckpointReturnTransition
    ) -> None: ...

    async def expire_service_bound(self, service_id: UUID, *, at: datetime) -> int: ...


class DeliveryRepository(Protocol):
    async def claim_timestamp_delivery(
        self, service_timestamp_id: UUID, user_id: UUID
    ) -> bool: ...

    async def enqueue(
        self, delivery: NewOutboundDelivery
    ) -> OutboundDeliveryRecord: ...

    async def claim_next_safe(
        self, *, now: datetime, lease_duration: timedelta = DEFAULT_SAFE_CLAIM_LEASE
    ) -> OutboundDeliveryRecord | None: ...

    async def renew_claim(
        self,
        delivery_id: UUID,
        *,
        claim_token: UUID,
        now: datetime,
        lease_duration: timedelta = DEFAULT_SAFE_CLAIM_LEASE,
    ) -> OutboundDeliveryRecord: ...

    async def start_attempt(
        self,
        delivery_id: UUID,
        correlation_id: UUID,
        *,
        claim_token: UUID,
        started_at: datetime,
    ) -> DeliveryAttemptRecord: ...

    async def finish_attempt(
        self,
        delivery_id: UUID,
        attempt_id: UUID,
        outcome: DeliveryOutcome,
        *,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error: str | None = None,
        confirmed_telegram_message_id: int | None = None,
    ) -> None: ...


class DiagnosticRepository(Protocol):
    async def record(
        self,
        *,
        correlation_id: UUID,
        severity: str,
        safe_summary: str,
        safe_context: dict[str, JsonValue],
        at: datetime,
    ) -> DiagnosticRecord: ...

    async def enqueue_admin_notifications(
        self, diagnostic_id: UUID, *, at: datetime
    ) -> int: ...


class FlowVersionRepository(Protocol):
    async def publish(
        self,
        definition: PublishedFlowDefinition,
        *,
        scope_kind: FlowScopeKind,
        service_id: UUID | None,
        published_by_user_id: UUID | None,
    ) -> FlowVersionRecord: ...

    async def get(self, flow_version_id: UUID) -> FlowVersionRecord: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_lease_duration(lease_duration: timedelta) -> None:
    if lease_duration <= timedelta():
        raise ValueError("safe claim lease duration must be positive")


def _user_record(row: User) -> UserRecord:
    return UserRecord(
        id=row.id,
        telegram_user_id=row.telegram_user_id,
        display_name=row.display_name,
        role=row.role,
        is_admin=row.is_admin,
    )


def _profile_record(row: OperationalProfile) -> OperationalProfileRecord:
    return OperationalProfileRecord(
        id=row.id,
        user_id=row.user_id,
        normalized_name=row.normalized_name,
        interests=tuple(cast(list[str], row.interests)),
        cg_name=row.cg_name,
        telegram_contact_url=row.telegram_contact_url,
        always_available=row.always_available,
        capacity=row.capacity,
        reserved_capacity=row.reserved_capacity,
    )


def _login_record(row: OperationalLogin) -> OperationalLoginRecord:
    return OperationalLoginRecord(
        id=row.id,
        operational_profile_id=row.operational_profile_id,
        user_id=row.user_id,
        attached_at=row.attached_at,
        detached_at=row.detached_at,
    )


def _service_record(row: Service) -> ServiceRecord:
    return ServiceRecord(
        id=row.id,
        key=row.key,
        highkey=row.highkey,
        doors_open_at=row.doors_open_at,
        doors_close_at=row.doors_close_at,
        interaction_ends_at=row.interaction_ends_at,
    )


def _attendance_record(row: ServiceAttendance) -> AttendanceRecord:
    return AttendanceRecord(
        id=row.id,
        service_id=row.service_id,
        user_id=row.user_id,
        attendee_kind=row.attendee_kind,
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def _timestamp_record(row: ServiceTimestamp) -> ServiceTimestampRecord:
    return ServiceTimestampRecord(
        id=row.id,
        service_id=row.service_id,
        key=row.key,
        occurs_at=row.occurs_at,
        audience=row.audience,
        flow_version_id=row.flow_version_id,
        root_flow_key=row.root_flow_key,
    )


def _conversation_record(row: ConversationMessage) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        id=row.id,
        user_id=row.user_id,
        source_kind=row.source_kind,
        source_message_id=row.source_message_id,
        body=row.body,
        replied_to_body=row.replied_to_body,
        occurred_at=row.occurred_at,
    )


def _cursor_record(row: PersonaCursor) -> PersonaCursorRecord:
    return PersonaCursorRecord(
        user_id=row.user_id,
        persona=row.persona,
        last_message_id=row.last_message_id,
        generated_at=row.generated_at,
    )


def _candidate_record(profile: OperationalProfile, user: User) -> MatchCandidateRecord:
    return MatchCandidateRecord(
        profile_id=profile.id,
        user_id=user.id,
        role=user.role,
        interests=tuple(cast(list[str], profile.interests)),
        cg_name=profile.cg_name,
        always_available=profile.always_available,
        capacity=profile.capacity,
        reserved_capacity=profile.reserved_capacity,
    )


def _assignment_record(row: HumanMatchAssignment) -> MatchAssignmentRecord:
    return MatchAssignmentRecord(
        id=row.id,
        request_id=row.request_id,
        responder_profile_id=row.responder_profile_id,
        assigned_at=row.assigned_at,
    )


def _freeze_json(value: JsonValue) -> JsonValue:
    """Make JSON values immutable before exposing them through a frozen DTO."""

    if isinstance(value, dict):
        return cast(
            JsonValue,
            MappingProxyType({key: _freeze_json(item) for key, item in value.items()}),
        )
    if isinstance(value, list):
        return cast(JsonValue, tuple(_freeze_json(item) for item in value))
    return value


def _freeze_payload(payload: dict[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Detach the returned message payload from the ORM row and freeze it recursively."""

    return MappingProxyType(
        {key: _freeze_json(value) for key, value in payload.items()}
    )


def _delivery_record(row: OutboundDelivery) -> OutboundDeliveryRecord:
    return OutboundDeliveryRecord(
        id=row.id,
        idempotency_key=row.idempotency_key,
        status=row.status,
        message=OutboundDeliveryMessage(
            chat_id=row.telegram_chat_id,
            kind=row.kind,
            payload=_freeze_payload(row.payload),
        ),
        eligible_at=row.eligible_at,
        claim_token=row.claim_token,
        claim_expires_at=row.claim_expires_at,
        confirmed_telegram_message_id=row.confirmed_telegram_message_id,
    )


def _attempt_record(row: OutboundDeliveryAttempt) -> DeliveryAttemptRecord:
    return DeliveryAttemptRecord(
        id=row.id,
        delivery_id=row.delivery_id,
        attempt_number=row.attempt_number,
        started_at=row.started_at,
        correlation_id=row.correlation_id,
    )


def _diagnostic_record(row: DiagnosticModel) -> DiagnosticRecord:
    return DiagnosticRecord(
        id=row.id,
        correlation_id=row.correlation_id,
        severity=row.severity,
        safe_summary=row.safe_summary,
    )


def _flow_version_record(row: FlowVersion) -> FlowVersionRecord:
    return FlowVersionRecord(
        id=row.id,
        scope_kind=row.scope_kind,
        service_id=row.service_id,
        root_flow_key=row.root_flow_key,
        definition=cast(dict[str, JsonValue], dict(row.definition)),
        content_hash=row.content_hash,
        published_at=row.published_at,
        published_by_user_id=row.published_by_user_id,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_telegram_sender(
        self, telegram_user_id: int, *, received_at: datetime
    ) -> UserRecord:
        row = await self._session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if row is None:
            statement = (
                pg_insert(User)
                .values(
                    telegram_user_id=telegram_user_id,
                    created_at=received_at,
                    updated_at=received_at,
                )
                .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
                .returning(User)
            )
            row = await self._session.scalar(statement)
        if row is None:
            row = await self._session.scalar(
                select(User).where(User.telegram_user_id == telegram_user_id)
            )
        if row is None:
            raise LookupError("telegram sender could not be resolved")
        return _user_record(row)

    async def require_by_telegram_id(self, telegram_user_id: int) -> UserRecord:
        row = await self._session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if row is None:
            raise LookupError("telegram sender was not found")
        return _user_record(row)

    async def set_display_name(
        self, user_id: UUID, display_name: str, *, at: datetime
    ) -> UserRecord:
        row = await self._session.scalar(
            update(User)
            .where(User.id == user_id)
            .values(display_name=display_name, updated_at=at)
            .returning(User)
        )
        if row is None:
            raise LookupError("user was not found")
        return _user_record(row)


class SqlAlchemyOperationalProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord | None:
        row = await self._session.scalar(
            select(OperationalProfile).where(
                OperationalProfile.normalized_name == normalized_name,
                OperationalProfile.dob == dob,
            )
        )
        return _profile_record(row) if row is not None else None

    async def update_interests(
        self, profile_id: UUID, interests: list[str], *, at: datetime
    ) -> OperationalProfileRecord:
        row = await self._session.scalar(
            update(OperationalProfile)
            .where(OperationalProfile.id == profile_id)
            .values(interests=list(interests), updated_at=at)
            .returning(OperationalProfile)
        )
        if row is None:
            raise LookupError("operational profile was not found")
        return _profile_record(row)


class SqlAlchemyOperationalLoginRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def attach(
        self, profile_id: UUID, user_id: UUID, *, at: datetime
    ) -> LoginAttachmentResult:
        profile_exists = await self._session.scalar(
            select(OperationalProfile.id).where(OperationalProfile.id == profile_id)
        )
        user_exists = await self._session.scalar(
            select(User.id).where(User.id == user_id)
        )
        if profile_exists is None or user_exists is None:
            return "not_found"
        row = await self._session.scalar(
            pg_insert(OperationalLogin)
            .values(
                operational_profile_id=profile_id,
                user_id=user_id,
                attached_at=at,
            )
            .on_conflict_do_nothing()
            .returning(OperationalLogin.id)
        )
        return "attached" if row is not None else "occupied"

    async def detach_for_user(
        self, user_id: UUID, *, at: datetime
    ) -> OperationalLoginRecord | None:
        row = await self._session.scalar(
            update(OperationalLogin)
            .where(
                OperationalLogin.user_id == user_id,
                OperationalLogin.detached_at.is_(None),
            )
            .values(detached_at=at)
            .returning(OperationalLogin)
        )
        return _login_record(row) if row is not None else None


class SqlAlchemyServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, service_id: UUID) -> ServiceRecord:
        row = await self._session.get(Service, service_id)
        if row is None:
            raise LookupError("service was not found")
        return _service_record(row)

    async def list_ongoing(self, *, now: datetime) -> list[ServiceRecord]:
        rows = await self._session.scalars(
            select(Service)
            .where(
                Service.doors_open_at <= now,
                Service.interaction_ends_at > now,
            )
            .order_by(Service.doors_open_at, Service.id)
        )
        return [_service_record(row) for row in rows]

    async def list_due_timestamps(
        self, *, now: datetime
    ) -> list[ServiceTimestampRecord]:
        rows = await self._session.scalars(
            select(ServiceTimestamp)
            .join(Service, Service.id == ServiceTimestamp.service_id)
            .where(
                ServiceTimestamp.occurs_at <= now,
                Service.interaction_ends_at > now,
            )
            .order_by(ServiceTimestamp.occurs_at, ServiceTimestamp.id)
        )
        return [_timestamp_record(row) for row in rows]

    async def list_audience_user_ids(
        self, audience: ServiceAudience, service_id: UUID | None, *, now: datetime
    ) -> list[UUID]:
        if audience is ServiceAudience.ALL_NBNCS:
            statement = select(User.id).where(User.role == OperationalRole.NBNC)
        elif audience is ServiceAudience.ALL_SERVERS:
            statement = select(User.id).where(
                User.role.in_(
                    (
                        OperationalRole.SERVER,
                        OperationalRole.LEADER,
                        OperationalRole.STAFF,
                    )
                )
            )
        elif audience is ServiceAudience.ALL_LEADERS:
            statement = select(User.id).where(
                User.role.in_((OperationalRole.LEADER, OperationalRole.STAFF))
            )
        else:
            if service_id is None:
                raise ValueError("service audience requires a service id")
            statement = (
                select(User.id)
                .join(ServiceAttendance, ServiceAttendance.user_id == User.id)
                .join(Service, Service.id == ServiceAttendance.service_id)
                .where(
                    ServiceAttendance.service_id == service_id,
                    ServiceAttendance.ended_at.is_(None),
                    Service.interaction_ends_at > now,
                )
            )
            if audience is ServiceAudience.SERVICE_NBNCS:
                statement = statement.where(User.role == OperationalRole.NBNC)
            elif audience is ServiceAudience.SERVICE_SERVERS:
                statement = statement.where(
                    User.role.in_(
                        (
                            OperationalRole.SERVER,
                            OperationalRole.LEADER,
                            OperationalRole.STAFF,
                        )
                    )
                )
            elif audience is ServiceAudience.SERVICE_LEADERS:
                statement = statement.where(
                    User.role.in_((OperationalRole.LEADER, OperationalRole.STAFF))
                )
            elif audience is not ServiceAudience.ALL_SERVICE_ATTENDEES:
                raise ValueError("unsupported service audience")
        rows = await self._session.scalars(statement.distinct().order_by(User.id))
        return list(rows)


class SqlAlchemyAttendanceRepository:
    def __init__(
        self, session: AsyncSession, lock_user: Callable[[UUID], Awaitable[None]]
    ) -> None:
        self._session = session
        self._lock_user = lock_user

    async def start_or_switch(
        self,
        user_id: UUID,
        service_id: UUID,
        *,
        attendee_kind: str,
        started_at: datetime,
    ) -> AttendanceStartResult:
        await self._lock_user(user_id)
        active = await self._session.scalar(
            select(ServiceAttendance)
            .where(
                ServiceAttendance.user_id == user_id,
                ServiceAttendance.ended_at.is_(None),
            )
            .with_for_update()
        )
        if active is not None and active.service_id == service_id:
            return AttendanceStartResult(_attendance_record(active), False)

        ended_previous = active is not None
        if active is not None:
            active.ended_at = started_at
            active.updated_at = started_at

        existing = await self._session.scalar(
            select(ServiceAttendance)
            .where(
                ServiceAttendance.user_id == user_id,
                ServiceAttendance.service_id == service_id,
            )
            .with_for_update()
        )
        if existing is None:
            existing = ServiceAttendance(
                service_id=service_id,
                user_id=user_id,
                attendee_kind=attendee_kind,
                started_at=started_at,
            )
            self._session.add(existing)
        else:
            existing.attendee_kind = attendee_kind
            existing.ended_at = None
            existing.updated_at = started_at
        await self._session.flush()
        return AttendanceStartResult(_attendance_record(existing), ended_previous)

    async def active_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> AttendanceRecord | None:
        row = await self._session.scalar(
            select(ServiceAttendance).where(
                ServiceAttendance.user_id == user_id,
                ServiceAttendance.ended_at.is_(None),
                ServiceAttendance.started_at <= now,
            )
        )
        return _attendance_record(row) if row is not None else None

    async def end_active_for_service(
        self, service_id: UUID, *, ended_at: datetime
    ) -> int:
        active_ids = list(
            await self._session.scalars(
                select(ServiceAttendance.id).where(
                    ServiceAttendance.service_id == service_id,
                    ServiceAttendance.ended_at.is_(None),
                )
            )
        )
        if not active_ids:
            return 0
        await self._session.execute(
            update(ServiceAttendance)
            .where(
                ServiceAttendance.id.in_(active_ids),
            )
            .values(ended_at=ended_at, updated_at=ended_at)
        )
        return len(active_ids)


class SqlAlchemyPollStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> PollStateRecord:
        statement = (
            pg_insert(TelegramPollState)
            .values(singleton_id=1, next_update_offset=0, updated_at=_now())
            .on_conflict_do_nothing(index_elements=[TelegramPollState.singleton_id])
            .returning(TelegramPollState)
        )
        row = await self._session.scalar(statement)
        if row is None:
            row = await self._session.get(TelegramPollState, 1)
        if row is None:
            raise RuntimeError("telegram poll state could not be materialized")
        return PollStateRecord(next_update_offset=row.next_update_offset)

    async def advance_monotonically(
        self, next_update_offset: int, *, at: datetime
    ) -> PollStateRecord:
        insert_statement = pg_insert(TelegramPollState).values(
            singleton_id=1,
            next_update_offset=next_update_offset,
            updated_at=at,
        )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[TelegramPollState.singleton_id],
            set_={
                "next_update_offset": func.greatest(
                    TelegramPollState.next_update_offset,
                    insert_statement.excluded.next_update_offset,
                ),
                "updated_at": at,
            },
        ).returning(TelegramPollState)
        row = await self._session.scalar(statement)
        if row is None:
            raise RuntimeError("telegram poll state could not advance")
        return PollStateRecord(next_update_offset=row.next_update_offset)


class SqlAlchemyUpdateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_update(
        self, telegram_update_id: int, *, received_at: datetime
    ) -> bool:
        row = await self._session.scalar(
            pg_insert(ProcessedTelegramUpdate)
            .values(
                telegram_update_id=telegram_update_id,
                received_at=received_at,
                correlation_id=uuid4(),
            )
            .on_conflict_do_nothing(
                index_elements=[ProcessedTelegramUpdate.telegram_update_id]
            )
            .returning(ProcessedTelegramUpdate.telegram_update_id)
        )
        return row is not None


class SqlAlchemyConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_incoming(
        self,
        *,
        user_id: UUID,
        source_message_id: int,
        body: str,
        replied_to_body: str | None,
        occurred_at: datetime,
    ) -> ConversationMessageRecord:
        statement = (
            pg_insert(ConversationMessage)
            .values(
                user_id=user_id,
                source_kind="telegram",
                source_message_id=source_message_id,
                body=body,
                replied_to_body=replied_to_body,
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    ConversationMessage.user_id,
                    ConversationMessage.source_kind,
                    ConversationMessage.source_message_id,
                ],
                index_where=ConversationMessage.source_message_id.is_not(None),
            )
            .returning(ConversationMessage)
        )
        row = await self._session.scalar(statement)
        if row is None:
            row = await self._session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.user_id == user_id,
                    ConversationMessage.source_kind == "telegram",
                    ConversationMessage.source_message_id == source_message_id,
                )
            )
        if row is None:
            raise RuntimeError("incoming conversation message could not be recorded")
        return _conversation_record(row)

    async def list_after(
        self, user_id: UUID, message_id: UUID | None
    ) -> list[ConversationMessageRecord]:
        statement = select(ConversationMessage).where(
            ConversationMessage.user_id == user_id
        )
        if message_id is not None:
            cursor = await self._session.scalar(
                select(ConversationMessage).where(
                    ConversationMessage.id == message_id,
                    ConversationMessage.user_id == user_id,
                )
            )
            if cursor is None:
                raise ValueError("conversation cursor does not belong to the user")
            statement = statement.where(
                or_(
                    ConversationMessage.occurred_at > cursor.occurred_at,
                    and_(
                        ConversationMessage.occurred_at == cursor.occurred_at,
                        ConversationMessage.id > cursor.id,
                    ),
                )
            )
        rows = await self._session.scalars(
            statement.order_by(
                ConversationMessage.occurred_at,
                ConversationMessage.id,
            )
        )
        return [_conversation_record(row) for row in rows]


class SqlAlchemyPersonaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord:
        await self._session.execute(
            pg_insert(PersonaCursor)
            .values(user_id=user_id, persona="")
            .on_conflict_do_nothing(index_elements=[PersonaCursor.user_id])
        )
        row = await self._session.scalar(
            select(PersonaCursor).where(PersonaCursor.user_id == user_id)
        )
        if row is None:
            raise LookupError("user was not found for persona cursor")
        return _cursor_record(row)

    async def advance(
        self,
        user_id: UUID,
        *,
        persona: str,
        last_message_id: UUID,
        generated_at: datetime,
    ) -> None:
        message = await self._session.scalar(
            select(ConversationMessage.id).where(
                ConversationMessage.id == last_message_id,
                ConversationMessage.user_id == user_id,
            )
        )
        if message is None:
            raise ValueError("last message does not belong to the user")
        await self.get_or_create(user_id)
        await self._session.execute(
            update(PersonaCursor)
            .where(PersonaCursor.user_id == user_id)
            .values(
                persona=persona,
                last_message_id=last_message_id,
                generated_at=generated_at,
                updated_at=generated_at,
            )
        )


class SqlAlchemyMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_eligible_normal(
        self, service_id: UUID, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        excluded = exists(
            select(HumanMatchExclusion.id).where(
                HumanMatchExclusion.request_id == request_id,
                HumanMatchExclusion.responder_profile_id == OperationalProfile.id,
            )
        )
        rows = await self._session.execute(
            select(OperationalProfile, User)
            .join(User, User.id == OperationalProfile.user_id)
            .join(ServiceAttendance, ServiceAttendance.user_id == User.id)
            .where(
                User.role == OperationalRole.SERVER,
                ServiceAttendance.service_id == service_id,
                ServiceAttendance.ended_at.is_(None),
                OperationalProfile.reserved_capacity < OperationalProfile.capacity,
                ~excluded,
            )
            .order_by(OperationalProfile.id)
        )
        return [_candidate_record(profile, user) for profile, user in rows.tuples()]

    async def list_eligible_safety(
        self, service_id: UUID | None, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        excluded = exists(
            select(HumanMatchExclusion.id).where(
                HumanMatchExclusion.request_id == request_id,
                HumanMatchExclusion.responder_profile_id == OperationalProfile.id,
            )
        )
        attendance_exists = (
            exists(
                select(ServiceAttendance.id).where(
                    ServiceAttendance.user_id == User.id,
                    ServiceAttendance.service_id == service_id,
                    ServiceAttendance.ended_at.is_(None),
                )
            )
            if service_id is not None
            else false()
        )
        rows = await self._session.execute(
            select(OperationalProfile, User)
            .join(User, User.id == OperationalProfile.user_id)
            .where(
                User.role.in_((OperationalRole.LEADER, OperationalRole.STAFF)),
                OperationalProfile.reserved_capacity < OperationalProfile.capacity,
                or_(OperationalProfile.always_available.is_(True), attendance_exists),
                ~excluded,
            )
            .order_by(OperationalProfile.id)
        )
        return [_candidate_record(profile, user) for profile, user in rows.tuples()]

    async def reserve_ranked(
        self, request_id: UUID, ranked_profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord | None:
        active_assignment = await self._session.scalar(
            select(HumanMatchAssignment)
            .where(
                HumanMatchAssignment.request_id == request_id,
                HumanMatchAssignment.released_at.is_(None),
            )
            .with_for_update()
        )
        if active_assignment is not None:
            return None
        request = await self._session.scalar(
            select(HumanMatchRequest)
            .where(HumanMatchRequest.id == request_id)
            .with_for_update()
        )
        if request is None:
            raise LookupError("human match request was not found")
        active_assignment = await self._session.scalar(
            select(HumanMatchAssignment)
            .where(
                HumanMatchAssignment.request_id == request_id,
                HumanMatchAssignment.released_at.is_(None),
            )
            .with_for_update()
        )
        if active_assignment is not None:
            return None
        for profile_id in ranked_profile_ids:
            excluded = await self._session.scalar(
                select(HumanMatchExclusion.id).where(
                    HumanMatchExclusion.request_id == request_id,
                    HumanMatchExclusion.responder_profile_id == profile_id,
                )
            )
            if excluded is not None:
                continue
            profile = await self._session.scalar(
                update(OperationalProfile)
                .where(
                    OperationalProfile.id == profile_id,
                    OperationalProfile.reserved_capacity < OperationalProfile.capacity,
                )
                .values(reserved_capacity=OperationalProfile.reserved_capacity + 1)
                .returning(OperationalProfile.id)
            )
            if profile is None:
                continue
            reservation_id = await self._session.scalar(
                pg_insert(CapacityReservation)
                .values(
                    operational_profile_id=profile_id,
                    request_id=request_id,
                    reserved_at=now,
                )
                .on_conflict_do_nothing()
                .returning(CapacityReservation.id)
            )
            if reservation_id is None:
                await self._session.execute(
                    update(OperationalProfile)
                    .where(
                        OperationalProfile.id == profile_id,
                        OperationalProfile.reserved_capacity > 0,
                    )
                    .values(reserved_capacity=OperationalProfile.reserved_capacity - 1)
                )
                continue
            assignment = await self._session.scalar(
                pg_insert(HumanMatchAssignment)
                .values(
                    request_id=request_id,
                    responder_profile_id=profile_id,
                    capacity_reservation_id=reservation_id,
                    assigned_at=now,
                )
                .on_conflict_do_nothing()
                .returning(HumanMatchAssignment)
            )
            if assignment is not None:
                return _assignment_record(assignment)
            await self._session.execute(
                update(CapacityReservation)
                .where(CapacityReservation.id == reservation_id)
                .values(released_at=now)
            )
            await self._session.execute(
                update(OperationalProfile)
                .where(
                    OperationalProfile.id == profile_id,
                    OperationalProfile.reserved_capacity > 0,
                )
                .values(reserved_capacity=OperationalProfile.reserved_capacity - 1)
            )
        return None

    async def release_and_exclude(
        self, request_id: UUID, profile_id: UUID, *, reason: str, now: datetime
    ) -> None:
        assignment = await self._session.scalar(
            select(HumanMatchAssignment)
            .where(
                HumanMatchAssignment.request_id == request_id,
                HumanMatchAssignment.responder_profile_id == profile_id,
                HumanMatchAssignment.released_at.is_(None),
            )
            .with_for_update()
        )
        if assignment is not None:
            assignment.released_at = now
            assignment.release_reason = reason
            reservation = await self._session.scalar(
                select(CapacityReservation)
                .where(
                    CapacityReservation.id == assignment.capacity_reservation_id,
                    CapacityReservation.released_at.is_(None),
                )
                .with_for_update()
            )
            if reservation is not None:
                reservation.released_at = now
                await self._session.execute(
                    update(OperationalProfile)
                    .where(
                        OperationalProfile.id == reservation.operational_profile_id,
                        OperationalProfile.reserved_capacity > 0,
                    )
                    .values(reserved_capacity=OperationalProfile.reserved_capacity - 1)
                )
        await self._session.execute(
            pg_insert(HumanMatchExclusion)
            .values(
                request_id=request_id,
                responder_profile_id=profile_id,
                created_at=now,
            )
            .on_conflict_do_nothing()
        )


class SqlAlchemyOpenSelectionRepository:
    def __init__(
        self,
        session: AsyncSession,
        locked_user_ids: set[UUID],
    ) -> None:
        self._session = session
        self._locked_user_ids = locked_user_ids

    async def list_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]:
        rows = await self._session.scalars(
            select_from_open_selection(user_id, now).order_by(
                OpenFlowSelection.last_focused_at.desc(), OpenFlowSelection.id
            )
        )
        return [_open_selection_state(row) for row in rows]

    async def apply(
        self, transition: SelectionTransition | CheckpointReturnTransition
    ) -> None:
        source = await self._source_for(transition)
        user_id = source.user_id
        flow_version_id = source.flow_version_id
        service_id = source.service_id
        delete_ids = (
            transition.delete_selection_ids
            if isinstance(transition, SelectionTransition)
            else frozenset()
        )
        reusable_ids = transition.reusable_past_selection_ids
        current_keys = transition.current_selection_ids
        focused_at = (
            transition.checkpoint_return.focused_at
            if isinstance(transition, SelectionTransition)
            and transition.checkpoint_return is not None
            else transition.focused_at
            if isinstance(transition, CheckpointReturnTransition)
            else None
        )
        if delete_ids:
            await self._session.execute(
                delete(OpenFlowSelection).where(
                    OpenFlowSelection.id.in_(delete_ids),
                    OpenFlowSelection.user_id == user_id,
                    OpenFlowSelection.flow_version_id == flow_version_id,
                    OpenFlowSelection.service_id.is_not_distinct_from(service_id),
                )
            )
        if reusable_ids:
            await self._session.execute(
                update(OpenFlowSelection)
                .where(
                    OpenFlowSelection.id.in_(reusable_ids),
                    OpenFlowSelection.user_id == user_id,
                    OpenFlowSelection.flow_version_id == flow_version_id,
                    OpenFlowSelection.service_id.is_not_distinct_from(service_id),
                )
                .values(is_current=False)
            )
        if current_keys:
            statement = update(OpenFlowSelection).where(
                OpenFlowSelection.user_id == user_id,
                OpenFlowSelection.flow_version_id == flow_version_id,
                OpenFlowSelection.service_id.is_not_distinct_from(service_id),
                OpenFlowSelection.parent_flow_key.in_(current_keys),
                OpenFlowSelection.expires_at.is_(None),
            )
            values: dict[str, object] = {"is_current": True}
            if focused_at is not None:
                values["last_focused_at"] = focused_at
            await self._session.execute(statement.values(**values))
        if isinstance(transition, SelectionTransition):
            for selection in transition.upsert_selections:
                if (
                    selection.user_id,
                    selection.flow_version_id,
                    selection.service_id,
                ) != (user_id, flow_version_id, service_id):
                    raise ValueError(
                        "selection upsert must stay within the source branch"
                    )
                await self._upsert(selection)

    async def expire_service_bound(self, service_id: UUID, *, at: datetime) -> int:
        selection_ids = list(
            await self._session.scalars(
                select(OpenFlowSelection.id).where(
                    OpenFlowSelection.service_id == service_id,
                    OpenFlowSelection.expires_at.is_(None),
                )
            )
        )
        if not selection_ids:
            return 0
        await self._session.execute(
            update(OpenFlowSelection)
            .where(
                OpenFlowSelection.id.in_(selection_ids),
            )
            .values(expires_at=at, is_current=False)
        )
        return len(selection_ids)

    async def _source_for(
        self, transition: SelectionTransition | CheckpointReturnTransition
    ) -> OpenFlowSelection:
        row = await self._session.scalar(
            select(OpenFlowSelection)
            .where(OpenFlowSelection.id == transition.source_selection_id)
            .with_for_update()
        )
        if row is None:
            raise LookupError("selection transition source was not found")
        if row.user_id not in self._locked_user_ids:
            raise RuntimeError("selection transition user is not locked")
        return row

    async def _upsert(self, selection: OpenSelectionState) -> None:
        row = await self._session.scalar(
            select(OpenFlowSelection)
            .where(
                OpenFlowSelection.user_id == selection.user_id,
                OpenFlowSelection.flow_version_id == selection.flow_version_id,
                OpenFlowSelection.parent_flow_key == selection.parent_flow_key,
                OpenFlowSelection.service_id.is_not_distinct_from(selection.service_id),
            )
            .with_for_update()
        )
        values = {
            "is_current": selection.is_current,
            "is_global_interruptive": selection.is_global_interruptive,
            "ancestor_flow_keys": list(selection.ancestor_flow_keys),
            "checkpoint_flow_keys": list(selection.checkpoint_flow_keys),
            "opened_at": selection.opened_at,
            "last_focused_at": selection.last_focused_at,
            "expires_at": None,
        }
        if row is None:
            self._session.add(
                OpenFlowSelection(
                    id=selection.id,
                    user_id=selection.user_id,
                    flow_version_id=selection.flow_version_id,
                    parent_flow_key=selection.parent_flow_key,
                    service_id=selection.service_id,
                    **values,
                )
            )
            return
        for field, value in values.items():
            setattr(row, field, value)


class SqlAlchemyDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_timestamp_delivery(
        self, service_timestamp_id: UUID, user_id: UUID
    ) -> bool:
        row = await self._session.scalar(
            pg_insert(TimestampDeliveryClaim)
            .values(
                service_timestamp_id=service_timestamp_id,
                user_id=user_id,
                claimed_at=_now(),
                status="claimed",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    TimestampDeliveryClaim.service_timestamp_id,
                    TimestampDeliveryClaim.user_id,
                ]
            )
            .returning(TimestampDeliveryClaim.id)
        )
        return row is not None

    async def enqueue(self, delivery: NewOutboundDelivery) -> OutboundDeliveryRecord:
        row = await self._session.scalar(
            pg_insert(OutboundDelivery)
            .values(
                idempotency_key=delivery.idempotency_key,
                user_id=delivery.user_id,
                telegram_chat_id=delivery.telegram_chat_id,
                kind=delivery.kind,
                payload=delivery.payload,
                status=delivery.status,
                eligible_at=delivery.eligible_at or _now(),
            )
            .on_conflict_do_nothing(index_elements=[OutboundDelivery.idempotency_key])
            .returning(OutboundDelivery)
        )
        if row is None:
            row = await self._session.scalar(
                select(OutboundDelivery).where(
                    OutboundDelivery.idempotency_key == delivery.idempotency_key
                )
            )
        if row is None:
            raise RuntimeError("outbound delivery could not be enqueued")
        return _delivery_record(row)

    async def claim_next_safe(
        self,
        *,
        now: datetime,
        lease_duration: timedelta = DEFAULT_SAFE_CLAIM_LEASE,
    ) -> OutboundDeliveryRecord | None:
        """Atomically lease due work or an expired unstarted lease, never a send."""

        _validate_lease_duration(lease_duration)

        row = await self._session.scalar(
            select(OutboundDelivery)
            .where(
                or_(
                    and_(
                        OutboundDelivery.status.in_(("pending", "retry")),
                        OutboundDelivery.eligible_at <= now,
                    ),
                    and_(
                        OutboundDelivery.status == "claimed",
                        OutboundDelivery.claim_expires_at.is_not(None),
                        OutboundDelivery.claim_expires_at <= now,
                    ),
                )
            )
            .order_by(OutboundDelivery.created_at, OutboundDelivery.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "claimed"
        row.claim_token = uuid4()
        row.claim_expires_at = now + lease_duration
        return _delivery_record(row)

    async def renew_claim(
        self,
        delivery_id: UUID,
        *,
        claim_token: UUID,
        now: datetime,
        lease_duration: timedelta = DEFAULT_SAFE_CLAIM_LEASE,
    ) -> OutboundDeliveryRecord:
        """Extend a still-live claim only for its exact opaque owner token."""

        _validate_lease_duration(lease_duration)
        delivery = await self._session.scalar(
            select(OutboundDelivery)
            .where(OutboundDelivery.id == delivery_id)
            .with_for_update()
        )
        if (
            delivery is None
            or delivery.status != "claimed"
            or delivery.claim_token != claim_token
            or delivery.claim_expires_at is None
            or delivery.claim_expires_at <= now
        ):
            raise DeliveryClaimLostError("outbound delivery claim was lost")
        delivery.claim_expires_at = now + lease_duration
        return _delivery_record(delivery)

    async def start_attempt(
        self,
        delivery_id: UUID,
        correlation_id: UUID,
        *,
        claim_token: UUID,
        started_at: datetime,
    ) -> DeliveryAttemptRecord:
        delivery = await self._session.scalar(
            select(OutboundDelivery)
            .where(OutboundDelivery.id == delivery_id)
            .with_for_update()
        )
        if (
            delivery is None
            or delivery.status != "claimed"
            or delivery.claim_token != claim_token
            or delivery.claim_expires_at is None
            or delivery.claim_expires_at <= started_at
        ):
            raise DeliveryClaimLostError("outbound delivery claim was lost")
        attempt_number = await self._session.scalar(
            select(
                func.coalesce(func.max(OutboundDeliveryAttempt.attempt_number), 0)
            ).where(OutboundDeliveryAttempt.delivery_id == delivery_id)
        )
        next_attempt_number = 1 if attempt_number is None else attempt_number + 1
        attempt = OutboundDeliveryAttempt(
            delivery_id=delivery_id,
            attempt_number=next_attempt_number,
            started_at=started_at,
            correlation_id=correlation_id,
        )
        delivery.status = "sending"
        delivery.claim_token = None
        delivery.claim_expires_at = None
        self._session.add(attempt)
        await self._session.flush()
        return _attempt_record(attempt)

    async def finish_attempt(
        self,
        delivery_id: UUID,
        attempt_id: UUID,
        outcome: DeliveryOutcome,
        *,
        now: datetime,
        retry_at: datetime | None = None,
        safe_error: str | None = None,
        confirmed_telegram_message_id: int | None = None,
    ) -> None:
        attempt = await self._session.scalar(
            select(OutboundDeliveryAttempt)
            .where(
                OutboundDeliveryAttempt.id == attempt_id,
                OutboundDeliveryAttempt.delivery_id == delivery_id,
            )
            .with_for_update()
        )
        if attempt is None:
            raise LookupError("outbound delivery attempt was not found")
        if attempt.finished_at is not None:
            raise RuntimeError("outbound delivery attempt is already finished")
        delivery = await self._session.scalar(
            select(OutboundDelivery)
            .where(OutboundDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            raise LookupError("outbound delivery was not found")
        if delivery.status != "sending":
            raise RuntimeError("outbound delivery is not sending")
        if (
            confirmed_telegram_message_id is not None
            and confirmed_telegram_message_id <= 0
        ):
            raise ValueError("confirmed Telegram message id must be positive")
        attempt.finished_at = now
        attempt.outcome = outcome
        attempt.safe_error = safe_error
        delivery.status = outcome
        delivery.claim_token = None
        delivery.claim_expires_at = None
        if outcome == "retry":
            delivery.eligible_at = retry_at or now
        if outcome == "sent":
            delivery.sent_at = now
        if confirmed_telegram_message_id is not None:
            delivery.confirmed_telegram_message_id = confirmed_telegram_message_id


class SqlAlchemyDiagnosticRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        correlation_id: UUID,
        severity: str,
        safe_summary: str,
        safe_context: dict[str, JsonValue],
        at: datetime,
    ) -> DiagnosticRecord:
        row = await self._session.scalar(
            pg_insert(DiagnosticModel)
            .values(
                correlation_id=correlation_id,
                severity=severity,
                safe_summary=safe_summary,
                safe_context=safe_context,
                created_at=at,
            )
            .returning(DiagnosticModel)
        )
        if row is None:
            raise RuntimeError("diagnostic record could not be created")
        return _diagnostic_record(row)

    async def enqueue_admin_notifications(
        self, diagnostic_id: UUID, *, at: datetime
    ) -> int:
        admin_ids = await self._session.scalars(
            select(User.id).where(User.is_admin.is_(True))
        )
        inserted = 0
        for admin_id in admin_ids:
            row = await self._session.scalar(
                pg_insert(AdminNotificationDelivery)
                .values(
                    diagnostic_id=diagnostic_id,
                    admin_user_id=admin_id,
                    status="pending",
                    created_at=at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        AdminNotificationDelivery.diagnostic_id,
                        AdminNotificationDelivery.admin_user_id,
                    ]
                )
                .returning(AdminNotificationDelivery.id)
            )
            inserted += row is not None
        return inserted


class SqlAlchemyFlowVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(
        self,
        definition: PublishedFlowDefinition,
        *,
        scope_kind: FlowScopeKind,
        service_id: UUID | None,
        published_by_user_id: UUID | None,
    ) -> FlowVersionRecord:
        row = await self._session.scalar(
            pg_insert(FlowVersion)
            .values(
                scope_kind=scope_kind,
                service_id=service_id,
                root_flow_key=_root_flow_key(definition),
                definition=definition.document,
                content_hash=definition.content_hash,
                published_at=_now(),
                published_by_user_id=published_by_user_id,
            )
            .on_conflict_do_nothing()
            .returning(FlowVersion)
        )
        if row is None:
            row = await self._session.scalar(
                select(FlowVersion).where(
                    FlowVersion.scope_kind == scope_kind,
                    FlowVersion.service_id.is_not_distinct_from(service_id),
                    FlowVersion.root_flow_key == _root_flow_key(definition),
                    FlowVersion.content_hash == definition.content_hash,
                )
            )
        if row is None:
            raise RuntimeError("flow version publication could not be resolved")
        return _flow_version_record(row)

    async def get(self, flow_version_id: UUID) -> FlowVersionRecord:
        row = await self._session.get(FlowVersion, flow_version_id)
        if row is None:
            raise LookupError("flow version was not found")
        return _flow_version_record(row)


def _root_flow_key(definition: PublishedFlowDefinition) -> str:
    root_key = definition.document.get("key")
    if not isinstance(root_key, str):
        raise TypeError("published definition has no root flow key")
    return root_key


def select_from_open_selection(
    user_id: UUID, now: datetime
) -> Select[tuple[OpenFlowSelection]]:
    """Return the canonical active-or-reusable selection query for one user."""

    return select(OpenFlowSelection).where(
        OpenFlowSelection.user_id == user_id,
        or_(
            OpenFlowSelection.expires_at.is_(None),
            OpenFlowSelection.expires_at > now,
        ),
    )


def _open_selection_state(row: OpenFlowSelection) -> OpenSelectionState:
    return OpenSelectionState(
        id=row.id,
        user_id=row.user_id,
        flow_version_id=row.flow_version_id,
        parent_flow_key=row.parent_flow_key,
        service_id=row.service_id,
        is_current=row.is_current,
        is_global_interruptive=row.is_global_interruptive,
        ancestor_flow_keys=tuple(row.ancestor_flow_keys),
        checkpoint_flow_keys=tuple(row.checkpoint_flow_keys),
        opened_at=row.opened_at,
        last_focused_at=row.last_focused_at,
    )
