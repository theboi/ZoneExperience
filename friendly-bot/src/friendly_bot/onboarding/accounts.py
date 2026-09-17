"""Operational-account decisions backed exclusively by F01 repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
    "not_started",
]

type OperationalCommandInputKind = Literal[
    "not_active",
    "login_name_captured",
    "login_name_invalid",
    "login_dob_invalid",
    "login_attached",
    "login_interests_required",
    "login_occupied",
    "login_not_found",
    "login_not_started",
    "interests_saved",
    "interests_invalid",
    "interests_not_attached",
]

_OPERATIONAL_ROLES = frozenset(
    {OperationalRole.SERVER, OperationalRole.LEADER, OperationalRole.STAFF}
)
_LOGIN_ATTEMPT_TTL = timedelta(minutes=10)
_MAX_INTERESTS = 10
_MAX_INTEREST_LENGTH = 120
_LOGIN_NAME_STEP = "login_name"
_LOGIN_DOB_STEP = "login_dob"
_INTERESTS_STEP = "interests"


@dataclass(frozen=True, slots=True)
class LoginResult:
    """A configured-flow outcome that never identifies another Telegram account."""

    kind: LoginResultKind
    opens_interest_capture: bool = False
    opens_interest_editor: bool = False


@dataclass(frozen=True, slots=True)
class OperationalCommandInputResult:
    """A local command-session result that must never reach discussion routing."""

    kind: OperationalCommandInputKind

    @property
    def handled(self) -> bool:
        return self.kind != "not_active"


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

    async def start_login_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        now: datetime,
    ) -> None:
        """Open a local-only command session which waits for the login name."""

        await uow.operational_login_attempts.start(
            user_id,
            step=_LOGIN_NAME_STEP,
            normalized_name=None,
            expires_at=now + _LOGIN_ATTEMPT_TTL,
            at=now,
        )

    async def start_interest_management_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        now: datetime,
    ) -> bool:
        """Open a local-only interest editor for the current operational account."""

        managed = await self.manage_in_uow(uow, user_id=user_id, now=now)
        if not managed.opens_interest_editor:
            return False
        await uow.operational_login_attempts.start(
            user_id,
            step=_INTERESTS_STEP,
            normalized_name=None,
            expires_at=now + _LOGIN_ATTEMPT_TTL,
            at=now,
        )
        return True

    async def process_pending_input_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        text: str,
        now: datetime,
    ) -> OperationalCommandInputResult:
        """Consume only an active local command session before discussion routing."""

        attempt = await uow.operational_login_attempts.current(user_id, now=now)
        if attempt is None:
            return OperationalCommandInputResult("not_active")
        if attempt.step == _LOGIN_NAME_STEP:
            try:
                normalized_name = normalize_operational_name(text)
            except ValueError:
                return OperationalCommandInputResult("login_name_invalid")
            await uow.operational_login_attempts.start(
                user_id,
                step=_LOGIN_DOB_STEP,
                normalized_name=normalized_name,
                expires_at=now + _LOGIN_ATTEMPT_TTL,
                at=now,
            )
            return OperationalCommandInputResult("login_name_captured")
        if attempt.step == _LOGIN_DOB_STEP:
            try:
                dob = parse_operational_dob(text)
            except ValueError:
                return OperationalCommandInputResult("login_dob_invalid")
            if attempt.normalized_name is None:
                await uow.operational_login_attempts.clear(user_id)
                return OperationalCommandInputResult("login_not_started")
            await uow.operational_login_attempts.clear(user_id)
            result = await self.login_in_uow(
                uow,
                user_id=user_id,
                normalized_name=attempt.normalized_name,
                dob=dob,
                now=now,
            )
            if result.opens_interest_capture:
                await uow.operational_login_attempts.start(
                    user_id,
                    step=_INTERESTS_STEP,
                    normalized_name=None,
                    expires_at=now + _LOGIN_ATTEMPT_TTL,
                    at=now,
                )
                return OperationalCommandInputResult("login_interests_required")
            if result.kind == "attached":
                return OperationalCommandInputResult("login_attached")
            if result.kind == "occupied":
                return OperationalCommandInputResult("login_occupied")
            if result.kind == "not_found":
                return OperationalCommandInputResult("login_not_found")
            raise AssertionError("operational login returned an unsupported outcome")
        if attempt.step == _INTERESTS_STEP:
            try:
                interests = normalize_operational_interests(_split_interests(text))
            except ValueError:
                return OperationalCommandInputResult("interests_invalid")
            await uow.operational_login_attempts.clear(user_id)
            saved = await self.update_interests_in_uow(
                uow,
                user_id=user_id,
                interests=interests,
                now=now,
            )
            return OperationalCommandInputResult(
                "interests_saved" if saved else "interests_not_attached"
            )
        await uow.operational_login_attempts.clear(user_id)
        return OperationalCommandInputResult("not_active")

    async def complete_login_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        dob: date,
        now: datetime,
    ) -> LoginResult:
        """Compatibility entry point for callers that already hold a parsed DOB."""

        attempt = await uow.operational_login_attempts.current(user_id, now=now)
        if attempt is None or attempt.step != _LOGIN_DOB_STEP:
            return LoginResult("not_started")
        if attempt.normalized_name is None:
            await uow.operational_login_attempts.clear(user_id)
            return LoginResult("not_started")
        await uow.operational_login_attempts.clear(user_id)
        return await self.login_in_uow(
            uow,
            user_id=user_id,
            normalized_name=attempt.normalized_name,
            dob=dob,
            now=now,
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

    async def update_interests_in_uow(
        self,
        uow: UnitOfWork,
        *,
        user_id: UUID,
        interests: list[str],
        now: datetime,
    ) -> bool:
        """Update only the active operational profile attached to this Telegram user."""

        profile = await uow.operational_profiles.find_active_for_login_user(user_id)
        if profile is None:
            return False
        await uow.operational_profiles.update_interests(
            profile.id, normalize_operational_interests(interests), at=now
        )
        return True

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

        await uow.operational_login_attempts.clear(user_id)
        detached = await uow.operational_logins.detach_for_user(user_id, at=now)
        return LoginResult("detached" if detached is not None else "not_attached")


def normalize_operational_name(value: str) -> str:
    """Canonicalize a supplied login name without sending it to the model."""

    if type(value) is not str:
        raise ValueError("login name must be text")
    normalized = " ".join(value.split()).casefold()
    if not normalized or len(normalized) > 256:
        raise ValueError("login name is invalid")
    return normalized


def normalize_operational_interests(values: list[str]) -> list[str]:
    """Keep a small, usable, de-duplicated interest list for operational matching."""

    if not isinstance(values, list):
        raise TypeError("interests must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if type(value) is not str:
            raise ValueError("interest must be text")
        interest = " ".join(value.split())
        if not interest or len(interest) > _MAX_INTEREST_LENGTH:
            raise ValueError("interest is invalid")
        key = interest.casefold()
        if key not in seen:
            normalized.append(interest)
            seen.add(key)
    if not normalized or len(normalized) > _MAX_INTERESTS:
        raise ValueError("provide between one and ten interests")
    return normalized


def parse_operational_dob(value: str) -> date:
    """Parse the explicit DOB formats accepted by the direct `/login` command."""

    cleaned = value.strip()
    for format_string in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, format_string).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise ValueError("date of birth is invalid")


def _split_interests(value: str) -> list[str]:
    return [part for part in value.replace(";", ",").replace("\n", ",").split(",") if part]
