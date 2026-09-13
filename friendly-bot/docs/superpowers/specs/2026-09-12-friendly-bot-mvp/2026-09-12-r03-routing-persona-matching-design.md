# R03 Constrained Routing, Persona, and Matching Design

| Field | Value |
| --- | --- |
| Status | Implementation complete; operational privacy blocked pending account-level OpenRouter Observability proof; G2 not passed |
| Date | 2026-09-12 |
| Owner | R03 |
| Authority inputs | Product specification, architecture (`ARCH-004`, `ARCH-006`, `ARCH-008`, `ARCH-012`), approved MVP design, Zone X fixture, frozen blueprint, and corrected F01 planning commit `a920d2d` |
| Scope | OpenRouter privacy gateway, constrained routing, durable persona maintenance, and normal/safety match policy |
| Excluded ownership | Flow schemas/migrations/repositories (F01), Telegram/service delivery (T02), action registry/runtime composition/Zone X seed (I04), and living authorities |

## 1. Settled behavior and authority impact

The router returns only configured flow keys or reserved harness keys; it never generates Telegram prose. It considers current, reusable-past, system-global, service-global, and currently valid service candidates together. Native reply body is a strong prompt hint, never a message-ID association or hard scope. One update may execute several distinct keys: after each valid selected key, remove it from the candidate set and call the model again until `system.done`.

`system.clarify_ambiguous_context` makes the harness send “Sorry, which message were you referring to?”; `system.no_match` runs a configured fallback when supplied or the fixed default “Sorry, I didn't understand your request.” Invalid output, a privacy-policy failure, and transport exhaustion are errors for the application harness, not improvised model answers.

The existing product specification already owns every R03 user-visible outcome, privacy rule, and matching rule. This dated design adds only implementation boundaries and testable contracts, so it has no product-specification impact and must not modify a living authority.

## 2. F01 reconciliation and required consumed contract

F01 has published the shared `friendly_bot.domain` models (`DiscussionFlow`, `MessageDiscussionFlowTrigger`, `OpenSelectionState`) and `persistence.uow.UnitOfWork`, with `ConversationRepository`, `PersonaRepository`, `OperationalProfileRepository`, `AttendanceRepository`, `MatchRepository`, and `OpenSelectionRepository`. R03 consumes those records under one F01 transaction and creates neither a migration, an ORM model, a session, nor a side table. F01 reconciliation commit `9b5b4e5` makes `users.role` the sole persisted role source and requires matching queries to join `operational_profiles.user_id` to `users.id`.

R03 requires these typed repository operations from F01's stated “direct methods for externally required operations”; their execution implementation is one exact F01-owned interface, not an adapter or fallback:

```python
class ConversationRepository(Protocol):
    async def list_after(self, user_id: UUID, message_id: UUID | None) -> list[ConversationMessageRecord]: ...
class PersonaRepository(Protocol):
    async def get_or_create(self, user_id: UUID) -> PersonaCursorRecord: ...
    async def advance(self, user_id: UUID, *, persona: str, last_message_id: UUID, generated_at: datetime) -> None: ...
class MatchRepository(Protocol):
    async def list_eligible_normal(self, service_id: UUID, request_id: UUID) -> list[MatchCandidateRecord]: ...
    async def list_eligible_safety(self, service_id: UUID | None, request_id: UUID) -> list[MatchCandidateRecord]: ...
    async def reserve_ranked(self, request_id: UUID, ranked_profile_ids: list[UUID], *, now: datetime) -> MatchAssignmentRecord | None: ...
    async def release_and_exclude(self, request_id: UUID, profile_id: UUID, *, reason: str, now: datetime) -> None: ...
```

R03 consumes these exact F01 protocols and frozen DTOs after G1 supplies their implemented evidence. R03 does not translate them through an adapter, access SQLAlchemy internals, or create a duplicate persistence path.

## 3. Gateway and privacy boundary

`hyperparameters.py` owns non-secret constants: `OPENROUTER_MODEL = "qwen/qwen3.7-flash"`, `OPENROUTER_TIMEOUT_SECONDS`, `ROUTING_MAX_ATTEMPTS`, `PERSONA_IDLE_AFTER = timedelta(hours=48)`, and `PERSONA_MAX_UNSUMMARIZED_TOKENS`. `OpenRouterSettings` reads `OPENROUTER_API_KEY` plus R03's non-secret `FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION` from the environment. That setting defaults to `false` (unattested) and only accepts `disabled-globally-or-friendly-bot-key-excluded` to enable a gateway. It is an operator attestation that the account has disabled OpenRouter Input & Output Logging globally or explicitly excluded the dedicated Friendly Bot key; it is not proof of either account state and is deliberately usable by I04 through `OpenRouterGateway.from_environment()` without exposing a Telegram ID, DOB, or credential.

`OpenRouterGateway` refuses construction without that exact attestation. It posts to `https://openrouter.ai/api/v1/chat/completions` with the configured model and `provider={"zdr": true, "data_collection": "deny"}`. It never opts into `logprobs`, debug, trace, or metadata logging fields: `logprobs` controls returned token probabilities, not OpenRouter prompt storage, so it is never described or used as a logging control. A response is accepted only when its entire parsed assistant content is a single JSON object `{"key":"<one allowed key>"}`. Any unavailable compliant endpoint, non-2xx response after bounded retry, malformed object, unknown key, or multiple key is a closed failure with no raw request or response attached.

Every prompt DTO is a Pydantic model with `model_config = ConfigDict(extra="forbid")`. The DTOs admit user name, persona, user-authored message/reply bodies, unsummarized messages, candidate keys/gists/context labels, and match candidate aliases with interests and `cg_name`. They reject structured-identifier aliases including `telegramUserId`, `telegram_chat_id`, and `dob`, as well as source message IDs, contact URL, UUID, raw database records, and diagnostic payloads. Gateway tests exercise each DTO, including nested `RoutingPromptCandidate` and `MatchPromptCandidate`, and the actual selection, persona, and matching transports. They prove sentinels are rejected and absent from serialized payloads while ordinary authored words such as “telegram” and “dob” remain allowed free text.

## 4. Routing contract

`CandidateAssembler.assemble(selections: Sequence[OpenSelectionState], definitions: Mapping[UUID, PublishedFlowDefinition], *, now: datetime) -> list[RoutingCandidate]` reconstructs candidates from F01 open selections and immutable flow definitions. It emits `RoutingCandidate(key, gist, source, is_current, service_id)` only for `MessageDiscussionFlowTrigger` descendants; buttons, commands, automatic triggers, action events, and expired service selections are omitted. Duplicate keys are a publication/runtime invariant failure, never silently preferred. The allowed set also includes `system.done`, `system.no_match`, and `system.clarify_ambiguous_context`.

Routing result types are explicit and distinct from F01 flow configuration:

```python
@dataclass(frozen=True)
class RoutingDecision:
    key: str

@dataclass(frozen=True)
class RoutingResult:
    selected_keys: tuple[RoutingDecision, ...]
    terminal: RoutingTerminal

async def ConstrainedRouter.route_update(
    self, user_id: UUID, incoming: IncomingText, now: datetime,
) -> RoutingResult: ...
```

`ConstrainedRouter.route_update` sends the user persona plus the unsummarized segment and native reply text, validates each model key against the shrinking allowed set, and returns ordered `RoutingDecision` items in `RoutingResult.selected_keys` plus exactly one terminal `RoutingTerminal`. `DiscussionFlow.next_flow_mode` remains the only configuration that controls flow selection/reuse. The routing result never treats current as exclusive, never maps Telegram message IDs to selections, and cannot return one key twice in one update.

## 5. Persona contract

`PersonaMaintenanceService` gets the durable cursor, fetches messages strictly after `last_message_id`, and regenerates only if the segment is nonempty and either 48 hours have elapsed since the last message or model-relative token count reaches the configured threshold. The summary request contains persona plus source message bodies and user name; it excludes structured Telegram IDs and DOB. It advances `persona_cursors.last_message_id` only after a valid nonempty summary is returned; source messages remain untouched. Retry/failure leaves the old persona and cursor intact.

## 6. Matching contract

Normal matching uses F01's query that joins each operational profile to its user and reads the exact role from `users.role`. It admits only `server` users with active attendance at the request service, positive free capacity, and no active exclusion for the request. `leader` and `staff` users are excluded even though they inherit server capabilities for service audiences. The rank prompt receives local aliases (for example `candidate-0`), interests, and `cg_name`; only the service maps aliases back to profile UUIDs. It atomically reserves the first rankable candidate through F01's guarded capacity update before the meeting preference is shown. No qualifying assignment produces the ordinary `human_match.not_found` action event.

Safety matching uses F01's joined query and admits only users whose `users.role` is exactly `leader` or `staff`. A responder qualifies when their operational profile has `always_available=True` or their user attends the current service; ordinary `server` users are never included. No candidate produces `safety_match.not_found`, allowing I04's action executor to send configured urgent-support copy, notify admins, and retain pending state. It never falls back to normal matching.

For rematch, `release_and_exclude` releases the active reservation, writes the request/profile exclusion, and notifies the previous responder in the same user-serialized transaction before ranking again. `reserve_ranked` locks/guards capacity so concurrent requests yield at most the available number of reservations. Capacity remains reserved until rematch, service interaction end, or admin intervention.

## 7. Execution acceptance

Focused R03 evidence is: gateway payload/privacy and fail-closed tests, including transport and non-2xx retry exhaustion; current/global/reusable/native-reply/multi-key/ambiguity/no-match routing tests; idle/token/cursor persona tests; normal role/attendance/capacity/exclusion/rematch tests; safety leader/staff/always-available/no-fallback tests; and concurrent reservation integration tests. `ruff check .`, `ruff format --check .`, and `mypy src/friendly_bot` run after R03 focused suites. User-facing and product scope are unchanged. R03 implementation cannot become operational until an account operator supplies a redacted receipt showing OpenRouter Input & Output Logging is disabled globally or the dedicated Friendly Bot API key is excluded; the local environment attestation is not that receipt and G2 remains not passed.
