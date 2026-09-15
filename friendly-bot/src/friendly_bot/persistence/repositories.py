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
from sqlalchemy.orm import aliased

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
    PendingFlowIntent,
    PersonaCursor,
    ProcessedTelegramUpdate,
    Service,
    ServiceAttendance,
    ServiceAudience,
    ServiceTimestamp,
    TelegramOutboundPause,
    TelegramPollState,
    TimestampDeliveryClaim,
    User,
)
from friendly_bot.persistence.models import (
    DiagnosticRecord as DiagnosticModel,
)

type LoginAttachmentKind = Literal["attached", "occupied", "not_found"]
type DeliveryOutcome = Literal["sent", "retry", "rejected", "uncertain"]
type MatchRequestKind = Literal["normal", "safety"]
type MatchRequestStatus = Literal["pending", "reserved", "confirmed", "resolved"]
type MeetingPreference = Literal["nbnc_joins_human", "human_joins_nbnc"]
DEFAULT_SAFE_CLAIM_LEASE = timedelta(minutes=1)
_NO_TELEGRAM_OUTBOUND_PAUSE_UNTIL = datetime(1970, 1, 1, tzinfo=UTC)
SERVICE_INTERACTION_END_RELEASE_REASON = "service_interaction_ended"


class DeliveryClaimLostError(RuntimeError):
    """Raised when a worker no longer owns a safe outbound-delivery claim."""


class ServiceInteractionClosedError(RuntimeError):
    """Raised when a service-bound write crosses the interaction boundary."""

    def __init__(self, service_id: UUID) -> None:
        super().__init__("service interaction is closed")
        self.service_id = service_id


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
class LoginAttachmentResult:
    """The safe outcome of one exclusive operational-profile attachment."""

    kind: LoginAttachmentKind
    is_first_ever_attachment: bool = False


@dataclass(frozen=True, slots=True)
class NewService:
    """The complete mutable service facts accepted from an approved seed publisher."""

    key: str
    name: str
    timezone: str
    highkey: bool
    doors_open_at: datetime
    doors_close_at: datetime
    service_starts_at: datetime
    service_ends_at: datetime
    interaction_ends_at: datetime


@dataclass(frozen=True, slots=True)
class NewServiceTimestamp:
    """One timestamp-to-immutable-flow binding from an approved service seed."""

    key: str
    occurs_at: datetime
    audience: ServiceAudience
    flow_version_id: UUID
    root_flow_key: str


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    id: UUID
    key: str
    highkey: bool
    doors_open_at: datetime
    doors_close_at: datetime
    interaction_ends_at: datetime
    interaction_closed_at: datetime | None = None
    name: str = ""


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
class ServiceBoundSelectionExpiry:
    """The durable users and selection count expired for one service boundary."""

    expired_selection_count: int
    affected_user_ids: frozenset[UUID]


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
class NewPendingFlowIntent:
    """A queued interactive flow reference with no retained free-form input."""

    user_id: UUID
    flow_key: str
    flow_version_id: UUID
    service_id: UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingFlowIntentRecord(NewPendingFlowIntent):
    """One persisted pending flow reference in user-owned execution order."""

    id: UUID
    position: int


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
class MatchRequestRecord:
    """One requester-owned human-match state without an ORM object escape."""

    id: UUID
    requester_user_id: UUID
    service_id: UUID | None
    kind: MatchRequestKind
    interest: str | None
    meeting_preference: MeetingPreference | None
    status: MatchRequestStatus
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class MatchResponderRecord:
    """The actively attached Telegram recipient for one current/previous assignment."""

    assignment: MatchAssignmentRecord
    profile_id: UUID
    recipient_user_id: UUID
    telegram_chat_id: int
    display_name: str | None
    cg_name: str | None
    telegram_contact_url: str | None


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

    async def require_by_id(self, user_id: UUID) -> UserRecord: ...

    async def set_display_name(
        self, user_id: UUID, display_name: str, *, at: datetime
    ) -> UserRecord: ...


class OperationalProfileRepository(Protocol):
    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord | None: ...

    async def find_active_for_login_user(
        self, user_id: UUID
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
    async def get_by_key(self, service_key: str) -> ServiceRecord: ...

    async def get(self, service_id: UUID) -> ServiceRecord: ...

    async def upsert(self, service: NewService) -> ServiceRecord: ...

    async def upsert_timestamp(
        self, timestamp: NewServiceTimestamp, *, service_id: UUID
    ) -> ServiceTimestampRecord: ...

    async def list_ongoing(self, *, now: datetime) -> list[ServiceRecord]: ...

    async def claim_interaction_closure(
        self, service_id: UUID, *, now: datetime
    ) -> bool: ...

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


class PendingIntentRepository(Protocol):
    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]: ...

    async def append(
        self, intent: NewPendingFlowIntent, *, max_per_user: int
    ) -> PendingFlowIntentRecord: ...

    async def delete(self, intent_id: UUID) -> None: ...

    async def delete_expired(self, user_id: UUID, *, now: datetime) -> int: ...


class MatchRepository(Protocol):
    async def get_or_create_active_request(
        self,
        *,
        requester_user_id: UUID,
        service_id: UUID | None,
        kind: MatchRequestKind,
        now: datetime,
    ) -> MatchRequestRecord: ...

    async def require_request(
        self, request_id: UUID, *, requester_user_id: UUID
    ) -> MatchRequestRecord: ...

    async def set_interest(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        interest: str,
        now: datetime,
    ) -> MatchRequestRecord: ...

    async def confirm(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        meeting_preference: MeetingPreference,
        now: datetime,
    ) -> MatchRequestRecord: ...

    async def current_responder(
        self, request_id: UUID, *, requester_user_id: UUID
    ) -> MatchResponderRecord | None: ...

    async def release_active(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        reason: str,
        now: datetime,
    ) -> MatchResponderRecord | None: ...

    async def exclude_responder(
        self,
        request_id: UUID,
        profile_id: UUID,
        *,
        requester_user_id: UUID,
        now: datetime,
    ) -> None: ...

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

    async def release_service_bound(self, service_id: UUID, *, at: datetime) -> int: ...


class OpenSelectionRepository(Protocol):
    async def list_for_user(
        self, user_id: UUID, *, now: datetime
    ) -> list[OpenSelectionState]: ...

    async def open_root(
        self, root: OpenSelectionState, *, at: datetime
    ) -> OpenSelectionState: ...

    async def apply(
        self,
        transition: SelectionTransition | CheckpointReturnTransition,
        *,
        at: datetime,
    ) -> None: ...

    async def expire_service_bound(
        self, service_id: UUID, *, at: datetime
    ) -> ServiceBoundSelectionExpiry: ...


class DeliveryRepository(Protocol):
    async def claim_timestamp_delivery(
        self, service_timestamp_id: UUID, user_id: UUID, *, now: datetime
    ) -> bool: ...

    async def enqueue(
        self, delivery: NewOutboundDelivery
    ) -> OutboundDeliveryRecord: ...

    async def claim_next_safe(
        self, *, now: datetime, lease_duration: timedelta = DEFAULT_SAFE_CLAIM_LEASE
    ) -> OutboundDeliveryRecord | None: ...

    async def extend_telegram_pause(self, *, pause_until: datetime) -> datetime: ...

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
        interaction_closed_at=row.interaction_closed_at,
        name=row.name,
    )


async def _lock_open_service(
    session: AsyncSession, service_id: UUID, *, at: datetime
) -> Service:
    """Fence service-bound mutations behind durable interaction closure state."""

    service = await _lock_service(session, service_id)
    if service.interaction_closed_at is not None or at >= service.interaction_ends_at:
        raise ServiceInteractionClosedError(service_id)
    return service


async def _lock_timestamp_delivery_service(
    session: AsyncSession, timestamp: ServiceTimestamp, *, at: datetime
) -> Service:
    """Allow only the configured terminal timestamp to enter the closure transaction."""

    service = await _lock_service(session, timestamp.service_id)
    is_terminal_timestamp = timestamp.occurs_at == service.interaction_ends_at
    if service.interaction_closed_at is not None or (
        at >= service.interaction_ends_at and not is_terminal_timestamp
    ):
        raise ServiceInteractionClosedError(service.id)
    return service


async def _lock_root_service(
    session: AsyncSession,
    *,
    flow_version_id: UUID,
    service_id: UUID,
    at: datetime,
) -> Service:
    """Permit an unclosed service's exact terminal timestamp root, and nothing else."""

    service = await _lock_service(session, service_id)
    if service.interaction_closed_at is not None:
        raise ServiceInteractionClosedError(service.id)
    if at < service.interaction_ends_at:
        return service
    terminal_timestamp_id = await session.scalar(
        select(ServiceTimestamp.id).where(
            ServiceTimestamp.service_id == service.id,
            ServiceTimestamp.flow_version_id == flow_version_id,
            ServiceTimestamp.occurs_at == service.interaction_ends_at,
        )
    )
    if terminal_timestamp_id is None:
        raise ServiceInteractionClosedError(service.id)
    return service


async def _lock_service(session: AsyncSession, service_id: UUID) -> Service:
    """Lock one service row without rejecting the lifecycle's own closed work."""

    service = await session.scalar(
        select(Service).where(Service.id == service_id).with_for_update()
    )
    if service is None:
        raise LookupError("service was not found")
    return service


async def _locked_match_request(
    session: AsyncSession, request_id: UUID, *, now: datetime
) -> HumanMatchRequest:
    """Take service then request locks before any service-bound match mutation."""

    service_scope = (
        await session.execute(
            select(HumanMatchRequest.service_id).where(
                HumanMatchRequest.id == request_id
            )
        )
    ).one_or_none()
    if service_scope is None:
        raise LookupError("human match request was not found")
    service_id = service_scope._tuple()[0]
    if service_id is not None:
        await _lock_open_service(session, service_id, at=now)
    request = await session.scalar(
        select(HumanMatchRequest)
        .where(HumanMatchRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise LookupError("human match request was not found")
    if request.service_id != service_id:
        raise RuntimeError("human match request service scope changed")
    return request


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


def _pending_intent_record(row: PendingFlowIntent) -> PendingFlowIntentRecord:
    return PendingFlowIntentRecord(
        id=row.id,
        user_id=row.user_id,
        flow_key=row.flow_key,
        flow_version_id=row.flow_version_id,
        service_id=row.service_id,
        position=row.position,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def _candidate_record(
    profile: OperationalProfile, role_owner: User, recipient: User
) -> MatchCandidateRecord:
    return MatchCandidateRecord(
        profile_id=profile.id,
        user_id=recipient.id,
        role=role_owner.role,
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


def _match_request_record(row: HumanMatchRequest) -> MatchRequestRecord:
    """Freeze the only F01 request fields consumed by matching composition."""

    if row.kind not in {"normal", "safety"}:
        raise RuntimeError("human match request kind is invalid")
    if row.status not in {"pending", "reserved", "confirmed", "resolved"}:
        raise RuntimeError("human match request status is invalid")
    if row.meeting_preference not in {
        None,
        "nbnc_joins_human",
        "human_joins_nbnc",
    }:
        raise RuntimeError("human match request meeting preference is invalid")
    return MatchRequestRecord(
        id=row.id,
        requester_user_id=row.requester_user_id,
        service_id=row.service_id,
        kind=cast(MatchRequestKind, row.kind),
        interest=row.interest,
        meeting_preference=cast(MeetingPreference | None, row.meeting_preference),
        status=cast(MatchRequestStatus, row.status),
        created_at=row.created_at,
        resolved_at=row.resolved_at,
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

    async def require_by_id(self, user_id: UUID) -> UserRecord:
        row = await self._session.get(User, user_id)
        if row is None:
            raise LookupError("user was not found")
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

    async def find_active_for_login_user(
        self, user_id: UUID
    ) -> OperationalProfileRecord | None:
        row = await self._session.scalar(
            select(OperationalProfile)
            .join(
                OperationalLogin,
                OperationalLogin.operational_profile_id == OperationalProfile.id,
            )
            .where(
                OperationalLogin.user_id == user_id,
                OperationalLogin.detached_at.is_(None),
            )
            .with_for_update()
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
            return LoginAttachmentResult("not_found")
        attachment_id = await self._session.scalar(
            pg_insert(OperationalLogin)
            .values(
                operational_profile_id=profile_id,
                user_id=user_id,
                attached_at=at,
            )
            .on_conflict_do_nothing()
            .returning(OperationalLogin.id)
        )
        if attachment_id is None:
            return LoginAttachmentResult("occupied")
        has_prior_attachment = await self._session.scalar(
            select(
                exists().where(
                    OperationalLogin.operational_profile_id == profile_id,
                    OperationalLogin.id != attachment_id,
                )
            )
        )
        return LoginAttachmentResult(
            "attached", is_first_ever_attachment=not bool(has_prior_attachment)
        )

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

    async def get_by_key(self, service_key: str) -> ServiceRecord:
        if type(service_key) is not str or not service_key:
            raise ValueError("service key must be nonempty")
        row = await self._session.scalar(
            select(Service).where(Service.key == service_key)
        )
        if row is None:
            raise LookupError("service was not found")
        return _service_record(row)

    async def get(self, service_id: UUID) -> ServiceRecord:
        row = await self._session.get(Service, service_id)
        if row is None:
            raise LookupError("service was not found")
        return _service_record(row)

    async def upsert(self, service: NewService) -> ServiceRecord:
        """Insert or refresh the non-closure fields of one stable service seed record."""

        if not isinstance(service, NewService):
            raise TypeError("service seed input is invalid")
        row = await self._session.scalar(
            pg_insert(Service)
            .values(
                key=service.key,
                name=service.name,
                timezone=service.timezone,
                highkey=service.highkey,
                doors_open_at=service.doors_open_at,
                doors_close_at=service.doors_close_at,
                service_starts_at=service.service_starts_at,
                service_ends_at=service.service_ends_at,
                interaction_ends_at=service.interaction_ends_at,
            )
            .on_conflict_do_update(
                index_elements=[Service.key],
                set_={
                    "name": service.name,
                    "timezone": service.timezone,
                    "highkey": service.highkey,
                    "doors_open_at": service.doors_open_at,
                    "doors_close_at": service.doors_close_at,
                    "service_starts_at": service.service_starts_at,
                    "service_ends_at": service.service_ends_at,
                    "interaction_ends_at": service.interaction_ends_at,
                },
            )
            .returning(Service)
        )
        if row is None:
            raise RuntimeError("service seed could not be upserted")
        return _service_record(row)

    async def upsert_timestamp(
        self, timestamp: NewServiceTimestamp, *, service_id: UUID
    ) -> ServiceTimestampRecord:
        """Keep one stable timestamp key bound to its current immutable root version."""

        if not isinstance(timestamp, NewServiceTimestamp):
            raise TypeError("service timestamp seed input is invalid")
        await _lock_service(self._session, service_id)
        row = await self._session.scalar(
            select(ServiceTimestamp)
            .where(
                ServiceTimestamp.service_id == service_id,
                ServiceTimestamp.key == timestamp.key,
            )
            .with_for_update()
        )
        if row is None:
            row = ServiceTimestamp(
                id=uuid4(),
                service_id=service_id,
                key=timestamp.key,
                occurs_at=timestamp.occurs_at,
                audience=timestamp.audience,
                flow_version_id=timestamp.flow_version_id,
                root_flow_key=timestamp.root_flow_key,
            )
            self._session.add(row)
            await self._session.flush()
        else:
            row.occurs_at = timestamp.occurs_at
            row.audience = timestamp.audience
            row.flow_version_id = timestamp.flow_version_id
            row.root_flow_key = timestamp.root_flow_key
        return _timestamp_record(row)

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

    async def claim_interaction_closure(
        self, service_id: UUID, *, now: datetime
    ) -> bool:
        """Atomically close a due interaction while retaining its row lock to commit."""

        claimed_id = await self._session.scalar(
            update(Service)
            .where(
                Service.id == service_id,
                Service.interaction_ends_at <= now,
                Service.interaction_closed_at.is_(None),
            )
            .values(interaction_closed_at=now)
            .returning(Service.id)
        )
        return claimed_id is not None

    async def list_due_timestamps(
        self, *, now: datetime
    ) -> list[ServiceTimestampRecord]:
        rows = await self._session.scalars(
            select(ServiceTimestamp)
            .join(Service, Service.id == ServiceTimestamp.service_id)
            .where(
                ServiceTimestamp.occurs_at <= now,
                Service.interaction_closed_at.is_(None),
                or_(
                    Service.interaction_ends_at > now,
                    ServiceTimestamp.occurs_at == Service.interaction_ends_at,
                ),
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
        await _lock_open_service(self._session, service_id, at=started_at)
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
        active_rows = list(
            await self._session.scalars(
                select(ServiceAttendance)
                .where(
                    ServiceAttendance.service_id == service_id,
                    ServiceAttendance.ended_at.is_(None),
                )
                .with_for_update()
            )
        )
        active_ids = [row.id for row in active_rows]
        if not active_rows:
            return 0
        ended_ids = list(
            await self._session.scalars(
                update(ServiceAttendance)
                .where(
                    ServiceAttendance.id.in_(active_ids),
                    ServiceAttendance.ended_at.is_(None),
                )
                .values(ended_at=ended_at, updated_at=ended_at)
                .returning(ServiceAttendance.id)
            )
        )
        return len(ended_ids)


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


class SqlAlchemyPendingIntentRepository:
    """Persist bounded per-user interactive flow references behind the user lock."""

    def __init__(self, session: AsyncSession, locked_user_ids: set[UUID]) -> None:
        self._session = session
        self._locked_user_ids = locked_user_ids

    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]:
        rows = await self._session.scalars(
            select(PendingFlowIntent)
            .where(
                PendingFlowIntent.user_id == user_id,
                PendingFlowIntent.expires_at > now,
            )
            .order_by(PendingFlowIntent.position, PendingFlowIntent.id)
        )
        return [_pending_intent_record(row) for row in rows]

    async def append(
        self, intent: NewPendingFlowIntent, *, max_per_user: int
    ) -> PendingFlowIntentRecord:
        self._require_locked(intent.user_id)
        _validate_pending_intent(intent, max_per_user=max_per_user)
        await self._delete_expired(intent.user_id, now=intent.created_at)
        duplicate = await self._session.scalar(
            select(PendingFlowIntent)
            .where(
                PendingFlowIntent.user_id == intent.user_id,
                PendingFlowIntent.flow_version_id == intent.flow_version_id,
                PendingFlowIntent.flow_key == intent.flow_key,
            )
            .with_for_update()
        )
        next_position = await self._next_position(intent.user_id)
        if duplicate is not None:
            duplicate.service_id = intent.service_id
            duplicate.position = next_position
            duplicate.created_at = intent.created_at
            duplicate.expires_at = intent.expires_at
            await self._session.flush()
            return _pending_intent_record(duplicate)

        rows = list(
            await self._session.scalars(
                select(PendingFlowIntent)
                .where(PendingFlowIntent.user_id == intent.user_id)
                .order_by(PendingFlowIntent.position, PendingFlowIntent.id)
                .with_for_update()
            )
        )
        for row in rows[: max(0, len(rows) - max_per_user + 1)]:
            await self._session.delete(row)
        record = PendingFlowIntent(
            user_id=intent.user_id,
            flow_key=intent.flow_key,
            flow_version_id=intent.flow_version_id,
            service_id=intent.service_id,
            position=next_position,
            created_at=intent.created_at,
            expires_at=intent.expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return _pending_intent_record(record)

    async def delete(self, intent_id: UUID) -> None:
        row = await self._session.scalar(
            select(PendingFlowIntent)
            .where(PendingFlowIntent.id == intent_id)
            .with_for_update()
        )
        if row is None:
            return
        self._require_locked(row.user_id)
        await self._session.delete(row)

    async def delete_expired(self, user_id: UUID, *, now: datetime) -> int:
        self._require_locked(user_id)
        return await self._delete_expired(user_id, now=now)

    async def _delete_expired(self, user_id: UUID, *, now: datetime) -> int:
        deleted = await self._session.scalars(
            delete(PendingFlowIntent)
            .where(
                PendingFlowIntent.user_id == user_id,
                PendingFlowIntent.expires_at <= now,
            )
            .returning(PendingFlowIntent.id)
        )
        return len(list(deleted))

    async def _next_position(self, user_id: UUID) -> int:
        current = await self._session.scalar(
            select(func.max(PendingFlowIntent.position)).where(
                PendingFlowIntent.user_id == user_id
            )
        )
        return 0 if current is None else int(current) + 1

    def _require_locked(self, user_id: UUID) -> None:
        if user_id not in self._locked_user_ids:
            raise RuntimeError("pending intent user is not locked")


def _validate_pending_intent(
    intent: NewPendingFlowIntent, *, max_per_user: int
) -> None:
    if not isinstance(intent.user_id, UUID) or not isinstance(
        intent.flow_version_id, UUID
    ):
        raise TypeError("pending intent identity is invalid")
    if intent.service_id is not None and not isinstance(intent.service_id, UUID):
        raise TypeError("pending intent service identity is invalid")
    if type(intent.flow_key) is not str or not intent.flow_key:
        raise ValueError("pending intent flow key must be nonempty")
    if type(max_per_user) is not int or max_per_user < 1:
        raise ValueError("pending intent maximum must be positive")
    if intent.created_at.tzinfo is None or intent.expires_at.tzinfo is None:
        raise ValueError("pending intent timestamps must be timezone-aware")
    if intent.expires_at <= intent.created_at:
        raise ValueError("pending intent expiry must be after creation")


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
        role_owner = aliased(User)
        recipient = aliased(User)
        rows = await self._session.execute(
            select(OperationalProfile, role_owner, recipient)
            .join(role_owner, role_owner.id == OperationalProfile.user_id)
            .join(
                OperationalLogin,
                OperationalLogin.operational_profile_id == OperationalProfile.id,
            )
            .join(recipient, recipient.id == OperationalLogin.user_id)
            .join(ServiceAttendance, ServiceAttendance.user_id == recipient.id)
            .where(
                role_owner.role == OperationalRole.SERVER,
                OperationalLogin.detached_at.is_(None),
                recipient.telegram_user_id.is_not(None),
                ServiceAttendance.service_id == service_id,
                ServiceAttendance.ended_at.is_(None),
                OperationalProfile.reserved_capacity < OperationalProfile.capacity,
                ~excluded,
            )
            .order_by(OperationalProfile.id)
        )
        return [
            _candidate_record(profile, owner, attached)
            for profile, owner, attached in rows.tuples()
        ]

    async def list_eligible_safety(
        self, service_id: UUID | None, request_id: UUID
    ) -> list[MatchCandidateRecord]:
        excluded = exists(
            select(HumanMatchExclusion.id).where(
                HumanMatchExclusion.request_id == request_id,
                HumanMatchExclusion.responder_profile_id == OperationalProfile.id,
            )
        )
        role_owner = aliased(User)
        recipient = aliased(User)
        attendance_exists = (
            exists(
                select(ServiceAttendance.id).where(
                    ServiceAttendance.user_id == recipient.id,
                    ServiceAttendance.service_id == service_id,
                    ServiceAttendance.ended_at.is_(None),
                )
            )
            if service_id is not None
            else false()
        )
        rows = await self._session.execute(
            select(OperationalProfile, role_owner, recipient)
            .join(role_owner, role_owner.id == OperationalProfile.user_id)
            .join(
                OperationalLogin,
                OperationalLogin.operational_profile_id == OperationalProfile.id,
            )
            .join(recipient, recipient.id == OperationalLogin.user_id)
            .where(
                role_owner.role.in_((OperationalRole.LEADER, OperationalRole.STAFF)),
                OperationalLogin.detached_at.is_(None),
                recipient.telegram_user_id.is_not(None),
                OperationalProfile.reserved_capacity < OperationalProfile.capacity,
                or_(OperationalProfile.always_available.is_(True), attendance_exists),
                ~excluded,
            )
            .order_by(OperationalProfile.id)
        )
        return [
            _candidate_record(profile, owner, attached)
            for profile, owner, attached in rows.tuples()
        ]

    async def get_or_create_active_request(
        self,
        *,
        requester_user_id: UUID,
        service_id: UUID | None,
        kind: MatchRequestKind,
        now: datetime,
    ) -> MatchRequestRecord:
        if kind not in {"normal", "safety"}:
            raise ValueError("human match request kind is invalid")
        if service_id is not None:
            await _lock_open_service(self._session, service_id, at=now)
        service_filter = (
            HumanMatchRequest.service_id.is_(None)
            if service_id is None
            else HumanMatchRequest.service_id == service_id
        )
        active = await self._session.scalar(
            select(HumanMatchRequest)
            .where(
                HumanMatchRequest.requester_user_id == requester_user_id,
                service_filter,
                HumanMatchRequest.kind == kind,
                HumanMatchRequest.status.in_(("pending", "reserved", "confirmed")),
            )
            .order_by(HumanMatchRequest.created_at.desc(), HumanMatchRequest.id)
            .with_for_update()
        )
        if active is not None:
            return _match_request_record(active)
        request = HumanMatchRequest(
            id=uuid4(),
            requester_user_id=requester_user_id,
            service_id=service_id,
            kind=kind,
            status="pending",
            created_at=now,
        )
        self._session.add(request)
        await self._session.flush()
        return _match_request_record(request)

    async def require_request(
        self, request_id: UUID, *, requester_user_id: UUID
    ) -> MatchRequestRecord:
        request = await self._session.scalar(
            select(HumanMatchRequest).where(
                HumanMatchRequest.id == request_id,
                HumanMatchRequest.requester_user_id == requester_user_id,
            )
        )
        if request is None:
            raise LookupError("human match request was not found")
        return _match_request_record(request)

    async def set_interest(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        interest: str,
        now: datetime,
    ) -> MatchRequestRecord:
        if type(interest) is not str or not interest:
            raise ValueError("human match interest must be nonempty")
        request = await self._owned_locked_request(request_id, requester_user_id, now)
        if request.status == "resolved":
            raise ValueError("human match request is resolved")
        request.interest = interest
        return _match_request_record(request)

    async def confirm(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        meeting_preference: MeetingPreference,
        now: datetime,
    ) -> MatchRequestRecord:
        if meeting_preference not in {"nbnc_joins_human", "human_joins_nbnc"}:
            raise ValueError("human match meeting preference is invalid")
        request = await self._owned_locked_request(request_id, requester_user_id, now)
        assignment = await self._active_assignment(request_id)
        if assignment is None:
            raise ValueError("human match request has no active assignment")
        if request.meeting_preference not in {None, meeting_preference}:
            raise ValueError("human match meeting preference conflicts")
        request.meeting_preference = meeting_preference
        request.status = "confirmed"
        return _match_request_record(request)

    async def current_responder(
        self, request_id: UUID, *, requester_user_id: UUID
    ) -> MatchResponderRecord | None:
        await self.require_request(request_id, requester_user_id=requester_user_id)
        assignment = await self._active_assignment(request_id)
        return (
            await self._responder_for_assignment(assignment)
            if assignment is not None
            else None
        )

    async def release_active(
        self,
        request_id: UUID,
        *,
        requester_user_id: UUID,
        reason: str,
        now: datetime,
    ) -> MatchResponderRecord | None:
        if type(reason) is not str or not reason:
            raise ValueError("human match release reason is invalid")
        request = await self._owned_locked_request(request_id, requester_user_id, now)
        assignment = await self._active_assignment(request_id)
        if assignment is None:
            return None
        responder = await self._responder_for_assignment(assignment)
        if responder is None:
            raise RuntimeError("active human match responder is unavailable")
        await self._release_assignment(assignment, reason=reason, at=now)
        request.status = "pending"
        return responder

    async def exclude_responder(
        self,
        request_id: UUID,
        profile_id: UUID,
        *,
        requester_user_id: UUID,
        now: datetime,
    ) -> None:
        await self._owned_locked_request(request_id, requester_user_id, now)
        assigned = await self._session.scalar(
            select(HumanMatchAssignment.id).where(
                HumanMatchAssignment.request_id == request_id,
                HumanMatchAssignment.responder_profile_id == profile_id,
            )
        )
        if assigned is None:
            raise ValueError("human match responder was not assigned to this request")
        await self._session.execute(
            pg_insert(HumanMatchExclusion)
            .values(
                request_id=request_id,
                responder_profile_id=profile_id,
                created_at=now,
            )
            .on_conflict_do_nothing()
        )

    async def reserve_ranked(
        self, request_id: UUID, ranked_profile_ids: list[UUID], *, now: datetime
    ) -> MatchAssignmentRecord | None:
        request = await _locked_match_request(self._session, request_id, now=now)
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
                request.status = "reserved"
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
        await _locked_match_request(self._session, request_id, now=now)
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

    async def release_service_bound(self, service_id: UUID, *, at: datetime) -> int:
        """Release only active capacity assigned through requests for one ended service."""

        await _lock_service(self._session, service_id)
        active_assignment_exists = exists(
            select(HumanMatchAssignment.id).where(
                HumanMatchAssignment.request_id == HumanMatchRequest.id,
                HumanMatchAssignment.released_at.is_(None),
            )
        )
        requests = list(
            await self._session.scalars(
                select(HumanMatchRequest)
                .where(
                    HumanMatchRequest.service_id == service_id,
                    active_assignment_exists,
                )
                .with_for_update()
            )
        )
        request_ids = [request.id for request in requests]
        if not request_ids:
            return 0
        assignments = list(
            await self._session.scalars(
                select(HumanMatchAssignment)
                .where(
                    HumanMatchAssignment.request_id.in_(request_ids),
                    HumanMatchAssignment.released_at.is_(None),
                )
                .with_for_update()
            )
        )
        for assignment in assignments:
            request = next(
                request for request in requests if request.id == assignment.request_id
            )
            assignment.released_at = at
            assignment.release_reason = SERVICE_INTERACTION_END_RELEASE_REASON
            request.status = "resolved"
            request.resolved_at = at
            reservation = await self._session.scalar(
                select(CapacityReservation)
                .where(
                    CapacityReservation.id == assignment.capacity_reservation_id,
                    CapacityReservation.released_at.is_(None),
                )
                .with_for_update()
            )
            if reservation is None:
                continue
            reservation.released_at = at
            released_profile_id = await self._session.scalar(
                update(OperationalProfile)
                .where(
                    OperationalProfile.id == reservation.operational_profile_id,
                    OperationalProfile.reserved_capacity > 0,
                )
                .values(reserved_capacity=OperationalProfile.reserved_capacity - 1)
                .returning(OperationalProfile.id)
            )
            if released_profile_id is None:
                raise RuntimeError(
                    "active capacity reservation has no reserved capacity"
                )
        return len(assignments)

    async def _owned_locked_request(
        self, request_id: UUID, requester_user_id: UUID, now: datetime
    ) -> HumanMatchRequest:
        request = await _locked_match_request(self._session, request_id, now=now)
        if request.requester_user_id != requester_user_id:
            raise LookupError("human match request was not found")
        return request

    async def _active_assignment(self, request_id: UUID) -> HumanMatchAssignment | None:
        return cast(
            HumanMatchAssignment | None,
            await self._session.scalar(
                select(HumanMatchAssignment)
                .where(
                    HumanMatchAssignment.request_id == request_id,
                    HumanMatchAssignment.released_at.is_(None),
                )
                .with_for_update()
            ),
        )

    async def _responder_for_assignment(
        self, assignment: HumanMatchAssignment
    ) -> MatchResponderRecord | None:
        recipient = aliased(User)
        row = (
            await self._session.execute(
                select(OperationalProfile, recipient)
                .join(
                    OperationalLogin,
                    OperationalLogin.operational_profile_id == OperationalProfile.id,
                )
                .join(recipient, recipient.id == OperationalLogin.user_id)
                .where(
                    OperationalProfile.id == assignment.responder_profile_id,
                    OperationalLogin.detached_at.is_(None),
                    recipient.telegram_user_id.is_not(None),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        profile, user = row._tuple()
        assert user.telegram_user_id is not None
        return MatchResponderRecord(
            assignment=_assignment_record(assignment),
            profile_id=profile.id,
            recipient_user_id=user.id,
            telegram_chat_id=user.telegram_user_id,
            display_name=user.display_name,
            cg_name=profile.cg_name,
            telegram_contact_url=profile.telegram_contact_url,
        )

    async def _release_assignment(
        self,
        assignment: HumanMatchAssignment,
        *,
        reason: str,
        at: datetime,
    ) -> None:
        assignment.released_at = at
        assignment.release_reason = reason
        reservation = await self._session.scalar(
            select(CapacityReservation)
            .where(
                CapacityReservation.id == assignment.capacity_reservation_id,
                CapacityReservation.released_at.is_(None),
            )
            .with_for_update()
        )
        if reservation is None:
            return
        reservation.released_at = at
        released_profile_id = await self._session.scalar(
            update(OperationalProfile)
            .where(
                OperationalProfile.id == reservation.operational_profile_id,
                OperationalProfile.reserved_capacity > 0,
            )
            .values(reserved_capacity=OperationalProfile.reserved_capacity - 1)
            .returning(OperationalProfile.id)
        )
        if released_profile_id is None:
            raise RuntimeError("active capacity reservation has no reserved capacity")


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

    async def open_root(
        self, root: OpenSelectionState, *, at: datetime
    ) -> OpenSelectionState:
        """Idempotently open one published root without changing another branch."""

        if root.user_id not in self._locked_user_ids:
            raise RuntimeError("root selection user is not locked")
        version = await self._session.get(FlowVersion, root.flow_version_id)
        if version is None:
            raise LookupError("root flow version was not found")
        if root.parent_flow_key != version.root_flow_key:
            raise ValueError("root selection key does not match flow version root")
        if root.service_id != version.service_id:
            raise ValueError("root selection service does not match flow version")
        if not root.is_current:
            raise ValueError("root selection must be current when opened")
        if root.service_id is not None:
            await _lock_root_service(
                self._session,
                flow_version_id=root.flow_version_id,
                service_id=root.service_id,
                at=at,
            )

        row = await self._session.scalar(
            pg_insert(OpenFlowSelection)
            .values(
                id=root.id,
                user_id=root.user_id,
                flow_version_id=root.flow_version_id,
                parent_flow_key=root.parent_flow_key,
                service_id=root.service_id,
                is_current=True,
                is_global_interruptive=root.is_global_interruptive,
                ancestor_flow_keys=list(root.ancestor_flow_keys),
                checkpoint_flow_keys=list(root.checkpoint_flow_keys),
                opened_at=at,
                last_focused_at=at,
            )
            .on_conflict_do_nothing()
            .returning(OpenFlowSelection)
        )
        if row is None:
            row = await self._session.scalar(
                select(OpenFlowSelection).where(
                    OpenFlowSelection.user_id == root.user_id,
                    OpenFlowSelection.flow_version_id == root.flow_version_id,
                    OpenFlowSelection.parent_flow_key == root.parent_flow_key,
                    OpenFlowSelection.service_id.is_not_distinct_from(root.service_id),
                )
            )
        if row is None:
            raise RuntimeError("root selection could not be opened")
        return _open_selection_state(row)

    async def apply(
        self,
        transition: SelectionTransition | CheckpointReturnTransition,
        *,
        at: datetime,
    ) -> None:
        source_user_id, source_service_id = await self._source_branch_for(transition)
        if source_user_id not in self._locked_user_ids:
            raise RuntimeError("selection transition user is not locked")
        if source_service_id is not None:
            await _lock_open_service(self._session, source_service_id, at=at)
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

    async def _source_branch_for(
        self, transition: SelectionTransition | CheckpointReturnTransition
    ) -> tuple[UUID, UUID | None]:
        row = (
            await self._session.execute(
                select(OpenFlowSelection.user_id, OpenFlowSelection.service_id).where(
                    OpenFlowSelection.id == transition.source_selection_id
                )
            )
        ).one_or_none()
        if row is None:
            raise LookupError("selection transition source was not found")
        return row._tuple()

    async def expire_service_bound(
        self, service_id: UUID, *, at: datetime
    ) -> ServiceBoundSelectionExpiry:
        affected_user_ids = list(
            await self._session.scalars(
                update(OpenFlowSelection)
                .where(
                    OpenFlowSelection.service_id == service_id,
                    OpenFlowSelection.expires_at.is_(None),
                )
                .values(expires_at=at, is_current=False)
                .returning(OpenFlowSelection.user_id)
            )
        )
        return ServiceBoundSelectionExpiry(
            expired_selection_count=len(affected_user_ids),
            affected_user_ids=frozenset(affected_user_ids),
        )

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
        self, service_timestamp_id: UUID, user_id: UUID, *, now: datetime
    ) -> bool:
        timestamp = await self._session.scalar(
            select(ServiceTimestamp)
            .where(ServiceTimestamp.id == service_timestamp_id)
            .with_for_update()
        )
        if timestamp is None:
            raise LookupError("service timestamp was not found")
        await _lock_timestamp_delivery_service(self._session, timestamp, at=now)
        row = await self._session.scalar(
            pg_insert(TimestampDeliveryClaim)
            .values(
                service_timestamp_id=service_timestamp_id,
                user_id=user_id,
                claimed_at=now,
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
        pause = await self._locked_telegram_outbound_pause()
        if pause.pause_until > now:
            return None
        row.status = "claimed"
        row.claim_token = uuid4()
        row.claim_expires_at = now + lease_duration
        return _delivery_record(row)

    async def extend_telegram_pause(self, *, pause_until: datetime) -> datetime:
        """Extend the singleton Telegram pause without allowing it to move backward."""

        pause = await self._locked_telegram_outbound_pause()
        pause.pause_until = max(pause.pause_until, pause_until)
        return pause.pause_until

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
        # Delivery operations always acquire the delivery row before the
        # singleton pause row.  Keeping this order consistent with
        # ``claim_next_safe`` prevents a recovering worker and a stale starter
        # from holding the two locks in opposite orders.
        pause = await self._locked_telegram_outbound_pause()
        if pause.pause_until > started_at:
            raise DeliveryClaimLostError("outbound delivery claim was paused")
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

    async def _locked_telegram_outbound_pause(self) -> TelegramOutboundPause:
        """Materialize and lock the one row that serializes outbound claims."""

        await self._session.execute(
            pg_insert(TelegramOutboundPause)
            .values(
                singleton_id=1,
                pause_until=_NO_TELEGRAM_OUTBOUND_PAUSE_UNTIL,
            )
            .on_conflict_do_nothing(index_elements=[TelegramOutboundPause.singleton_id])
        )
        pause = await self._session.scalar(
            select(TelegramOutboundPause)
            .where(TelegramOutboundPause.singleton_id == 1)
            .with_for_update()
        )
        if pause is None:
            raise RuntimeError("Telegram outbound pause could not be initialized")
        return pause


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
