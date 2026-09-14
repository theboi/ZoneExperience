"""Operational-account decisions backed exclusively by F01 repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.uow import UnitOfWork, UnitOfWorkFactory

type LoginResultKind = Literal[
    "attached",
    "occupied",
    "not_found",
    "manage",
    "detached",
    "not_attached",
]

_OPERATIONAL_ROLES = frozenset(
    {OperationalRole.SERVER, OperationalRole.LEADER, OperationalRole.STAFF}
)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """A configured-flow outcome that never identifies another Telegram account."""

    kind: LoginResultKind
    opens_interest_capture: bool = False
    opens_interest_editor: bool = False


class OperationalAccountService:
    """Attach, manage, and detach an operational profile through one UoW each."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def login(
        self,
        telegram_user_id: int,
        normalized_name: str,
        dob: date,
        *,
        now: datetime,
    ) -> LoginResult:
        """Attach only through F01's exclusive repository operation."""

        async with self._uow_factory() as uow:
            user = await uow.users.resolve_telegram_sender(
                telegram_user_id, received_at=now
            )
            await uow.lock_user(user.id)
            return await self.login_in_uow(
                uow,
                user_id=user.id,
                normalized_name=normalized_name,
                dob=dob,
                now=now,
            )

    async def login_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        normalized_name: str,
        dob: date,
        now: datetime,
    ) -> LoginResult:
        """Attach in ingress's UoW, deriving role from the matched profile owner."""

        profile = await uow.operational_profiles.find_by_login_identity(
            normalized_name, dob
        )
        if profile is None:
            return LoginResult("not_found")
        attached = await uow.operational_logins.attach(profile.id, user_id, at=now)
        if attached.kind == "occupied":
            return LoginResult("occupied")
        if attached.kind == "not_found":
            return LoginResult("not_found")
        operational_user = await uow.users.require_by_id(profile.user_id)
        return LoginResult(
            "attached",
            opens_interest_capture=(
                attached.is_first_ever_attachment
                and operational_user.role in _OPERATIONAL_ROLES
            ),
        )

    async def manage(self, telegram_user_id: int, *, now: datetime) -> LoginResult:
        """Expose the configured interest editor without updating profile data directly."""

        async with self._uow_factory() as uow:
            user = await uow.users.require_by_telegram_id(telegram_user_id)
            await uow.lock_user(user.id)
            return await self.manage_in_uow(uow, user_id=user.id, now=now)

    async def manage_in_uow(
        self, uow: UnitOfWork, *, user_id: UUID, now: datetime
    ) -> LoginResult:
        """Expose editing only for the active profile attached to this login user."""

        del now
        profile = await uow.operational_profiles.find_active_for_login_user(user_id)
        if profile is None:
            return LoginResult("manage")
        operational_user = await uow.users.require_by_id(profile.user_id)
        return LoginResult(
            "manage",
            opens_interest_editor=operational_user.role in _OPERATIONAL_ROLES,
        )

    async def logout(self, telegram_user_id: int, *, now: datetime) -> LoginResult:
        """Detach the active attachment while retaining profile and login history."""

        async with self._uow_factory() as uow:
            user = await uow.users.require_by_telegram_id(telegram_user_id)
            await uow.lock_user(user.id)
            return await self.logout_in_uow(uow, user_id=user.id, now=now)

    async def logout_in_uow(
        self, uow: UnitOfWork, *, user_id: UUID, now: datetime
    ) -> LoginResult:
        """Detach the supplied ingress user without opening or owning a transaction."""

        detached = await uow.operational_logins.detach_for_user(user_id, at=now)
        return LoginResult("detached" if detached is not None else "not_attached")
