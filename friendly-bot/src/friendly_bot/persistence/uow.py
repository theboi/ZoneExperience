"""One async transaction boundary with the typed F01 repository set."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from friendly_bot.persistence.models import UserProcessingLock
from friendly_bot.persistence.repositories import (
    AttendanceRepository,
    ConversationRepository,
    DiagnosticRepository,
    FlowVersionRepository,
    MatchRepository,
    OpenSelectionRepository,
    OperationalLoginAttemptRepository,
    OperationalLoginRepository,
    OperationalProfileRepository,
    PendingIntentRepository,
    PersonaRepository,
    PollStateRepository,
    ServiceRepository,
    SqlAlchemyAttendanceRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyDiagnosticRepository,
    SqlAlchemyFlowVersionRepository,
    SqlAlchemyMatchRepository,
    SqlAlchemyOpenSelectionRepository,
    SqlAlchemyOperationalLoginAttemptRepository,
    SqlAlchemyOperationalLoginRepository,
    SqlAlchemyOperationalProfileRepository,
    SqlAlchemyPendingIntentRepository,
    SqlAlchemyPersonaRepository,
    SqlAlchemyPollStateRepository,
    SqlAlchemyServiceRepository,
    SqlAlchemyUpdateRepository,
    SqlAlchemyUserRepository,
    UpdateRepository,
    UserRepository,
)

type AsyncSessionFactory = Callable[[], AsyncSession]


class UnitOfWork:
    """Own an async session and commit exactly one exception-free transaction."""

    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._transaction: AsyncSessionTransaction | None = None
        self._locked_user_ids: set[UUID] = set()
        self._users: UserRepository | None = None
        self._operational_profiles: OperationalProfileRepository | None = None
        self._operational_logins: OperationalLoginRepository | None = None
        self._operational_login_attempts: OperationalLoginAttemptRepository | None = (
            None
        )
        self._services: ServiceRepository | None = None
        self._attendances: AttendanceRepository | None = None
        self._poll_state: PollStateRepository | None = None
        self._updates: UpdateRepository | None = None
        self._conversations: ConversationRepository | None = None
        self._personas: PersonaRepository | None = None
        self._pending_intents: PendingIntentRepository | None = None
        self._matches: MatchRepository | None = None
        self._open_selections: OpenSelectionRepository | None = None
        self._diagnostics: DiagnosticRepository | None = None
        self._flow_versions: FlowVersionRepository | None = None

    async def __aenter__(self) -> Self:
        self._locked_user_ids.clear()
        self._session = self._session_factory()
        self._transaction = await self._session.begin()
        self._users = SqlAlchemyUserRepository(self._session)
        self._operational_profiles = SqlAlchemyOperationalProfileRepository(
            self._session
        )
        self._operational_logins = SqlAlchemyOperationalLoginRepository(self._session)
        self._operational_login_attempts = SqlAlchemyOperationalLoginAttemptRepository(
            self._session
        )
        self._services = SqlAlchemyServiceRepository(self._session)
        self._attendances = SqlAlchemyAttendanceRepository(
            self._session, self.lock_user
        )
        self._poll_state = SqlAlchemyPollStateRepository(self._session)
        self._updates = SqlAlchemyUpdateRepository(self._session)
        self._conversations = SqlAlchemyConversationRepository(self._session)
        self._personas = SqlAlchemyPersonaRepository(self._session)
        self._pending_intents = SqlAlchemyPendingIntentRepository(
            self._session, self._locked_user_ids
        )
        self._matches = SqlAlchemyMatchRepository(self._session)
        self._open_selections = SqlAlchemyOpenSelectionRepository(
            self._session, self._locked_user_ids
        )
        self._diagnostics = SqlAlchemyDiagnosticRepository(self._session)
        self._flow_versions = SqlAlchemyFlowVersionRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        transaction = self._required_transaction()
        session = self._required_session()
        try:
            if exc_type is None:
                await transaction.commit()
            else:
                await transaction.rollback()
        finally:
            await session.close()
            self._transaction = None
            self._session = None

    async def lock_user(self, user_id: UUID) -> None:
        """Materialize and lock the durable per-user serialization row."""

        session = self._required_session()
        await session.execute(
            pg_insert(UserProcessingLock)
            .values(user_id=user_id, locked_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[UserProcessingLock.user_id])
        )
        locked = await session.scalar(
            select(UserProcessingLock.user_id)
            .where(UserProcessingLock.user_id == user_id)
            .with_for_update()
        )
        if locked is None:
            raise LookupError("user was not found for processing lock")
        self._locked_user_ids.add(user_id)

    @property
    def users(self) -> UserRepository:
        return self._required_repository(self._users)

    @property
    def operational_profiles(self) -> OperationalProfileRepository:
        return self._required_repository(self._operational_profiles)

    @property
    def operational_logins(self) -> OperationalLoginRepository:
        return self._required_repository(self._operational_logins)

    @property
    def operational_login_attempts(self) -> OperationalLoginAttemptRepository:
        return self._required_repository(self._operational_login_attempts)

    @property
    def services(self) -> ServiceRepository:
        return self._required_repository(self._services)

    @property
    def attendances(self) -> AttendanceRepository:
        return self._required_repository(self._attendances)

    @property
    def poll_state(self) -> PollStateRepository:
        return self._required_repository(self._poll_state)

    @property
    def updates(self) -> UpdateRepository:
        return self._required_repository(self._updates)

    @property
    def conversations(self) -> ConversationRepository:
        return self._required_repository(self._conversations)

    @property
    def personas(self) -> PersonaRepository:
        return self._required_repository(self._personas)

    @property
    def pending_intents(self) -> PendingIntentRepository:
        return self._required_repository(self._pending_intents)

    @property
    def matches(self) -> MatchRepository:
        return self._required_repository(self._matches)

    @property
    def open_selections(self) -> OpenSelectionRepository:
        return self._required_repository(self._open_selections)

    @property
    def diagnostics(self) -> DiagnosticRepository:
        return self._required_repository(self._diagnostics)

    @property
    def flow_versions(self) -> FlowVersionRepository:
        return self._required_repository(self._flow_versions)

    def _required_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    def _required_transaction(self) -> AsyncSessionTransaction:
        if self._transaction is None:
            raise RuntimeError("unit of work is not active")
        return self._transaction

    @staticmethod
    def _required_repository[T](repository: T | None) -> T:
        if repository is None:
            raise RuntimeError("unit of work is not active")
        return repository


type UnitOfWorkFactory = Callable[[], UnitOfWork]
