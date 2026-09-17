"""Integration coverage for the explicit F01 Alembic revision and runtime guard."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import friendly_bot.persistence.models  # noqa: F401
from friendly_bot.persistence.base import Base

PROJECT_DIRECTORY = Path(__file__).parents[3]
F01_TABLES = {
    "capacity_reservations",
    "conversation_messages",
    "diagnostic_records",
    "flow_versions",
    "human_match_assignments",
    "human_match_exclusions",
    "human_match_requests",
    "open_flow_selections",
    "operational_login_attempts",
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


@dataclass(frozen=True, slots=True)
class MigratedDatabase:
    """A database created only for one test and populated only by Alembic."""

    database_name: str
    database_url: str = field(repr=False)


def runtime_guard_module() -> ModuleType:
    """Load the real operational script from a pytest path that contains only ``src``."""

    module_name = "friendly_bot_runtime_guard_test"
    script_path = Path(__file__).parents[3] / "scripts/db_runtime_check.py"
    specification = importlib.util.spec_from_file_location(module_name, script_path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


def _run_alembic(database_url: str, *arguments: str) -> None:
    """Run the real Alembic command without exposing the isolated URL in output."""

    environment = os.environ | {"FRIENDLY_BOT_DATABASE_URL": database_url}
    completed = subprocess.run(
        (sys.executable, "-m", "alembic", *arguments),
        check=False,
        cwd=PROJECT_DIRECTORY,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(f"alembic {' '.join(arguments)} failed")


def _asyncpg_connection_kwargs(database_url: str, database: str) -> dict[str, Any]:
    """Build a direct PostgreSQL connection from the explicitly supplied test URL."""

    url = make_url(database_url)
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": database,
    }


@pytest.fixture
async def migrated_database() -> AsyncIterator[MigratedDatabase]:
    """Create, upgrade, and later destroy a generated database without ORM DDL."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")

    source_url = make_url(database_url)
    database_name = f"friendly_bot_task7_{uuid4().hex}"
    generated_url = source_url.set(database=database_name).render_as_string(
        hide_password=False
    )
    admin = await asyncpg.connect(
        **_asyncpg_connection_kwargs(database_url, "postgres")
    )
    try:
        await admin.execute(f'CREATE DATABASE "{database_name}"')
        database = await asyncpg.connect(
            **_asyncpg_connection_kwargs(generated_url, database_name)
        )
        try:
            await database.execute("CREATE EXTENSION pgcrypto")
        finally:
            await database.close()

        _run_alembic(generated_url, "upgrade", "head")
        yield MigratedDatabase(
            database_name=database_name,
            database_url=generated_url,
        )
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        await admin.close()


@pytest.fixture
async def async_engine(
    migrated_database: MigratedDatabase,
) -> AsyncIterator[AsyncEngine]:
    """Inspect an Alembic-upgraded generated database through the async runtime."""

    engine = create_async_engine(migrated_database.database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def inspect_tables(async_engine: AsyncEngine) -> set[str]:
    """Inspect the public schema created by Alembic, not ORM create_all()."""

    async with async_engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )


def _schema_snapshot(sync_connection: Any) -> dict[str, Any]:
    """Capture migrated PostgreSQL DDL for parity assertions without ORM creation."""

    inspector = inspect(sync_connection)
    tables = set(inspector.get_table_names())
    return {
        "tables": tables,
        "columns": {
            table_name: {
                column["name"]: column for column in inspector.get_columns(table_name)
            }
            for table_name in F01_TABLES & tables
        },
        "foreign_keys": {
            table_name: {
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
            }
            for table_name in F01_TABLES & tables
        },
        "checks": {
            table_name: {
                check["name"]: check["sqltext"]
                for check in inspector.get_check_constraints(table_name)
            }
            for table_name in F01_TABLES & tables
        },
        "unique_constraints": {
            table_name: {
                constraint["name"]: tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table_name)
            }
            for table_name in F01_TABLES & tables
        },
        "enums": {
            enum["name"]: tuple(enum["labels"]) for enum in inspector.get_enums()
        },
        "index_definitions": {
            row["indexname"]: row["indexdef"]
            for row in sync_connection.exec_driver_sql(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
            ).mappings()
        },
        "extensions": {
            row["name"]
            for row in sync_connection.exec_driver_sql(
                "SELECT extname AS name FROM pg_extension"
            ).mappings()
        },
    }


async def test_alembic_head_exposes_open_selection_table(
    async_engine: AsyncEngine,
) -> None:
    """An incomplete initial revision would leave core F01 durable state absent."""

    tables = await inspect_tables(async_engine)

    assert tables == F01_TABLES | {"alembic_version"}


async def test_alembic_head_matches_every_f01_metadata_table_and_column(
    async_engine: AsyncEngine,
) -> None:
    """A hand-written revision must not silently omit a current F01 table or column."""

    async with async_engine.connect() as connection:
        actual = await connection.run_sync(_schema_snapshot)

    assert set(Base.metadata.tables) == F01_TABLES
    for table_name, table in Base.metadata.tables.items():
        expected_columns = {column.name: column for column in table.columns}
        assert set(actual["columns"][table_name]) == set(expected_columns)
        for column_name, expected_column in expected_columns.items():
            assert (
                actual["columns"][table_name][column_name]["nullable"]
                is expected_column.nullable
            )

    users = actual["columns"]["users"]
    profiles = actual["columns"]["operational_profiles"]
    selections = actual["columns"]["open_flow_selections"]
    assert users["id"]["type"].__class__.__name__ == "UUID"
    assert users["role"]["type"].__class__.__name__ == "ENUM"
    assert profiles["interests"]["type"].__class__.__name__ == "JSONB"
    assert selections["ancestor_flow_keys"]["type"].__class__.__name__ == "ARRAY"
    assert "gen_random_uuid" in str(users["id"]["default"])
    assert "nbnc" in str(users["role"]["default"])
    assert "false" in str(users["is_admin"]["default"])
    assert str(profiles["capacity"]["default"]) == "0"
    assert str(profiles["reserved_capacity"]["default"]) == "0"


async def test_alembic_head_exposes_nullable_service_interaction_closure(
    async_engine: AsyncEngine,
) -> None:
    """Lifecycle fencing needs durable state rather than a process-local flag."""

    async with async_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("services")
            }
        )

    assert columns["interaction_closed_at"]["nullable"] is True


async def test_pending_intent_revision_is_reversible(
    migrated_database: MigratedDatabase,
) -> None:
    """Structured pending work must upgrade and downgrade without residual state."""

    _run_alembic(
        migrated_database.database_url, "downgrade", "0004_service_interaction_closure"
    )
    engine = create_async_engine(migrated_database.database_url)
    try:
        async with engine.connect() as connection:
            before = await connection.run_sync(_schema_snapshot)
    finally:
        await engine.dispose()
    assert "pending_flow_intents" not in before["tables"]

    _run_alembic(migrated_database.database_url, "upgrade", "head")
    engine = create_async_engine(migrated_database.database_url)
    try:
        async with engine.connect() as connection:
            after = await connection.run_sync(_schema_snapshot)
    finally:
        await engine.dispose()
    assert set(after["columns"]["pending_flow_intents"]) == {
        "id",
        "user_id",
        "flow_key",
        "flow_version_id",
        "service_id",
        "position",
        "created_at",
        "expires_at",
    }


async def test_outbound_delivery_removal_is_reversible(
    migrated_database: MigratedDatabase,
) -> None:
    """Head removes delivery queue tables while its downgrade restores history."""

    engine = create_async_engine(migrated_database.database_url)
    try:
        async with engine.connect() as connection:
            at_head = await connection.run_sync(_schema_snapshot)
    finally:
        await engine.dispose()
    removed_tables = {
        "admin_notification_deliveries",
        "outbound_deliveries",
        "outbound_delivery_attempts",
        "telegram_outbound_pauses",
    }
    assert removed_tables.isdisjoint(at_head["tables"])
    assert "timestamp_delivery_claims" in at_head["tables"]

    _run_alembic(
        migrated_database.database_url, "downgrade", "0005_pending_flow_intents"
    )
    engine = create_async_engine(migrated_database.database_url)
    try:
        async with engine.connect() as connection:
            before_upgrade = await connection.run_sync(_schema_snapshot)
    finally:
        await engine.dispose()
    assert removed_tables <= before_upgrade["tables"]

    _run_alembic(migrated_database.database_url, "upgrade", "head")


async def test_alembic_head_enables_pgcrypto_and_named_f01_enums(
    async_engine: AsyncEngine,
) -> None:
    """UUID defaults and typed F01 values require these PostgreSQL-owned objects."""

    async with async_engine.connect() as connection:
        actual = await connection.run_sync(_schema_snapshot)

    assert "pgcrypto" in actual["extensions"]
    assert actual["enums"] == {
        "flow_scope_kind": ("system", "service", "timestamp"),
        "operational_role": ("nbnc", "server", "leader", "staff"),
        "service_audience": (
            "all_nbncs",
            "all_servers",
            "all_leaders",
            "service_nbncs",
            "service_servers",
            "service_leaders",
            "all_service_attendees",
        ),
    }


async def test_alembic_head_preserves_foreign_keys_and_named_constraints(
    async_engine: AsyncEngine,
) -> None:
    """The explicit revision must retain relational and named SQL protections."""

    async with async_engine.connect() as connection:
        actual = await connection.run_sync(_schema_snapshot)

    expected_foreign_keys = {
        table_name: {
            (
                tuple(element.parent.name for element in constraint.elements),
                constraint.elements[0].column.table.name,
                tuple(element.column.name for element in constraint.elements),
            )
            for constraint in table.foreign_key_constraints
        }
        for table_name, table in Base.metadata.tables.items()
    }
    assert actual["foreign_keys"] == expected_foreign_keys

    assert actual["checks"]["operational_profiles"] == {
        "ck_operational_profiles_capacity_nonnegative": "capacity >= 0",
        "ck_operational_profiles_reserved_capacity_nonnegative": "reserved_capacity >= 0",
        "ck_operational_profiles_reserved_capacity_within_capacity": "reserved_capacity <= capacity",
    }
    assert actual["checks"]["telegram_poll_state"] == {
        "ck_telegram_poll_state_singleton": "singleton_id = 1"
    }
    expected_named_unique_constraints = {
        table_name: {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint) and constraint.name is not None
        }
        for table_name, table in Base.metadata.tables.items()
    }
    assert {
        table_name: {
            name: actual["unique_constraints"][table_name][name]
            for name in expected_constraints
        }
        for table_name, expected_constraints in expected_named_unique_constraints.items()
    } == expected_named_unique_constraints

    index_definitions = {
        name: definition.lower()
        for name, definition in actual["index_definitions"].items()
    }
    expected_partial_indexes = {
        index.name: str(index.dialect_options["postgresql"]["where"]).lower()
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.dialect_options["postgresql"].get("where") is not None
    }
    assert {
        name: index_definitions[name] for name in expected_partial_indexes
    }.keys() == expected_partial_indexes.keys()
    for index_name, expected_where in expected_partial_indexes.items():
        if expected_where == "status in ('pending', 'retry')":
            assert "status" in index_definitions[index_name]
            assert "'pending'" in index_definitions[index_name]
            assert "'retry'" in index_definitions[index_name]
            continue
        normalized_expected = (
            expected_where.replace("::text", "")
            .replace("(", "")
            .replace(")", "")
            .replace(" ", "")
        )
        normalized_actual = (
            index_definitions[index_name]
            .replace("::text", "")
            .replace("(", "")
            .replace(")", "")
            .replace(" ", "")
        )
        assert normalized_expected in normalized_actual
    assert (
        "coalesce(service_id"
        in index_definitions["uq_open_flow_selections_user_version_parent_service"]
    )
    assert (
        "coalesce(service_id"
        in index_definitions["uq_flow_versions_scope_service_root_content"]
    )


async def test_alembic_downgrade_preserves_preexisting_pgcrypto(
    migrated_database: MigratedDatabase,
) -> None:
    """A shared extension established before F01 must survive a clean downgrade."""

    _run_alembic(migrated_database.database_url, "downgrade", "base")
    engine = create_async_engine(migrated_database.database_url)
    try:
        async with engine.connect() as connection:
            extension_exists = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto')"
                )
            )
        assert extension_exists is True
    finally:
        await engine.dispose()

    _run_alembic(migrated_database.database_url, "upgrade", "head")


def test_runtime_guard_rejects_unexpected_project() -> None:
    """A project typo must be rejected before a Docker command can be considered."""

    assert runtime_guard_module().main(["--project", "other-project"]) == 2


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--uid", "1"),
        ("--checkout", "/tmp/other-checkout"),
        ("--network", "other-network"),
        ("--volume", "other-volume"),
        ("--port", "15432"),
    ],
)
def test_runtime_guard_rejects_every_namespace_override(
    option: str, value: str
) -> None:
    """Each account namespace boundary must reject accidental cross-project input."""

    assert runtime_guard_module().main([option, value]) == 2


def test_runtime_guard_verify_only_checks_compose_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify validates the resolved namespace without creating Compose state."""

    db_runtime_check = runtime_guard_module()
    monkeypatch.setattr(db_runtime_check, "EXPECTED_UID", os.getuid())
    monkeypatch.setattr(
        db_runtime_check, "EXPECTED_CHECKOUT", PROJECT_DIRECTORY.parent.resolve()
    )
    monkeypatch.setattr(db_runtime_check, "FRIENDLY_BOT_DIRECTORY", PROJECT_DIRECTORY)
    monkeypatch.setattr(
        db_runtime_check, "COMPOSE_FILE", PROJECT_DIRECTORY / "compose.yaml"
    )

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        db_runtime_check,
        "run_compose",
        lambda arguments: calls.append(arguments),
    )

    assert db_runtime_check.main(["verify"]) == 0
    assert calls == [("config", "--quiet")]


def test_runtime_guard_reset_recreates_missing_runtime_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reset must recover a lost local credential file after dropping its volume."""

    db_runtime_check = runtime_guard_module()
    environment_file = tmp_path / "postgres.env"
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(db_runtime_check, "POSTGRES_ENV_FILE", environment_file)
    monkeypatch.setattr(
        db_runtime_check,
        "run_compose",
        lambda arguments: calls.append(arguments),
    )

    db_runtime_check.run_action("reset", confirm_reset=True)

    assert calls == [("down", "--volumes"), ("up", "-d", "postgres")]
    assert environment_file.is_file()
    assert environment_file.stat().st_mode & 0o777 == 0o600


def _docker_compose_is_available() -> bool:
    """Avoid treating the known host-tooling gap as a product test failure."""

    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ("docker", "compose", "version"),
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def _guarded_runtime_is_available() -> bool:
    """Run the real Compose canary only inside its fixed account and checkout."""

    if not _docker_compose_is_available():
        return False
    db_runtime_check = runtime_guard_module()
    return (
        os.getuid() == db_runtime_check.EXPECTED_UID
        and Path(db_runtime_check.__file__).resolve().parents[2]
        == db_runtime_check.EXPECTED_CHECKOUT.resolve()
    )


def test_runtime_guard_verify_succeeds_without_a_runtime_env_file() -> None:
    """Fresh verification must be read-only and valid before the first startup."""

    if not _guarded_runtime_is_available():
        pytest.skip("the fixed guarded runtime profile is unavailable on this host")

    db_runtime_check = runtime_guard_module()
    environment_file = db_runtime_check.POSTGRES_ENV_FILE
    preserved_environment_file = environment_file.with_name(
        f"postgres.env.task7-{uuid4().hex}"
    )
    had_environment_file = environment_file.exists()
    if had_environment_file:
        environment_file.replace(preserved_environment_file)
    try:
        assert db_runtime_check.main(["verify"]) == 0
        assert not environment_file.exists()
    finally:
        if had_environment_file:
            preserved_environment_file.replace(environment_file)
