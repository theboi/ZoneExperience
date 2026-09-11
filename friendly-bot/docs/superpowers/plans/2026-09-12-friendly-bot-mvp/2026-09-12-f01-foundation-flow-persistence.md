# F01 Foundation, Flow Engine, and Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the shared Python foundation, validated immutable recursive flow contract, deterministic selection state engine, and durable async PostgreSQL persistence layer required by Friendly Bot.

**Architecture:** Pydantic v2 discriminated unions validate flow JSON before append-only JSONB publication. A pure transition engine computes branch mutations while an async SQLAlchemy unit of work atomically applies them with per-user locking and database idempotency constraints. Docker Compose supplies one account-isolated PostgreSQL instance and Alembic owns the only schema sequence.

**Tech Stack:** Python 3.12+, uv, Pydantic v2, SQLAlchemy 2 async, asyncpg, Alembic, PostgreSQL 16, Docker Compose, pytest/pytest-asyncio, Ruff, and mypy.

**Spec:** `friendly-bot/docs/superpowers/specs/2026-09-12-friendly-bot-mvp/2026-09-12-f01-foundation-flow-persistence-design.md`

## Global Constraints

- Work begins only after the coordinator's passed `PG` receipt is reachable from `origin/main`.
- Keep all implementation under `friendly-bot/`; use Python 3.12+, Pydantic v2, SQLAlchemy 2 async, Alembic, asyncpg, pytest/pytest-asyncio, Ruff, and mypy.
- `DiscussionFlow` is the sole flow type; typed triggers/actions are configuration only and executors remain I04-owned.
- Persist immutable definitions as JSONB and runtime selections relationally; never introduce a flow-instance table, copied candidate lists, trigger stack, Telegram-message mapping, compatibility adapter, or fallback path.
- Bind only `127.0.0.1:5832`, use Compose project/network/volume `friendly-bot-u504`, and mutate Docker only after identity/namespace checks.
- Every task follows red → minimal implementation → focused green → exact-path commit, then fetch/merge, rerun its affected check, push, and prove the commit reachable from `origin/main` while holding `/tmp/friendly-bot-main-mutation-u504.lock`.

## File Structure

| Path | Responsibility |
| --- | --- |
| `friendly-bot/pyproject.toml`, `uv.lock`, `.gitignore` | Package metadata, pinned resolver graph, tools, ignored runtime state |
| `friendly-bot/src/friendly_bot/config/settings.py` | Typed redacted database configuration |
| `friendly-bot/src/friendly_bot/domain/{events,flows,triggers,actions,publication,state}.py` | Flow DTOs, registries, publication validation, pure transitions |
| `friendly-bot/src/friendly_bot/persistence/{base,models,repositories,uow}.py` | Metadata, ORM records, typed repository implementations, async transaction boundary |
| `friendly-bot/alembic/{env.py,script.py.mako,versions/0001_foundation.py}` | Sole migration sequence |
| `friendly-bot/compose.yaml`, `friendly-bot/scripts/db_runtime_check.py` | Account-isolated database and mutation guard |
| `friendly-bot/tests/unit/domain/` | Flow parsing, validation, and transition unit tests |
| `friendly-bot/tests/integration/persistence/` | Migration, rollback, constraints, and idempotency integration tests |

### Task 1: Package, tooling, and safe database configuration

**Files:**
- Create: `friendly-bot/pyproject.toml`, `friendly-bot/.gitignore`, `friendly-bot/src/friendly_bot/__init__.py`, `friendly-bot/src/friendly_bot/config/__init__.py`, `friendly-bot/src/friendly_bot/config/settings.py`, `friendly-bot/tests/unit/config/test_settings.py`
- Create: `friendly-bot/uv.lock`

**Interfaces:**
- Produces: `DatabaseSettings.database_url: PostgresDsn`, `DatabaseSettings.redacted_url() -> str`.
- Consumes: environment variable `FRIENDLY_BOT_DATABASE_URL`.

- [ ] **Step 1: Write the failing configuration tests.**

```python
def test_default_database_url_uses_isolated_loopback_port() -> None:
    assert DatabaseSettings().database_url.host == "127.0.0.1"
    assert DatabaseSettings().database_url.port == 5832

def test_redacted_url_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRIENDLY_BOT_DATABASE_URL", "postgresql+asyncpg://u:secret@127.0.0.1:5832/db")
    assert "secret" not in DatabaseSettings().redacted_url()
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/unit/config/test_settings.py -q`

Expected: FAIL because the package and `DatabaseSettings` do not exist.

- [ ] **Step 3: Create the package/tool configuration and minimal settings model.**

```python
class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRIENDLY_BOT_")
    database_url: PostgresDsn = "postgresql+asyncpg://friendly_bot:friendly_bot@127.0.0.1:5832/friendly_bot"

    def redacted_url(self) -> str:
        return self.database_url._url.render_as_string(hide_password=True)
```

Configure `requires-python = ">=3.12"`, the exact dependencies in the spec, strict mypy, Ruff Python 3.12, and pytest asyncio auto mode; generate `uv.lock`; ignore only `.runtime/`, `.venv/`, cache, and coverage outputs.

- [ ] **Step 4: Run focused configuration and tooling checks.**

Run: `cd friendly-bot && uv run pytest tests/unit/config/test_settings.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot`

Expected: each command exits 0.

- [ ] **Step 5: Commit this coherent package checkpoint.**

Run: `git add friendly-bot/pyproject.toml friendly-bot/uv.lock friendly-bot/.gitignore friendly-bot/src/friendly_bot/__init__.py friendly-bot/src/friendly_bot/config friendly-bot/tests/unit/config/test_settings.py && git commit -m "build: add friendly bot foundation tooling"`

### Task 2: Registered trigger and action configuration unions

**Files:**
- Create: `friendly-bot/src/friendly_bot/domain/__init__.py`, `events.py`, `triggers.py`, `actions.py`
- Create: `friendly-bot/tests/unit/domain/test_triggers.py`, `test_actions.py`

**Interfaces:**
- Produces: `DiscussionFlowTrigger`, `DiscussionAction`, `ActionEvent`, `parse_trigger(data: dict[str, object])`, `parse_action(data: dict[str, object])`.
- Consumes: Pydantic discriminators named in Zone X.

- [ ] **Step 1: Write failing union/fixture tests.**

```python
def test_zone_x_action_discriminators_parse() -> None:
    action = parse_action({"type": "find_and_reserve_server", "service_id": "{{ active_service.id }}", "capacity_required": 1})
    assert action.declared_event_keys == frozenset({"human_match.found", "human_match.not_found"})

def test_nested_any_of_and_unknown_discriminator_are_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_trigger({"type": "any_of", "triggers": [{"type": "any_of", "triggers": []}]})
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_triggers.py tests/unit/domain/test_actions.py -q`

Expected: FAIL because trigger/action modules are absent.

- [ ] **Step 3: Implement the closed registered unions.**

```python
DiscussionFlowTrigger = Annotated[
    MessageDiscussionFlowTrigger | ButtonDiscussionFlowTrigger | CommandDiscussionFlowTrigger |
    AutomaticDiscussionFlowTrigger | ActionEventDiscussionFlowTrigger | AnyOfDiscussionFlowTrigger,
    Field(discriminator="type"),
]

class FindAndReserveServerAction(DiscussionActionBase):
    type: Literal["find_and_reserve_server"]
    service_id: str
    capacity_required: PositiveInt = 1
    declared_event_keys: ClassVar[frozenset[str]] = frozenset({"human_match.found", "human_match.not_found"})
```

Implement all action discriminators enumerated in the F01 spec, stable key/button regex checks, nonempty `llm_gist`, and `AnyOf` flattening rules. Do not add executor callables.

- [ ] **Step 4: Run the focused union tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_triggers.py tests/unit/domain/test_actions.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the typed configuration contract.**

Run: `git add friendly-bot/src/friendly_bot/domain friendly-bot/tests/unit/domain/test_triggers.py friendly-bot/tests/unit/domain/test_actions.py && git commit -m "feat: define typed flow triggers and actions"`

### Task 3: Recursive flow parsing and publication validation

**Files:**
- Create: `friendly-bot/src/friendly_bot/domain/flows.py`, `publication.py`
- Create: `friendly-bot/tests/unit/domain/test_publication.py`, `fixtures.py`

**Interfaces:**
- Produces: `DiscussionFlow`, `NextFlowMode`, `TemplateContextSchema`, `PublishedFlowDefinition`, `validate_for_publication(root, context_schema, root_kind)`.
- Consumes: Task 2 registered unions.

- [ ] **Step 1: Write failing publication invariant tests.**

```python
@pytest.mark.parametrize("mutator", [duplicate_key, missing_event_handler, two_error_handlers, nonterminal_event_action])
def test_invalid_recursive_definition_is_not_publishable(mutator: Callable[[DiscussionFlow], None]) -> None:
    root = valid_system_checkpoint()
    mutator(root)
    with pytest.raises(FlowPublicationError):
        validate_for_publication(root, TemplateContextSchema({"user.name"}), RootKind.SYSTEM)

def test_published_definition_is_canonical_and_hash_stable() -> None:
    published = validate_for_publication(valid_system_checkpoint(), TemplateContextSchema({"user.name"}), RootKind.SYSTEM)
    assert published.content_hash == sha256(canonical_json(published.document)).hexdigest()
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q`

Expected: FAIL because publication validation is absent.

- [ ] **Step 3: Implement recursive validation.**

```python
def validate_for_publication(root: DiscussionFlow, context_schema: TemplateContextSchema, root_kind: RootKind) -> PublishedFlowDefinition:
    index = _index_unique_keys(root)
    _validate_root(root, root_kind)
    _validate_nodes(root, index, context_schema)
    _reject_event_only_cycles(root)
    document = root.model_dump(mode="json")
    return PublishedFlowDefinition(document=document, flow_key_index=index, content_hash=_hash(document))
```

Check every exact invariant from the F01 spec, including direct-child-only event completeness, timestamp root rules, checkpoint child requirement, templates, and warning capture for a no-op leaf.

- [ ] **Step 4: Run the focused publication suite.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_publication.py -q`

Expected: all valid roots publish and every invalid invariant fails.

- [ ] **Step 5: Commit the publication boundary.**

Run: `git add friendly-bot/src/friendly_bot/domain/flows.py friendly-bot/src/friendly_bot/domain/publication.py friendly-bot/tests/unit/domain/test_publication.py friendly-bot/tests/unit/domain/fixtures.py && git commit -m "feat: validate immutable discussion flow publications"`

### Task 4: Pure selection and checkpoint transition engine

**Files:**
- Create: `friendly-bot/src/friendly_bot/domain/state.py`
- Create: `friendly-bot/tests/unit/domain/test_state.py`

**Interfaces:**
- Produces: `OpenSelectionState`, `SelectionTransition`, `CheckpointReturnTransition`, `SelectionTransitionEngine.select_child`, and `.return_to_nearest_checkpoint`.
- Consumes: Task 3 `DiscussionFlow` and `NextFlowMode`.

- [ ] **Step 1: Write failing transition examples.**

```python
@pytest.mark.parametrize("mode,expect_deleted,expect_reusable", [
    (NextFlowMode.ONE_AND_ONCE_ONLY, True, False),
    (NextFlowMode.ALLOW_MANY, False, True),
    (NextFlowMode.CHECKPOINT, False, True),
])
def test_current_parent_transition(mode: NextFlowMode, expect_deleted: bool, expect_reusable: bool) -> None: ...

def test_leaf_returns_only_its_nearest_nested_checkpoint() -> None:
    transition = engine.return_to_nearest_checkpoint(branch=nested_branch, now=NOW)
    assert transition.target_checkpoint_key == "service.zone_x.questions"
    assert "unrelated.timestamp" not in transition.current_selection_ids
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_state.py -q`

Expected: FAIL because the state engine is absent.

- [ ] **Step 3: Implement data-only state transitions.**

```python
def select_child(self, *, parent: OpenSelectionState, child: DiscussionFlow, parent_definition: DiscussionFlow, now: datetime) -> SelectionTransition:
    if child.key in self._executed_flow_keys:
        raise DuplicateFlowExecutionError(child.key)
    self._executed_flow_keys.add(child.key)
    return _build_transition(parent, child, parent_definition, now)
```

Cover past reuse, child selection deduplication, independent current branches, checkpoint ancestry, explicit-return suppression of generic leaf return, and system-checkpoint repetition. Do not call action executors.

- [ ] **Step 4: Run the focused state suite.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain/test_state.py -q`

Expected: all transition cases pass.

- [ ] **Step 5: Commit the deterministic state contract.**

Run: `git add friendly-bot/src/friendly_bot/domain/state.py friendly-bot/tests/unit/domain/test_state.py && git commit -m "feat: add flow selection transition engine"`

### Task 5: SQLAlchemy metadata and all durable ORM records

**Files:**
- Create: `friendly-bot/src/friendly_bot/persistence/__init__.py`, `base.py`, `models.py`
- Create: `friendly-bot/tests/integration/persistence/test_schema_constraints.py`

**Interfaces:**
- Produces: `Base.metadata` and ORM records for every table listed in the F01 spec.
- Consumes: PostgreSQL UUID, JSONB, named enum, partial-index, and exclusion/constraint facilities.

- [ ] **Step 1: Write failing schema-constraint integration tests.**

```python
async def test_processed_update_id_is_unique(session: AsyncSession) -> None:
    session.add_all([ProcessedTelegramUpdate(update_id=7), ProcessedTelegramUpdate(update_id=7)])
    with pytest.raises(IntegrityError):
        await session.commit()

async def test_active_overlapping_attendance_is_rejected(session: AsyncSession) -> None: ...
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/integration/persistence/test_schema_constraints.py -q`

Expected: FAIL because persistence metadata and test database fixture are absent.

- [ ] **Step 3: Define all models in one metadata registry.**

```python
class OpenFlowSelection(Base):
    __tablename__ = "open_flow_selections"
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    flow_version_id: Mapped[UUID] = mapped_column(ForeignKey("flow_versions.id"))
    parent_flow_key: Mapped[str]
    checkpoint_flow_keys: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
```

Implement every table and exact durable uniqueness/foreign-key/check constraint in the F01 spec, including active attendance, idempotency tables, match records, diagnostics, and `reserved_capacity <= capacity`. Keep ORM rows private to persistence.

- [ ] **Step 4: Run schema constraints after applying metadata to the integration database.**

Run: `cd friendly-bot && uv run pytest tests/integration/persistence/test_schema_constraints.py -q`

Expected: all required uniqueness, overlap, and capacity constraint cases pass.

- [ ] **Step 5: Commit the schema metadata checkpoint.**

Run: `git add friendly-bot/src/friendly_bot/persistence friendly-bot/tests/integration/persistence/test_schema_constraints.py && git commit -m "feat: define friendly bot persistence schema"`

### Task 6: Async repositories and transactional unit of work

**Files:**
- Create: `friendly-bot/src/friendly_bot/persistence/repositories.py`, `uow.py`
- Create: `friendly-bot/tests/integration/persistence/test_repositories.py`, `test_uow.py`

**Interfaces:**
- Produces: `UnitOfWork`, `FlowVersionRepository`, `OpenSelectionRepository`, `UpdateRepository`, `DeliveryRepository`, and the typed repository collection in the F01 spec.
- Consumes: Task 4 transitions and Task 5 ORM records.

- [ ] **Step 1: Write failing repository transaction and idempotency tests.**

```python
async def test_uow_rolls_back_open_selection_when_action_effect_fails(uow_factory: UnitOfWorkFactory) -> None: ...

async def test_concurrent_update_claim_has_exactly_one_winner(uow_factory: UnitOfWorkFactory) -> None:
    winners = await asyncio.gather(*[claim_same_update(uow_factory) for _ in range(2)])
    assert winners.count(True) == 1
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/integration/persistence/test_repositories.py tests/integration/persistence/test_uow.py -q`

Expected: FAIL because repository and UoW modules are absent.

- [ ] **Step 3: Implement transaction-scoped repository methods.**

```python
class UnitOfWork:
    async def __aenter__(self) -> Self:
        self.session = self._session_factory()
        self._transaction = await self.session.begin()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await (self._transaction.rollback() if exc_type else self._transaction.commit())
        await self.session.close()
```

Use `INSERT ... ON CONFLICT DO NOTHING RETURNING` for update/delivery claims, `SELECT FOR UPDATE` for user serialization, guarded reservation updates, and return domain DTOs only. `FlowVersionRepository.publish` persists the canonical JSONB/hash exactly once.

- [ ] **Step 4: Run focused repository tests.**

Run: `cd friendly-bot && uv run pytest tests/integration/persistence/test_repositories.py tests/integration/persistence/test_uow.py -q`

Expected: rollback, one-winner claims, guarded capacity, selection persistence, and flow publication tests pass.

- [ ] **Step 5: Commit repositories and UoW.**

Run: `git add friendly-bot/src/friendly_bot/persistence/repositories.py friendly-bot/src/friendly_bot/persistence/uow.py friendly-bot/tests/integration/persistence/test_repositories.py friendly-bot/tests/integration/persistence/test_uow.py && git commit -m "feat: add async repositories and unit of work"`

### Task 7: Alembic migration and account-isolated Compose runtime

**Files:**
- Create: `friendly-bot/alembic.ini`, `friendly-bot/alembic/env.py`, `friendly-bot/alembic/script.py.mako`, `friendly-bot/alembic/versions/0001_foundation.py`
- Create: `friendly-bot/compose.yaml`, `friendly-bot/scripts/db_runtime_check.py`, `friendly-bot/tests/integration/persistence/test_migrations.py`

**Interfaces:**
- Produces: `alembic upgrade head` from an empty database and Compose PostgreSQL at `127.0.0.1:5832`.
- Consumes: Task 5 metadata and `DatabaseSettings`.

- [ ] **Step 1: Write failing migration/readiness tests.**

```python
async def test_alembic_head_exposes_open_selection_table(async_engine: AsyncEngine) -> None:
    tables = await inspect_tables(async_engine)
    assert {"flow_versions", "open_flow_selections", "processed_telegram_updates"} <= tables

def test_runtime_guard_rejects_unexpected_project() -> None:
    assert main(["--project", "other-project"]) == 2
```

- [ ] **Step 2: Run the test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/integration/persistence/test_migrations.py -q`

Expected: FAIL because migration and Compose files are absent.

- [ ] **Step 3: Generate an explicit initial migration and isolated runtime files.**

```python
def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    asyncio.run(_run_async_migrations(connectable))
```

Write every table/index/constraint explicitly into revision `0001_foundation`; do not rely on runtime autogenerate. Compose must publish only `127.0.0.1:5832:5432`; the guard verifies UID 504, `/Users/bot2/Dev/ZoneExperience`, project, namespace, and port before it permits `up`, `down`, or `reset` commands.

- [ ] **Step 4: Run fresh-database migration and runtime checks.**

Run: `cd friendly-bot && python scripts/db_runtime_check.py verify && docker compose -p friendly-bot-u504 up -d postgres && until pg_isready -h 127.0.0.1 -p 5832 -U friendly_bot -d friendly_bot; do sleep 1; done && FRIENDLY_BOT_DATABASE_URL="$FRIENDLY_BOT_DATABASE_URL" uv run alembic upgrade head && uv run pytest tests/integration/persistence/test_migrations.py -q`

Expected: guard verifies the exact namespace, PostgreSQL becomes ready, upgrade exits 0, and migration tests pass. Do not print credentials.

- [ ] **Step 5: Commit migration and Compose checkpoint.**

Run: `git add friendly-bot/alembic.ini friendly-bot/alembic friendly-bot/compose.yaml friendly-bot/scripts/db_runtime_check.py friendly-bot/tests/integration/persistence/test_migrations.py && git commit -m "feat: add friendly bot database migration runtime"`

### Task 8: Foundation acceptance suite and downstream handoff

**Files:**
- Modify: `friendly-bot/tests/unit/domain/test_publication.py`, `test_state.py`
- Modify: `friendly-bot/tests/integration/persistence/test_repositories.py`, `test_migrations.py`
- Create: `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/f01-execution.json`

**Interfaces:**
- Produces: reproducible F01 evidence for G1 and a stable import contract for T02/R03/I04.
- Consumes: all previous tasks.

- [ ] **Step 1: Add the final missing acceptance examples before any repair.**

```python
def test_unhandled_error_has_no_configured_default_flow() -> None:
    assert validate_for_publication(flow_without_error_child(), schema, RootKind.SYSTEM)

async def test_timestamp_delivery_claim_is_idempotent(delivery_repository: DeliveryRepository) -> None:
    assert await delivery_repository.claim_timestamp_delivery(TIMESTAMP_ID, USER_ID)
    assert not await delivery_repository.claim_timestamp_delivery(TIMESTAMP_ID, USER_ID)
```

- [ ] **Step 2: Run the focused acceptance tests to verify any gap fails.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain tests/integration/persistence -q`

Expected: a failure identifies any missing direct-event, checkpoint, migration, or idempotency behavior before repair.

- [ ] **Step 3: Make only the minimal repairs required by the failing evidence.**

```python
# Preserve the invariant: unhandled `error` is represented by absence of a
# direct error child; it is dispatched later by I04's hardcoded sender.
assert "error" not in action.declared_event_keys
```

Do not add a configured default error flow, action executor, router, Telegram integration, Zone X seed, or compatibility layer.

- [ ] **Step 4: Run the complete F01 verification set.**

Run: `cd friendly-bot && uv run pytest tests/unit/domain tests/integration/persistence -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot && uv run alembic upgrade head`

Expected: every command exits 0; record command, result, migration revision, remote commit, and redacted database endpoint in the execution manifest.

- [ ] **Step 5: Commit final F01 evidence.**

Run: `git add friendly-bot/tests/unit/domain/test_publication.py friendly-bot/tests/unit/domain/test_state.py friendly-bot/tests/integration/persistence/test_repositories.py friendly-bot/tests/integration/persistence/test_migrations.py friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/f01-execution.json && git commit -m "test: verify foundation flow persistence evidence"`

## Plan Self-Review

The eight tasks cover every F01 outcome: package/tooling (1), registered flow configuration (2), publication (3), state transitions (4), complete schema (5), repositories/UoW (6), migrations/isolated Compose (7), and G1 evidence (8). The plan intentionally leaves Telegram/service behavior, OpenRouter/persona/matching behavior, action registry, and Zone X seed to their owners. Interface names in repository tasks match the design contract. A placeholder scan found no unfinished markers or deferred implementation wording; each implementation task includes a failing test, a focused command, an implementation shape, a green command, and an exact commit.
