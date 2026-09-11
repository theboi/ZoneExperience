# F01 Foundation, Flow Engine, and Persistence Design

| Field | Value |
| --- | --- |
| Status | Planning complete; execution blocked pending `PG` |
| Date | 2026-09-12 |
| Owner | F01 |
| Authority inputs | Product specification, architecture masterplan, approved MVP design, Zone X fixture, and frozen implementation masterplan at `origin/main` `687a900` |
| Scope | Package/tooling, typed flow contract and transition engine, PostgreSQL schema/repositories/unit of work, migrations, and account-isolated database Compose configuration |
| Excluded ownership | Telegram transport and service scheduling behavior (T02); OpenRouter, persona, and matching behavior (R03); action-executor composition and Zone X JSON seed (I04); all living authorities |

## 1. Purpose and boundaries

F01 supplies the shared executable foundation that every later package imports: a Python 3.12+ package, validated immutable recursive `DiscussionFlow` definitions, deterministic selection/checkpoint transitions, and an async PostgreSQL persistence contract. It deliberately models configuration and durable state without implementing Telegram calls, routing-provider calls, matching policy, scheduler behavior, or central action dispatch.

`DiscussionFlow` remains the sole flow type. Real gatherings are `Service` records; `ActionEvent` is only an internal action-execution outcome. Definitions use Pydantic v2 discriminated unions and publish to JSONB only after validation. Open selections point to an immutable `FlowVersion` and reconstruct candidate children from that document; no `DiscussionFlowInstance`, copied candidate list, trigger stack, or Telegram-message-to-flow mapping exists.

## 2. Package and configuration contract

`friendly-bot/pyproject.toml` declares `requires-python = ">=3.12"`, a `src/` package layout, and runtime dependencies `pydantic>=2,<3`, `pydantic-settings>=2,<3`, `SQLAlchemy>=2,<3`, `asyncpg>=0.29,<1`, and `alembic>=1.13,<2`. Its development dependency group contains `pytest>=8,<9`, `pytest-asyncio>=0.24,<1`, `ruff>=0.6,<1`, and `mypy>=1.11,<2`. `uv.lock` is the committed resolver lock. Pytest uses `asyncio_mode = "auto"`; Ruff targets Python 3.12; mypy runs strict checking over `src/friendly_bot`.

`config.settings.DatabaseSettings` requires a non-empty `FRIENDLY_BOT_DATABASE_URL`; it has no source-coded URL or credential default. Tests set an explicit dummy URL, while ignored `.runtime/u504/postgres.env` supplies local Compose credentials. Secrets remain environment-supplied and are never printed. `DatabaseSettings.redacted_url()` replaces the password before an operational result is logged.

Compose is the tracked database source. `compose.yaml` uses project name `friendly-bot-u504`, service name `postgres`, network `friendly-bot-u504`, volume `friendly-bot-u504-postgres`, and publishes exactly `127.0.0.1:5832:5432`. It gets database user, password, and database name from ignored `.runtime/u504/postgres.env`; `.runtime/` is ignored. Its healthcheck is `pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}`. F01 operational commands reject a mismatched macOS UID, checkout path, Compose project, network, volume, or port before Docker mutation.

## 3. Typed flow-definition contract

The domain API lives in `friendly_bot.domain`:

```python
class NextFlowMode(StrEnum):
    ONE_AND_ONCE_ONLY = "one_and_once_only"
    ALLOW_MANY = "allow_many"
    CHECKPOINT = "checkpoint"

class ActionEvent(BaseModel):
    key: str
    payload: dict[str, JsonValue] | None = None

class DiscussionFlow(BaseModel):
    key: str
    trigger: DiscussionFlowTrigger | None = None
    actions: list[DiscussionAction] = Field(default_factory=list)
    next_flows: list[DiscussionFlow] = Field(default_factory=list)
    next_flow_mode: NextFlowMode
    return_actions: list[DiscussionAction] = Field(default_factory=list)
```

`DiscussionFlowTrigger` is a `Annotated[..., Field(discriminator="type")]` union of `message`, `button`, `command`, `automatic`, `action_event`, and `any_of`. `AnyOfDiscussionFlowTrigger` contains only non-`any_of` registered triggers, has at least two distinct children, and rejects nested `any_of`. Commands normalize `/name@friendly_bot` to `/name` at the transport boundary; F01 validates the stored command begins with `/` and has no `@` suffix.

`DiscussionAction` is a similarly discriminated registered union. F01 provides models for every type in the canonical fixture: `send_message`, `send_buttons`, `send_photo`, `show_activity`, `save_incoming`, `add_service_attendance`, `select_service_attendance`, `enter_service_checkpoint`, `enter_selected_service_checkpoint`, `enter_selected_service_latecomer_flow`, `resolve_service_switch_options`, `find_and_reserve_server`, `find_and_reserve_safety_responder`, `confirm_human_match`, `release_human_match`, `notify_matched_human`, `notify_previous_human`, `notify_all_admins`, `exclude_previous_human_from_next_attempt`, `share_human_contact`, `mark_safety_request_pending`, `end_service_interactions`, and `return_to_nearest_checkpoint`. The action model contains only validated declarative parameters, never executor names or arbitrary code.

Each action class exposes `declared_event_keys: ClassVar[frozenset[str]]`. `find_and_reserve_server` declares `human_match.found` and `human_match.not_found`; `find_and_reserve_safety_responder` declares `safety_match.found` and `safety_match.not_found`; service-selection actions declare their documented service outcomes. An event-emitting action is valid only as the last action in its list. `error` is reserved: no action declares it as a normal outcome.

Templates use `{{ dotted.path }}` variables. `TemplateContextSchema` records permitted dotted names for a publishable root. Every referenced variable must be declared, and every action field marked templatable must be a string. Button IDs match `^[a-z][a-z0-9_.-]{2,127}$`; flow keys match `^[a-z][a-z0-9_.-]{2,127}$`.

## 4. Publication validation and immutable versions

`validate_for_publication(root, context_schema, root_kind)` traverses the full recursive definition and returns a `PublishedFlowDefinition` only when all invariants hold. The result stores canonical `model_dump(mode="json")`, `flow_key_index`, and deterministic SHA-256 `content_hash`; callers persist the canonical document rather than mutable model objects.

Validation rejects duplicate flow keys, unknown discriminators, root triggers, a system/service root that is not `CHECKPOINT`, a checkpoint without a child, `return_actions` on another mode, undeclared or unresolved templates, invalid button IDs, a nonterminal outcome action, duplicate direct event handlers, a missing direct handler for a declared non-error outcome, more than one direct `error` child, and a reachable event-only cycle. Direct event handling considers only immediate `next_flows`. Timestamp roots use `trigger=None`, may use any mode, and take an inherited service checkpoint ancestry supplied by the caller. An empty action/child flow produces a structured warning but is publishable.

`FlowVersion` is append-only after publication: `id`, `scope_kind` (`system`, `service`, `timestamp`), nullable `service_id`, `root_flow_key`, JSONB `definition`, `content_hash`, `published_at`, and `published_by_user_id`. A unique constraint on `(scope_kind, service_id, root_flow_key, content_hash)` makes repeated publish requests idempotent. Open selections always reference the version selected at entry; later versions never rewrite them.

## 5. Selection and checkpoint transition contract

`OpenSelectionState` is the domain representation of the durable row: `id`, `user_id`, `flow_version_id`, `parent_flow_key`, `service_id | None`, `is_current`, `is_global_interruptive`, `ancestor_flow_keys`, `checkpoint_flow_keys`, `opened_at`, and `last_focused_at`.

```python
class SelectionTransitionEngine:
    def select_child(
        self, *, parent: OpenSelectionState, child: DiscussionFlow,
        parent_definition: DiscussionFlow, now: datetime,
    ) -> SelectionTransition
    def return_to_nearest_checkpoint(
        self, *, branch: OpenSelectionState, now: datetime,
    ) -> CheckpointReturnTransition
```

`select_child` is pure: it describes rows to delete, upsert, demote to reusable past, mark current, and which child/checkpoint actions the application must execute. It does not run an action executor or query PostgreSQL. A successful caller applies the returned mutation and action effects inside one per-user `UnitOfWork` transaction.

Selecting from current removes current focus. `ONE_AND_ONCE_ONLY` deletes the parent; `ALLOW_MANY` retains it as reusable past; `CHECKPOINT` retains it as reusable past and preserves it in the selected branch's checkpoint ancestry. A child with children adds or deduplicates its own current selection; unrelated current selections stay untouched. Selecting a reusable `ALLOW_MANY` or `CHECKPOINT` parent does not remove it. The same flow key is accepted at most once per incoming update by an application-owned `executed_flow_keys` set passed to the engine.

For a leaf, the engine targets the nearest key in the branch's `checkpoint_flow_keys`, returns that checkpoint's `return_actions`, and reopens its children. A `return_to_nearest_checkpoint` action marks the leaf as already returned so the generic leaf rule cannot return twice. Never-mind invokes the same operation; when invoked from a checkpoint it pops that checkpoint and chooses its parent, while the system checkpoint repeats its own return actions. Parallel branches retain independent checkpoint ancestry.

## 6. PostgreSQL schema and constraints

F01 owns one SQLAlchemy metadata registry and one Alembic revision sequence. UUID primary keys use PostgreSQL `gen_random_uuid()` and timestamps are `TIMESTAMPTZ`. Mutable runtime records include `created_at` and `updated_at`; JSON definitions and payloads use JSONB. SQL constraints, rather than process memory, protect durable uniqueness and idempotency.

| Area | Tables and essential constraints |
| --- | --- |
| Identities | `users` (unique nullable `telegram_user_id`, display name, sole role source, `is_admin`); `operational_profiles` (one-to-one user, normalized name/DOB, contact URL, `always_available`, `capacity >= 0`); `operational_logins` (unique profile and user attachment history) |
| Flow and service config | `services` (unique stable key, timezone, lifecycle times); `flow_versions` (append-only JSONB); `service_timestamps` (unique `(service_id, key, flow_version_id)`); `service_attendances` (unique `(service_id, user_id)`, partial unique active overlap per user); `open_flow_selections` (unique `(user_id, flow_version_id, parent_flow_key, service_id)` using `COALESCE(service_id, nil UUID)`) |
| Conversation and routing durability | `conversation_messages` (unique `(user_id, source_kind, source_message_id)` where source id exists); `persona_cursors` (one per user); `telegram_poll_state` (singleton); `processed_telegram_updates` (unique update id); `user_processing_locks` (one per user, locked with `SELECT ... FOR UPDATE`) |
| Scheduler and outbound delivery | `timestamp_delivery_claims` (unique `(service_timestamp_id, user_id)`); `outbound_deliveries` (unique idempotency key); `outbound_delivery_attempts` (append-only attempt number per delivery) |
| Human connection | `human_match_requests`, `human_match_assignments` (at most one active assignment per request), `human_match_exclusions` (unique request/candidate), and `capacity_reservations` (unique active assignment, nonnegative capacity enforced through guarded repository update) |
| Diagnostics | `diagnostic_records` (correlation id, sanitized summary, severity, JSONB safe context) and `admin_notification_deliveries` (unique diagnostic/admin recipient) |

The partial active-attendance index applies only while `ended_at IS NULL` and prevents one user from holding overlapping active services. The repository additionally locks the user row before an attendance mutation, so interval checking and the partial index execute serially. Capacity reservation uses `UPDATE operational_profiles SET reserved_capacity = reserved_capacity + 1 WHERE id = :id AND reserved_capacity < capacity RETURNING id`; release decrements only an existing active reservation. Processed update, timestamp claim, and outbound delivery inserts use PostgreSQL `ON CONFLICT DO NOTHING` and report whether this caller obtained work.

The initial revision uses the following columns; nullable fields are marked `?`, and all identifiers except Telegram update IDs are UUIDs. This is the complete F01-owned schema contract, not a future table wishlist.

| Table | Columns |
| --- | --- |
| `users` | `id`, `telegram_user_id?`, `display_name?`, `role`, `is_admin`, `created_at`, `updated_at` |
| `operational_profiles` | `id`, `user_id`, `normalized_name`, `dob`, `interests` JSONB, `cg_name?`, `telegram_contact_url?`, `always_available`, `capacity`, `reserved_capacity`, `created_at`, `updated_at` |
| `operational_logins` | `id`, `operational_profile_id`, `user_id`, `attached_at`, `detached_at?` |
| `services` | `id`, `key`, `name`, `timezone`, `highkey`, `doors_open_at`, `doors_close_at`, `service_starts_at`, `service_ends_at`, `interaction_ends_at`, `created_at`, `updated_at` |
| `flow_versions` | `id`, `scope_kind`, `service_id?`, `root_flow_key`, `definition`, `content_hash`, `published_at`, `published_by_user_id?` |
| `service_timestamps` | `id`, `service_id`, `key`, `occurs_at`, `audience`, `flow_version_id`, `root_flow_key`, `created_at` |
| `service_attendances` | `id`, `service_id`, `user_id`, `attendee_kind`, `started_at`, `ended_at?`, `created_at`, `updated_at` |
| `open_flow_selections` | `id`, `user_id`, `flow_version_id`, `parent_flow_key`, `service_id?`, `is_current`, `is_global_interruptive`, `ancestor_flow_keys` text array, `checkpoint_flow_keys` text array, `opened_at`, `last_focused_at`, `expires_at?` |
| `conversation_messages` | `id`, `user_id`, `source_kind`, `source_message_id?`, `body`, `replied_to_body?`, `occurred_at`, `created_at` |
| `persona_cursors` | `id`, `user_id`, `persona`, `last_message_id?`, `generated_at?`, `updated_at` |
| `telegram_poll_state` | `singleton_id` fixed to `1`, `next_update_offset`, `updated_at` |
| `processed_telegram_updates` | `telegram_update_id`, `received_at`, `processed_at?`, `correlation_id` |
| `user_processing_locks` | `user_id`, `locked_at` |
| `timestamp_delivery_claims` | `id`, `service_timestamp_id`, `user_id`, `claimed_at`, `completed_at?`, `status` |
| `outbound_deliveries` | `id`, `idempotency_key`, `user_id`, `telegram_chat_id`, `kind`, `payload` JSONB, `status`, `created_at`, `sent_at?` |
| `outbound_delivery_attempts` | `id`, `delivery_id`, `attempt_number`, `started_at`, `finished_at?`, `outcome`, `safe_error?` |
| `human_match_requests` | `id`, `requester_user_id`, `service_id?`, `kind`, `interest?`, `meeting_preference?`, `status`, `created_at`, `resolved_at?` |
| `human_match_assignments` | `id`, `request_id`, `responder_profile_id`, `capacity_reservation_id`, `assigned_at`, `released_at?`, `release_reason?` |
| `human_match_exclusions` | `id`, `request_id`, `responder_profile_id`, `created_at` |
| `capacity_reservations` | `id`, `operational_profile_id`, `request_id`, `reserved_at`, `released_at?` |
| `diagnostic_records` | `id`, `correlation_id`, `severity`, `safe_summary`, `safe_context` JSONB, `created_at` |
| `admin_notification_deliveries` | `id`, `diagnostic_id`, `admin_user_id`, `status`, `created_at`, `sent_at?` |

## 7. Async repositories and unit of work

`persistence.uow.UnitOfWork` owns an `AsyncSession`, exposes typed repositories, and commits only on an exception-free `async with` block; otherwise it rolls back. It provides `lock_user(user_id: UUID) -> None`, which materializes/locks the user lock row before a user-scoped state transition. Repository methods return domain DTOs, never SQLAlchemy ORM objects across package boundaries. `UserRecord.role` is the sole role value; every matching/audience repository query joins `operational_profiles.user_id` to `users.id` rather than reading a duplicate profile role.

The public DTOs are frozen dataclasses: `UserRecord(id, telegram_user_id, display_name, role, is_admin)`, `OperationalProfileRecord(id, user_id, normalized_name, interests, cg_name, telegram_contact_url, always_available, capacity, reserved_capacity)`, `OperationalLoginRecord(id, operational_profile_id, user_id, attached_at, detached_at)`, `ServiceRecord(id, key, highkey, doors_open_at, doors_close_at, interaction_ends_at)`, `AttendanceRecord(id, service_id, user_id, attendee_kind, started_at, ended_at)`, `ServiceTimestampRecord(id, service_id, key, occurs_at, audience, flow_version_id, root_flow_key)`, `ConversationMessageRecord(id, user_id, source_kind, source_message_id, body, replied_to_body, occurred_at)`, `PersonaCursorRecord(user_id, persona, last_message_id, generated_at)`, `MatchCandidateRecord(profile_id, user_id, role, interests, cg_name, always_available, capacity, reserved_capacity)`, `MatchAssignmentRecord(id, request_id, responder_profile_id, assigned_at)`, `PollStateRecord(next_update_offset)`, `OutboundDeliveryRecord(id, idempotency_key, status)`, `DeliveryAttemptRecord(id, delivery_id, attempt_number, started_at)`, and `DiagnosticRecord(id, correlation_id, severity, safe_summary)`. `LoginAttachmentResult` is exactly `attached`, `occupied`, or `not_found`; `AttendanceStartResult` returns the active attendance plus whether a prior active attendance was ended.

```python
class FlowVersionRepository(Protocol):
    async def publish(self, definition: PublishedFlowDefinition, *, scope_kind: FlowScopeKind,
                      service_id: UUID | None, published_by_user_id: UUID | None) -> FlowVersionRecord: ...
    async def get(self, flow_version_id: UUID) -> FlowVersionRecord: ...

class OpenSelectionRepository(Protocol):
    async def list_for_user(self, user_id: UUID, *, now: datetime) -> list[OpenSelectionState]: ...
    async def apply(self, transition: SelectionTransition | CheckpointReturnTransition) -> None: ...
    async def expire_service_bound(self, service_id: UUID, *, at: datetime) -> int: ...

class UpdateRepository(Protocol):
    async def claim_update(self, telegram_update_id: int, *, received_at: datetime) -> bool: ...

class DeliveryRepository(Protocol):
    async def claim_timestamp_delivery(self, service_timestamp_id: UUID, user_id: UUID) -> bool: ...
    async def enqueue(self, delivery: NewOutboundDelivery) -> OutboundDeliveryRecord: ...
```

The remaining public protocols are concrete because T02/R03/I04 consume them directly:

```python
class UserRepository(Protocol):
    async def resolve_telegram_sender(self, telegram_user_id: int, *, received_at: datetime) -> UserRecord: ...
    async def require_by_telegram_id(self, telegram_user_id: int) -> UserRecord: ...
    async def set_display_name(self, user_id: UUID, display_name: str, *, at: datetime) -> UserRecord: ...

class OperationalProfileRepository(Protocol):
    async def find_by_login_identity(self, normalized_name: str, dob: date) -> OperationalProfileRecord | None: ...
    async def update_interests(self, profile_id: UUID, interests: list[str], *, at: datetime) -> OperationalProfileRecord: ...

class OperationalLoginRepository(Protocol):
    async def attach(self, profile_id: UUID, user_id: UUID, *, at: datetime) -> LoginAttachmentResult: ...
    async def detach_for_user(self, user_id: UUID, *, at: datetime) -> OperationalLoginRecord | None: ...

class ServiceRepository(Protocol):
    async def get(self, service_id: UUID) -> ServiceRecord: ...
    async def list_ongoing(self, *, now: datetime) -> list[ServiceRecord]: ...
    async def list_due_timestamps(self, *, now: datetime) -> list[ServiceTimestampRecord]: ...
    async def list_audience_user_ids(self, audience: ServiceAudience, service_id: UUID | None, *, now: datetime) -> list[UUID]: ...

class AttendanceRepository(Protocol):
    async def start_or_switch(self, user_id: UUID, service_id: UUID, *, attendee_kind: str, started_at: datetime) -> AttendanceStartResult: ...
    async def active_for_user(self, user_id: UUID, *, now: datetime) -> AttendanceRecord | None: ...
    async def end_active_for_service(self, service_id: UUID, *, ended_at: datetime) -> int: ...

class PollStateRepository(Protocol):
    async def get(self) -> PollStateRecord: ...
    async def advance_monotonically(self, next_update_offset: int, *, at: datetime) -> PollStateRecord: ...

class ConversationRepository(Protocol):
    async def record_incoming(self, *, user_id: UUID, source_message_id: int, body: str, replied_to_body: str | None, occurred_at: datetime) -> ConversationMessageRecord: ...
    async def list_after(self, user_id: UUID, message_id: UUID | None) -> list[ConversationMessageRecord]: ...

class PersonaRepository(Protocol):
    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord: ...
    async def advance(self, user_id: UUID, *, persona: str, last_message_id: UUID, generated_at: datetime) -> None: ...

class MatchRepository(Protocol):
    async def list_eligible_normal(self, service_id: UUID, request_id: UUID) -> list[MatchCandidateRecord]: ...
    async def list_eligible_safety(self, service_id: UUID | None, request_id: UUID) -> list[MatchCandidateRecord]: ...
    async def reserve_ranked(self, request_id: UUID, ranked_profile_ids: list[UUID], *, now: datetime) -> MatchAssignmentRecord | None: ...
    async def release_and_exclude(self, request_id: UUID, profile_id: UUID, *, reason: str, now: datetime) -> None: ...

class DiagnosticRepository(Protocol):
    async def record(self, *, correlation_id: UUID, severity: str, safe_summary: str, safe_context: dict[str, JsonValue], at: datetime) -> DiagnosticRecord: ...
    async def enqueue_admin_notifications(self, diagnostic_id: UUID, *, at: datetime) -> int: ...
```

`DeliveryRepository` additionally provides `claim_next_safe(now) -> OutboundDeliveryRecord | None`, `start_attempt(delivery_id, correlation_id, started_at) -> DeliveryAttemptRecord`, and `finish_attempt(delivery_id, attempt_id, outcome, now) -> None`. F01 defines these interfaces and persistence behavior; T02/R03/I04 consume them rather than creating side tables or independent sessions.

## 8. Migrations, verification, and downstream contract

Alembic `env.py` takes `DATABASE_URL` from `DatabaseSettings`, imports only F01 metadata, and runs migrations synchronously through `async_engine_from_config(...).connect().run_sync`. The first revision creates PostgreSQL extensions, named enums, tables, indexes, and constraints in dependency order; downgrade drops those exact owned objects in reverse order. `alembic upgrade head` against a freshly created `friendly_bot` database is mandatory evidence. The Compose readiness sequence is: validate namespace/port, create ignored env file locally, `docker compose -p friendly-bot-u504 up -d postgres`, wait for `pg_isready` at loopback port 5832, run migration, run async connection canary, and use only `docker compose -p friendly-bot-u504 down` for teardown.

F01's execution acceptance is an independently executable suite: recursive parsing and publication failures; all fixture action discriminators; direct child outcome completeness; mode/reuse transitions; nested leaf/never-mind returns; Alembic upgrade; repository rollback; open-selection persistence; concurrent update/delivery/idempotency claims; attendance overlap; and capacity guard. `ruff check .`, `ruff format --check .`, and `mypy src/friendly_bot` run after focused pytest groups. The product masterplan already covers all approved F01 behavior; this dated implementation design adds no product decision, so no living-authority edit is required.
