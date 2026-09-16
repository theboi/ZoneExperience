"""PostgreSQL integration coverage for F01's durable schema boundary."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import Enum, MetaData, insert, select
from sqlalchemy.dialects.postgresql import CreateEnumType, dialect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from friendly_bot.persistence.base import Base
from friendly_bot.persistence.models import (
    FlowScopeKind,
    OperationalRole,
    ServiceAudience,
    TelegramPollState,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_JSON_COLUMNS = {
    "diagnostic_records": {"safe_context"},
    "flow_versions": {"definition"},
    "operational_profiles": {"interests"},
}


@dataclass(frozen=True, slots=True)
class SchemaConnection:
    """A real PostgreSQL connection confined to one generated test schema."""

    connection: asyncpg.Connection[asyncpg.Record]
    schema: str

    def table(self, name: str) -> str:
        """Return the quoted table name for controlled metadata table names."""

        return f'"{self.schema}"."{name}"'

    async def insert(self, table: str, /, **values: object) -> None:
        """Insert a row while exercising the ORM metadata's PostgreSQL DDL."""

        columns = ", ".join(values)
        placeholders = ", ".join(f"${index}" for index in range(1, len(values) + 1))
        query = f"INSERT INTO {self.table(table)} ({columns}) VALUES ({placeholders})"
        encoded_values = tuple(
            _encode_json(table, column, value) for column, value in values.items()
        )
        await self.connection.execute(query, *encoded_values)


def _encode_json(table: str, column: str, value: object) -> object:
    """Adapt only JSONB values without altering PostgreSQL ARRAY values."""

    if column in _JSON_COLUMNS.get(table, set()):
        return json.dumps(value)
    return value


async def _create_schema(
    connection: asyncpg.Connection[asyncpg.Record], schema: str
) -> None:
    """Compile F01's one metadata registry into an isolated PostgreSQL schema."""

    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata, schema=schema)

    enum_types: dict[str, Enum] = {}
    for table in metadata.sorted_tables:
        for column in table.columns:
            if isinstance(column.type, Enum) and column.type.native_enum:
                column.type.schema = schema
                enum_types.setdefault(column.type.name or column.name, column.type)

    postgres = dialect()
    for enum_type in enum_types.values():
        await connection.execute(
            str(CreateEnumType(enum_type).compile(dialect=postgres))
        )
    for table in metadata.sorted_tables:
        await connection.execute(str(CreateTable(table).compile(dialect=postgres)))
    for table in metadata.sorted_tables:
        for index in table.indexes:
            await connection.execute(str(CreateIndex(index).compile(dialect=postgres)))


@pytest.fixture
async def session() -> AsyncIterator[SchemaConnection]:
    """Create all metadata in a generated schema from the explicit test URL."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")

    url = make_url(database_url)
    schema = f"friendly_bot_task5_{uuid4().hex}"
    connection = await asyncpg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        database=url.database,
    )
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await _create_schema(connection, schema)
        yield SchemaConnection(connection=connection, schema=schema)
    finally:
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


@pytest.fixture
async def async_session(session: SchemaConnection) -> AsyncIterator[AsyncSession]:
    """Provide a real SQLAlchemy session bound to Task 5's isolated schema."""

    database_url = os.environ["FRIENDLY_BOT_DATABASE_URL"]
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": session.schema}},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as database_session:
            yield database_session
    finally:
        await engine.dispose()


async def _user(
    session: SchemaConnection, *, role: OperationalRole = OperationalRole.NBNC
) -> UUID:
    user_id = uuid4()
    await session.insert("users", id=user_id, role=role.value)
    return user_id


async def _profile(
    session: SchemaConnection,
    *,
    user_id: UUID,
    normalized_name: str,
    dob: date,
) -> UUID:
    """Insert a minimally valid operational profile for constraint examples."""

    profile_id = uuid4()
    await session.insert(
        "operational_profiles",
        id=profile_id,
        user_id=user_id,
        normalized_name=normalized_name,
        dob=dob,
        interests=[],
        always_available=True,
        capacity=1,
        reserved_capacity=0,
    )
    return profile_id


async def _service(session: SchemaConnection, *, key: str) -> UUID:
    service_id = uuid4()
    await session.insert(
        "services",
        id=service_id,
        key=key,
        name=key,
        timezone="Asia/Singapore",
        highkey=True,
        doors_open_at=NOW - timedelta(hours=1),
        doors_close_at=NOW + timedelta(hours=1),
        service_starts_at=NOW,
        service_ends_at=NOW + timedelta(hours=2),
        interaction_ends_at=NOW + timedelta(hours=3),
    )
    return service_id


async def _flow_version(
    session: SchemaConnection, *, service_id: UUID | None = None
) -> UUID:
    flow_version_id = uuid4()
    await session.insert(
        "flow_versions",
        id=flow_version_id,
        scope_kind=(
            FlowScopeKind.SERVICE if service_id else FlowScopeKind.SYSTEM
        ).value,
        service_id=service_id,
        root_flow_key="system.home",
        definition={"key": "system.home"},
        content_hash=uuid4().hex,
        published_at=NOW,
    )
    return flow_version_id


async def test_metadata_creates_every_f01_durable_table(
    session: SchemaConnection,
) -> None:
    """An omitted durable record would leave a required table unavailable to consumers."""

    rows = await session.connection.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = $1", session.schema
    )
    assert {row["tablename"] for row in rows} == {
        "capacity_reservations",
        "conversation_messages",
        "diagnostic_records",
        "flow_versions",
        "human_match_assignments",
        "human_match_exclusions",
        "human_match_requests",
        "open_flow_selections",
        "operational_logins",
        "operational_profiles",
        "pending_flow_intents",
        "persona_cursors",
        "processed_telegram_updates",
        "service_attendances",
        "service_timestamps",
        "services",
        "telegram_poll_state",
        "timestamp_delivery_claims",
        "user_processing_locks",
        "users",
    }


async def test_async_session_can_execute_and_commit(
    async_session: AsyncSession,
) -> None:
    """The supported async runtime can execute and commit through AsyncSession."""

    await async_session.execute(
        insert(TelegramPollState).values(singleton_id=1, next_update_offset=1)
    )
    await async_session.commit()

    result = await async_session.execute(
        select(TelegramPollState.next_update_offset).where(
            TelegramPollState.singleton_id == 1
        )
    )
    assert result.scalar_one() == 1


async def test_processed_update_id_is_unique(session: SchemaConnection) -> None:
    """A replayed Telegram update cannot become two durable work claims."""

    await session.insert(
        "processed_telegram_updates",
        telegram_update_id=7,
        received_at=NOW,
        correlation_id=uuid4(),
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "processed_telegram_updates",
            telegram_update_id=7,
            received_at=NOW,
            correlation_id=uuid4(),
        )


async def test_active_attendance_is_unique_per_user(session: SchemaConnection) -> None:
    """A missing active-attendance partial index would allow overlapping services."""

    user_id = await _user(session)
    first_service_id = await _service(session, key="zone_x_2026_10_18")
    second_service_id = await _service(session, key="zone_y_2026_10_18")
    await session.insert(
        "service_attendances",
        id=uuid4(),
        service_id=first_service_id,
        user_id=user_id,
        attendee_kind="ordinary",
        started_at=NOW,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "service_attendances",
            id=uuid4(),
            service_id=second_service_id,
            user_id=user_id,
            attendee_kind="ordinary",
            started_at=NOW + timedelta(minutes=1),
        )


async def test_ended_attendance_preserves_history_before_new_active(
    session: SchemaConnection,
) -> None:
    """The active index must not reject a completed historical attendance."""

    user_id = await _user(session)
    first_service_id = await _service(session, key="zone_x_history")
    second_service_id = await _service(session, key="zone_y_active")
    await session.insert(
        "service_attendances",
        id=uuid4(),
        service_id=first_service_id,
        user_id=user_id,
        attendee_kind="ordinary",
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=1),
    )
    await session.insert(
        "service_attendances",
        id=uuid4(),
        service_id=second_service_id,
        user_id=user_id,
        attendee_kind="ordinary",
        started_at=NOW + timedelta(minutes=1),
    )


@pytest.mark.parametrize(
    ("capacity", "reserved_capacity"),
    [(-1, 0), (1, -1), (1, 2)],
)
async def test_operational_profile_rejects_invalid_capacity_bounds(
    session: SchemaConnection,
    capacity: int,
    reserved_capacity: int,
) -> None:
    """Invalid capacity accounting must fail before any matching repository runs."""

    user_id = await _user(session, role=OperationalRole.SERVER)
    with pytest.raises(asyncpg.CheckViolationError):
        await session.insert(
            "operational_profiles",
            id=uuid4(),
            user_id=user_id,
            normalized_name="jordan",
            dob=datetime(1990, 1, 1, tzinfo=UTC).date(),
            interests=[],
            always_available=False,
            capacity=capacity,
            reserved_capacity=reserved_capacity,
        )


async def test_operational_profile_login_identity_is_unique(
    session: SchemaConnection,
) -> None:
    """A normalized name and date of birth resolve to exactly one profile."""

    first_user_id = await _user(session, role=OperationalRole.SERVER)
    second_user_id = await _user(session, role=OperationalRole.SERVER)
    await _profile(
        session,
        user_id=first_user_id,
        normalized_name="jordan",
        dob=date(1990, 1, 1),
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await _profile(
            session,
            user_id=second_user_id,
            normalized_name="jordan",
            dob=date(1990, 1, 1),
        )

    await _profile(
        session,
        user_id=second_user_id,
        normalized_name="jordan",
        dob=date(1991, 1, 1),
    )


async def test_open_selection_treats_null_service_as_a_real_unique_key(
    session: SchemaConnection,
) -> None:
    """Two global copies of one selection must conflict despite NULL semantics."""

    user_id = await _user(session)
    flow_version_id = await _flow_version(session)
    values = {
        "user_id": user_id,
        "flow_version_id": flow_version_id,
        "parent_flow_key": "system.home",
        "service_id": None,
        "is_current": True,
        "is_global_interruptive": False,
        "ancestor_flow_keys": ["system.home"],
        "checkpoint_flow_keys": ["system.home"],
        "opened_at": NOW,
        "last_focused_at": NOW,
    }
    await session.insert("open_flow_selections", id=uuid4(), **values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("open_flow_selections", id=uuid4(), **values)


async def test_flow_version_treats_null_service_as_an_idempotency_key(
    session: SchemaConnection,
) -> None:
    """A repeated system publication must conflict even though service_id is NULL."""

    values = {
        "scope_kind": FlowScopeKind.SYSTEM.value,
        "service_id": None,
        "root_flow_key": "system.home",
        "definition": {"key": "system.home"},
        "content_hash": "a" * 64,
        "published_at": NOW,
    }
    await session.insert("flow_versions", id=uuid4(), **values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("flow_versions", id=uuid4(), **values)


async def test_operational_login_keeps_only_one_active_profile_attachment(
    session: SchemaConnection,
) -> None:
    """A profile cannot simultaneously attach to two Telegram identities."""

    profile_user_id = await _user(session, role=OperationalRole.SERVER)
    first_login_user_id = await _user(session)
    second_login_user_id = await _user(session)
    profile_id = uuid4()
    await session.insert(
        "operational_profiles",
        id=profile_id,
        user_id=profile_user_id,
        normalized_name="jordan",
        dob=datetime(1990, 1, 1, tzinfo=UTC).date(),
        interests=[],
        always_available=True,
        capacity=1,
        reserved_capacity=0,
    )
    await session.insert(
        "operational_logins",
        id=uuid4(),
        operational_profile_id=profile_id,
        user_id=first_login_user_id,
        attached_at=NOW,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "operational_logins",
            id=uuid4(),
            operational_profile_id=profile_id,
            user_id=second_login_user_id,
            attached_at=NOW + timedelta(minutes=1),
        )


async def test_operational_login_keeps_only_one_active_user_attachment(
    session: SchemaConnection,
) -> None:
    """One Telegram identity cannot actively attach to two profiles."""

    first_profile_user_id = await _user(session, role=OperationalRole.SERVER)
    second_profile_user_id = await _user(session, role=OperationalRole.SERVER)
    login_user_id = await _user(session)
    first_profile_id = await _profile(
        session,
        user_id=first_profile_user_id,
        normalized_name="first",
        dob=date(1990, 1, 1),
    )
    second_profile_id = await _profile(
        session,
        user_id=second_profile_user_id,
        normalized_name="second",
        dob=date(1991, 1, 1),
    )
    await session.insert(
        "operational_logins",
        id=uuid4(),
        operational_profile_id=first_profile_id,
        user_id=login_user_id,
        attached_at=NOW,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "operational_logins",
            id=uuid4(),
            operational_profile_id=second_profile_id,
            user_id=login_user_id,
            attached_at=NOW + timedelta(minutes=1),
        )


async def test_released_history_permits_a_later_active_login_and_match(
    session: SchemaConnection,
) -> None:
    """Partial active indexes retain history while permitting a later active row."""

    profile_user_id = await _user(session, role=OperationalRole.SERVER)
    login_user_id = await _user(session)
    requester_user_id = await _user(session)
    profile_id = await _profile(
        session,
        user_id=profile_user_id,
        normalized_name="responder",
        dob=date(1990, 1, 1),
    )
    await session.insert(
        "operational_logins",
        id=uuid4(),
        operational_profile_id=profile_id,
        user_id=login_user_id,
        attached_at=NOW,
        detached_at=NOW + timedelta(minutes=1),
    )
    await session.insert(
        "operational_logins",
        id=uuid4(),
        operational_profile_id=profile_id,
        user_id=login_user_id,
        attached_at=NOW + timedelta(minutes=2),
    )

    request_id = uuid4()
    await session.insert(
        "human_match_requests",
        id=request_id,
        requester_user_id=requester_user_id,
        kind="normal",
        status="pending",
        created_at=NOW,
    )
    released_reservation_id = uuid4()
    await session.insert(
        "capacity_reservations",
        id=released_reservation_id,
        operational_profile_id=profile_id,
        request_id=request_id,
        reserved_at=NOW,
        released_at=NOW + timedelta(minutes=1),
    )
    await session.insert(
        "human_match_assignments",
        id=uuid4(),
        request_id=request_id,
        responder_profile_id=profile_id,
        capacity_reservation_id=released_reservation_id,
        assigned_at=NOW,
        released_at=NOW + timedelta(minutes=1),
    )
    active_reservation_id = uuid4()
    await session.insert(
        "capacity_reservations",
        id=active_reservation_id,
        operational_profile_id=profile_id,
        request_id=request_id,
        reserved_at=NOW + timedelta(minutes=2),
    )
    await session.insert(
        "human_match_assignments",
        id=uuid4(),
        request_id=request_id,
        responder_profile_id=profile_id,
        capacity_reservation_id=active_reservation_id,
        assigned_at=NOW + timedelta(minutes=2),
    )


async def test_message_idempotency_constraint(
    session: SchemaConnection,
) -> None:
    """A duplicate external source cannot create a second durable message row."""

    user_id = await _user(session)
    message_values = {
        "user_id": user_id,
        "source_kind": "telegram",
        "source_message_id": 19,
        "body": "hello",
        "occurred_at": NOW,
    }
    await session.insert("conversation_messages", id=uuid4(), **message_values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("conversation_messages", id=uuid4(), **message_values)


async def test_service_timestamp_persona_and_user_lock_keys(
    session: SchemaConnection,
) -> None:
    """Scheduler definitions and user-scoped state each retain one durable key."""

    user_id = await _user(session)
    service_id = await _service(session, key="zone_x_key_constraints")
    flow_version_id = await _flow_version(session, service_id=service_id)
    timestamp_values = {
        "service_id": service_id,
        "key": "doors.open",
        "occurs_at": NOW,
        "audience": ServiceAudience.ALL_NBNCS.value,
        "flow_version_id": flow_version_id,
        "root_flow_key": "service.zone_x.doors",
    }
    await session.insert("service_timestamps", id=uuid4(), **timestamp_values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("service_timestamps", id=uuid4(), **timestamp_values)

    await session.insert(
        "persona_cursors", id=uuid4(), user_id=user_id, persona="friendly"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "persona_cursors", id=uuid4(), user_id=user_id, persona="calm"
        )

    await session.insert("user_processing_locks", user_id=user_id, locked_at=NOW)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("user_processing_locks", user_id=user_id, locked_at=NOW)


async def test_persona_cursor_rejects_an_orphan_last_message(
    session: SchemaConnection,
) -> None:
    """A persona cursor cannot advance past an unknown conversation message."""

    user_id = await _user(session)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await session.insert(
            "persona_cursors",
            id=uuid4(),
            user_id=user_id,
            persona="friendly",
            last_message_id=uuid4(),
        )


async def test_match_constraints_allow_only_one_active_assignment(
    session: SchemaConnection,
) -> None:
    """Concurrent matching cannot allocate a single request to two responders."""

    requester_id = await _user(session)
    responder_one_id = await _user(session, role=OperationalRole.SERVER)
    responder_two_id = await _user(session, role=OperationalRole.SERVER)
    profile_one_id = uuid4()
    profile_two_id = uuid4()
    for profile_id, user_id, name, dob in (
        (profile_one_id, responder_one_id, "one", datetime(1990, 1, 1, tzinfo=UTC)),
        (profile_two_id, responder_two_id, "two", datetime(1991, 1, 1, tzinfo=UTC)),
    ):
        await session.insert(
            "operational_profiles",
            id=profile_id,
            user_id=user_id,
            normalized_name=name,
            dob=dob.date(),
            interests=[],
            always_available=True,
            capacity=2,
            reserved_capacity=0,
        )
    request_id = uuid4()
    await session.insert(
        "human_match_requests",
        id=request_id,
        requester_user_id=requester_id,
        kind="normal",
        status="pending",
        created_at=NOW,
    )
    reservation_one_id = uuid4()
    reservation_two_id = uuid4()
    await session.insert(
        "capacity_reservations",
        id=reservation_one_id,
        operational_profile_id=profile_one_id,
        request_id=request_id,
        reserved_at=NOW,
    )
    await session.insert(
        "capacity_reservations",
        id=reservation_two_id,
        operational_profile_id=profile_two_id,
        request_id=request_id,
        reserved_at=NOW,
    )
    await session.insert(
        "human_match_assignments",
        id=uuid4(),
        request_id=request_id,
        responder_profile_id=profile_one_id,
        capacity_reservation_id=reservation_one_id,
        assigned_at=NOW,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "human_match_assignments",
            id=uuid4(),
            request_id=request_id,
            responder_profile_id=profile_two_id,
            capacity_reservation_id=reservation_two_id,
            assigned_at=NOW,
        )


async def test_match_exclusion_and_capacity_keys(
    session: SchemaConnection,
) -> None:
    """Matching work cannot duplicate a live reservation or exclusion."""

    requester_id = await _user(session)
    responder_id = await _user(session, role=OperationalRole.SERVER)
    profile_id = uuid4()
    await session.insert(
        "operational_profiles",
        id=profile_id,
        user_id=responder_id,
        normalized_name="responder",
        dob=datetime(1990, 1, 1, tzinfo=UTC).date(),
        interests=[],
        always_available=True,
        capacity=1,
        reserved_capacity=0,
    )
    request_id = uuid4()
    await session.insert(
        "human_match_requests",
        id=request_id,
        requester_user_id=requester_id,
        kind="normal",
        status="pending",
        created_at=NOW,
    )
    reservation_values = {
        "operational_profile_id": profile_id,
        "request_id": request_id,
        "reserved_at": NOW,
    }
    await session.insert("capacity_reservations", id=uuid4(), **reservation_values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("capacity_reservations", id=uuid4(), **reservation_values)

    exclusion_values = {
        "request_id": request_id,
        "responder_profile_id": profile_id,
        "created_at": NOW,
    }
    await session.insert("human_match_exclusions", id=uuid4(), **exclusion_values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("human_match_exclusions", id=uuid4(), **exclusion_values)


async def test_timestamp_claim_key(session: SchemaConnection) -> None:
    """Scheduler claims reject duplicate timestamp-recipient work."""

    user_id = await _user(session)
    service_id = await _service(session, key="zone_x_claims")
    flow_version_id = await _flow_version(session, service_id=service_id)
    timestamp_id = uuid4()
    await session.insert(
        "service_timestamps",
        id=timestamp_id,
        service_id=service_id,
        key="doors.open",
        occurs_at=NOW,
        audience=ServiceAudience.ALL_NBNCS.value,
        flow_version_id=flow_version_id,
        root_flow_key="service.zone_x.doors",
    )
    claim_values = {
        "service_timestamp_id": timestamp_id,
        "user_id": user_id,
        "claimed_at": NOW,
        "status": "claimed",
    }
    await session.insert("timestamp_delivery_claims", id=uuid4(), **claim_values)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert("timestamp_delivery_claims", id=uuid4(), **claim_values)


async def test_foreign_keys_reject_an_attendance_without_a_service(
    session: SchemaConnection,
) -> None:
    """A mutable record cannot outlive the durable service it references."""

    user_id = await _user(session)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await session.insert(
            "service_attendances",
            id=uuid4(),
            service_id=uuid4(),
            user_id=user_id,
            attendee_kind="ordinary",
            started_at=NOW,
        )


async def test_poll_state_is_singleton_and_profile_has_no_role(
    session: SchemaConnection,
) -> None:
    """The poll cursor is singular and operational role never duplicates users.role."""

    await session.insert("telegram_poll_state", singleton_id=1, next_update_offset=1)
    with pytest.raises(asyncpg.UniqueViolationError):
        await session.insert(
            "telegram_poll_state", singleton_id=1, next_update_offset=2
        )

    columns = await session.connection.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = 'operational_profiles'",
        session.schema,
    )
    assert "role" not in {column["column_name"] for column in columns}
