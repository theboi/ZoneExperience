"""Integration coverage for the explicit F01 Alembic revision and runtime guard."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import friendly_bot.persistence.models  # noqa: F401
from friendly_bot.persistence.base import Base


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


@pytest.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    """Connect to the explicitly supplied disposable PostgreSQL database."""

    database_url = os.environ.get("FRIENDLY_BOT_DATABASE_URL")
    if not database_url:
        pytest.skip("FRIENDLY_BOT_DATABASE_URL is required for PostgreSQL integration")

    engine = create_async_engine(database_url)
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


async def test_alembic_head_exposes_open_selection_table(
    async_engine: AsyncEngine,
) -> None:
    """An incomplete initial revision would leave core F01 durable state absent."""

    tables = await inspect_tables(async_engine)

    assert {
        "flow_versions",
        "open_flow_selections",
        "processed_telegram_updates",
    } <= tables


async def test_alembic_head_matches_every_f01_metadata_table_and_column(
    async_engine: AsyncEngine,
) -> None:
    """A hand-written revision must not silently omit a current F01 table or column."""

    async with async_engine.connect() as connection:
        actual = await connection.run_sync(
            lambda sync_connection: {
                table_name: {
                    column["name"]
                    for column in inspect(sync_connection).get_columns(table_name)
                }
                for table_name in Base.metadata.tables
            }
        )

    expected = {
        table_name: {column.name for column in table.columns}
        for table_name, table in Base.metadata.tables.items()
    }
    assert actual == expected


async def test_alembic_head_enables_pgcrypto_and_named_f01_enums(
    async_engine: AsyncEngine,
) -> None:
    """UUID defaults and typed F01 values require these PostgreSQL-owned objects."""

    async with async_engine.connect() as connection:
        extensions, enum_names = await connection.run_sync(
            lambda sync_connection: (
                {
                    row["name"]
                    for row in sync_connection.exec_driver_sql(
                        "SELECT extname AS name FROM pg_extension"
                    ).mappings()
                },
                {enum["name"] for enum in inspect(sync_connection).get_enums()},
            )
        )

    assert "pgcrypto" in extensions
    assert {"operational_role", "flow_scope_kind", "service_audience"} <= enum_names


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

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        db_runtime_check,
        "run_compose",
        lambda arguments: calls.append(arguments),
    )

    assert db_runtime_check.main(["verify"]) == 0
    assert calls == [("config", "--quiet")]
