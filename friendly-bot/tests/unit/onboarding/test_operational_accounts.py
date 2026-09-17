"""Operational account outcomes use F01's durable attachment fact."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from friendly_bot.onboarding.accounts import LoginResult, OperationalAccountService
from friendly_bot.persistence.models import OperationalRole
from friendly_bot.persistence.repositories import (
    LoginAttachmentResult,
    OperationalLoginAttemptRecord,
    OperationalProfileRecord,
    UserRecord,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DOB = date(1999, 4, 2)
USER_ID = uuid4()
PROFILE_ID = uuid4()
INGRESS_USER_ID = uuid4()
PROFILE_OWNER_ID = uuid4()


class ReattachedAccountUow:
    """A UoW-shaped double with a previously attached operational profile."""

    def __init__(self) -> None:
        self.users = self
        self.operational_profiles = self
        self.operational_logins = self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    async def resolve_telegram_sender(
        self, telegram_user_id: int, *, received_at: datetime
    ) -> UserRecord:
        assert telegram_user_id == 81 and received_at == NOW
        return UserRecord(
            id=USER_ID,
            telegram_user_id=telegram_user_id,
            display_name="Server",
            role=OperationalRole.SERVER,
            is_admin=False,
        )

    async def lock_user(self, user_id: UUID) -> None:
        assert user_id == USER_ID

    async def require_by_id(self, user_id: UUID) -> UserRecord:
        assert user_id == USER_ID
        return UserRecord(
            id=USER_ID,
            telegram_user_id=81,
            display_name="Server",
            role=OperationalRole.SERVER,
            is_admin=False,
        )

    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord:
        assert (normalized_name, dob) == ("server-name", DOB)
        return OperationalProfileRecord(
            id=PROFILE_ID,
            user_id=USER_ID,
            normalized_name=normalized_name,
            interests=("prefilled",),
            cg_name=None,
            telegram_contact_url=None,
            always_available=False,
            capacity=1,
            reserved_capacity=0,
        )

    async def attach(
        self, profile_id: UUID, user_id: UUID, *, at: datetime
    ) -> LoginAttachmentResult:
        assert (profile_id, user_id, at) == (PROFILE_ID, USER_ID, NOW)
        return LoginAttachmentResult("attached", is_first_ever_attachment=False)


async def test_reattachment_does_not_reopen_interest_capture() -> None:
    """Ignoring F01's first-ever fact would repeat initial-profile capture after logout."""

    accounts = OperationalAccountService(lambda: ReattachedAccountUow())

    result = await accounts.login(81, "server-name", DOB, now=NOW)

    assert result.kind == "attached"
    assert result.opens_interest_capture is False


class ComposedAccountUow:
    """Ingress-shaped account collaborators with separate sender/profile ownership."""

    def __init__(self) -> None:
        self.users = self
        self.operational_profiles = self
        self.operational_logins = self
        self.required_user_ids: list[UUID] = []

    async def require_by_id(self, user_id: UUID) -> UserRecord:
        self.required_user_ids.append(user_id)
        if user_id == INGRESS_USER_ID:
            return UserRecord(
                id=user_id,
                telegram_user_id=81,
                display_name=None,
                role=OperationalRole.NBNC,
                is_admin=False,
            )
        if user_id == PROFILE_OWNER_ID:
            return UserRecord(
                id=user_id,
                telegram_user_id=None,
                display_name="Canonical server",
                role=OperationalRole.SERVER,
                is_admin=False,
            )
        raise LookupError("user was not found")

    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord:
        assert (normalized_name, dob) == ("server-name", DOB)
        return OperationalProfileRecord(
            id=PROFILE_ID,
            user_id=PROFILE_OWNER_ID,
            normalized_name=normalized_name,
            interests=("prefilled",),
            cg_name=None,
            telegram_contact_url=None,
            always_available=False,
            capacity=1,
            reserved_capacity=0,
        )

    async def find_active_for_login_user(
        self, user_id: UUID
    ) -> OperationalProfileRecord | None:
        assert user_id == INGRESS_USER_ID
        return await self.find_by_login_identity("server-name", DOB)

    async def attach(
        self, profile_id: UUID, user_id: UUID, *, at: datetime
    ) -> LoginAttachmentResult:
        assert (profile_id, user_id, at) == (PROFILE_ID, INGRESS_USER_ID, NOW)
        return LoginAttachmentResult("attached", is_first_ever_attachment=True)


async def test_composed_account_methods_use_profile_owner_role_without_another_uow() -> (
    None
):
    """A fresh sender is NBNC; the attached profile owner supplies operational role."""

    def fail_if_opened() -> ComposedAccountUow:
        raise AssertionError("composed account work must not open another unit of work")

    uow = ComposedAccountUow()
    accounts = OperationalAccountService(fail_if_opened)

    attached = await accounts.login_in_uow(
        uow,
        user_id=INGRESS_USER_ID,
        normalized_name="server-name",
        dob=DOB,
        now=NOW,
    )
    managed = await accounts.manage_in_uow(uow, user_id=INGRESS_USER_ID, now=NOW)

    assert attached == LoginResult("attached", opens_interest_capture=True)
    assert managed == LoginResult("manage", opens_interest_editor=True)
    assert uow.required_user_ids == [PROFILE_OWNER_ID, PROFILE_OWNER_ID]


class OperationalLoginFlowUow:
    """A minimal local account-flow double, including the disposable name attempt."""

    def __init__(self) -> None:
        self.users = self
        self.operational_profiles = self
        self.operational_logins = self
        self.operational_login_attempts = self
        self.attempt: OperationalLoginAttemptRecord | None = None
        self.interests: list[str] | None = None
        self.cleared = False

    async def start(
        self,
        user_id: UUID,
        *,
        normalized_name: str,
        expires_at: datetime,
        at: datetime,
    ) -> OperationalLoginAttemptRecord:
        assert user_id == INGRESS_USER_ID and at == NOW
        self.attempt = OperationalLoginAttemptRecord(
            user_id=user_id,
            normalized_name=normalized_name,
            expires_at=expires_at,
        )
        return self.attempt

    async def consume(
        self, user_id: UUID, *, now: datetime
    ) -> OperationalLoginAttemptRecord | None:
        assert user_id == INGRESS_USER_ID and now == NOW
        attempt, self.attempt = self.attempt, None
        return attempt

    async def clear(self, user_id: UUID) -> None:
        assert user_id == INGRESS_USER_ID
        self.cleared = True

    async def find_by_login_identity(
        self, normalized_name: str, dob: date
    ) -> OperationalProfileRecord | None:
        if (normalized_name, dob) != ("ryan the", DOB):
            return None
        return OperationalProfileRecord(
            id=PROFILE_ID,
            user_id=PROFILE_OWNER_ID,
            normalized_name=normalized_name,
            interests=(),
            cg_name=None,
            telegram_contact_url=None,
            always_available=False,
            capacity=1,
            reserved_capacity=0,
        )

    async def find_active_for_login_user(
        self, user_id: UUID
    ) -> OperationalProfileRecord | None:
        assert user_id == INGRESS_USER_ID
        return await self.find_by_login_identity("ryan the", DOB)

    async def attach(
        self, profile_id: UUID, user_id: UUID, *, at: datetime
    ) -> LoginAttachmentResult:
        assert (profile_id, user_id, at) == (PROFILE_ID, INGRESS_USER_ID, NOW)
        return LoginAttachmentResult("attached", is_first_ever_attachment=True)

    async def detach_for_user(self, user_id: UUID, *, at: datetime) -> object:
        assert (user_id, at) == (INGRESS_USER_ID, NOW)
        return object()

    async def require_by_id(self, user_id: UUID) -> UserRecord:
        if user_id == PROFILE_OWNER_ID:
            return UserRecord(
                id=user_id,
                telegram_user_id=None,
                display_name="Ryan The",
                role=OperationalRole.LEADER,
                is_admin=True,
            )
        raise LookupError("user was not found")

    async def update_interests(
        self, profile_id: UUID, interests: list[str], *, at: datetime
    ) -> OperationalProfileRecord:
        assert (profile_id, at) == (PROFILE_ID, NOW)
        self.interests = interests
        profile = await self.find_active_for_login_user(INGRESS_USER_ID)
        assert profile is not None
        return profile


async def test_login_flow_keeps_dob_out_of_durable_attempt_state_and_updates_interests() -> (
    None
):
    """The name expires quickly; the DOB is consumed immediately and never stored."""

    uow = OperationalLoginFlowUow()
    accounts = OperationalAccountService(lambda: uow)

    await accounts.begin_login_in_uow(
        uow, user_id=INGRESS_USER_ID, name=" Ryan   The ", now=NOW
    )
    assert uow.attempt is not None
    assert uow.attempt.normalized_name == "ryan the"
    assert uow.attempt.expires_at > NOW
    assert not hasattr(uow.attempt, "dob")

    attached = await accounts.complete_login_in_uow(
        uow, user_id=INGRESS_USER_ID, dob=DOB, now=NOW
    )
    saved = await accounts.update_interests_in_uow(
        uow,
        user_id=INGRESS_USER_ID,
        interests=["music", " Music ", "football"],
        now=NOW,
    )
    logged_out = await accounts.logout_in_uow(uow, user_id=INGRESS_USER_ID, now=NOW)

    assert attached == LoginResult("attached", opens_interest_capture=True)
    assert saved is True
    assert uow.interests == ["music", "football"]
    assert logged_out == LoginResult("detached")
    assert uow.cleared is True
