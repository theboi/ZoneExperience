"""Operational-account decisions backed exclusively by F01 repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.uow import UnitOfWorkFactory

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
            profile = await uow.operational_profiles.find_by_login_identity(
                normalized_name, dob
            )
            if profile is None:
                return LoginResult("not_found")
            attached = await uow.operational_logins.attach(profile.id, user.id, at=now)
            if attached.kind == "occupied":
                return LoginResult("occupied")
            if attached.kind == "not_found":
                return LoginResult("not_found")
            return LoginResult(
                "attached",
                opens_interest_capture=(
                    attached.is_first_ever_attachment
                    and user.role in _OPERATIONAL_ROLES
                ),
            )

    async def manage(self, telegram_user_id: int, *, now: datetime) -> LoginResult:
        """Expose the configured interest editor without updating profile data directly."""

        async with self._uow_factory() as uow:
            user = await uow.users.require_by_telegram_id(telegram_user_id)
            await uow.lock_user(user.id)
            return LoginResult(
                "manage",
                opens_interest_editor=user.role in _OPERATIONAL_ROLES,
            )

    async def logout(self, telegram_user_id: int, *, now: datetime) -> LoginResult:
        """Detach the active attachment while retaining profile and login history."""

        async with self._uow_factory() as uow:
            user = await uow.users.require_by_telegram_id(telegram_user_id)
            await uow.lock_user(user.id)
            detached = await uow.operational_logins.detach_for_user(user.id, at=now)
            return LoginResult("detached" if detached is not None else "not_attached")
