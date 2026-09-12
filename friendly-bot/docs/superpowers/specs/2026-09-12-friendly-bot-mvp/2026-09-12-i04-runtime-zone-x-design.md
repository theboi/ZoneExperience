# I04 Runtime Composition and Zone X Acceptance Design

| Field | Value |
| --- | --- |
| Status | Planning complete; execution blocked pending `PG`, `G1`, and `G2` |
| Date | 2026-09-12 |
| Owner | I04 |
| Authority inputs | Product specification, architecture masterplan (`ARCH-001` through `ARCH-013`), approved MVP design, canonical Zone X service document, frozen implementation masterplan, reconciled F01 contract at `9bc9e84c6f553d10c1173c786bcb48b4a8513e86`, reconciled T02 contract at `23350575bfaff92de934d2b47cdb89c498c0ad0d`, and reconciled R03 contract at `db386457aaa72a413ba1cd1afe69ef6e6b2a2ce3` on `origin/main` |
| Scope | Action-executor composition, action-event harness, application lifecycle and diagnostics, `python -m friendly_bot`, Zone X development seed, and cross-package end-to-end acceptance |
| Excluded ownership | Flow schema/validation/state/persistence/migrations/runtime database configuration (F01); Telegram wire/polling/outbox/onboarding/service scheduling policy (T02); OpenRouter/persona/match eligibility/ranking policy (R03); living authorities and coordinator gate documents |

## 1. Purpose and execution boundary

I04 is the one composition layer that turns the three implemented upstream packages into a locally runnable Friendly Bot. It owns no parallel transport, scheduler, router, repository, matching policy, migration, or flow-schema path. It translates a validated F01 `DiscussionAction` into one registered executor, supplies transaction-scoped dependencies, dispatches its one terminal `ActionEvent` only to a matching direct child, and starts/stops the T02 runtime components in one process.

Execution starts only after all of the following are fresh, remote-reachable evidence on `origin/main`: coordinator-passed `PG`; F01 `G1`; and T02/R03 `G2`. At that point the executor re-reads the actual exported types rather than treating these Pass 1 documents as implemented code. The final I04 run is the G3 evidence producer; it does not declare G3 passed.

The implementation creates no compatibility adapter, duplicate action path, legacy configuration format, broad fallback, hosted runtime, webhook, admin UI, bot-relayed chat, or free-form user-facing model response. The local account runtime is exactly macOS account `bot2` UID `504`, Compose project/network/volume `friendly-bot-u504`, loopback PostgreSQL `127.0.0.1:5832`, and generated ignored state only under `.runtime/u504/`.

## 2. Concrete I04 file boundary

| Path | Responsibility |
| --- | --- |
| `friendly-bot/src/friendly_bot/actions/context.py` | Per-dispatch action context, local template values, durable delivery enqueue port, and single terminal-event guard. |
| `friendly-bot/src/friendly_bot/actions/registry.py` | Closed type-to-executor registry and missing/duplicate registration failures. |
| `friendly-bot/src/friendly_bot/actions/executors.py` | Registered executors for every F01 action discriminator; delegates to T02/R03/F01 interfaces. |
| `friendly-bot/src/friendly_bot/actions/runner.py` | Ordered action execution, retry classification, terminal event direct-child dispatch, and code-owned unhandled-error delivery. |
| `friendly-bot/src/friendly_bot/actions/__init__.py` | Public I04 action-composition exports only. |
| `friendly-bot/src/friendly_bot/app.py` | Application dispatcher, routing-terminal policy, publication/seed loading, runtime assembly, diagnostics fan-out, and orderly lifecycle. |
| `friendly-bot/src/friendly_bot/__main__.py` | Thin `python -m friendly_bot` async entry point with signal-safe shutdown and exit status. |
| `friendly-bot/src/friendly_bot/__init__.py` | Root package version/export update necessary for module execution; it does not become a service locator. |
| `friendly-bot/seeds/zone-x.json` | Real development service configuration, semantically identical to the canonical YAML document. |
| `friendly-bot/docs/examples/friendly-bot-local-runtime.md` | Exact local prerequisites, account-namespace validation, redacted start/stop/migration commands, and no-production-approval warning. |
| `friendly-bot/tests/e2e/conftest.py` | Fakes for only external Telegram/OpenRouter boundaries plus deterministic clock and seeded operational records. |
| `friendly-bot/tests/e2e/test_action_runner.py` | Registry completeness, ordered execution, terminal-event and error-path tests. |
| `friendly-bot/tests/e2e/test_application_dispatch.py` | Button/direct-child, router terminal, candidate/reuse/checkpoint, and diagnostics orchestration tests. |
| `friendly-bot/tests/e2e/test_zone_x_seed.py` | Canonical YAML/JSON semantic-equivalence and publication tests. |
| `friendly-bot/tests/e2e/test_zone_x_journey.py` | Onboarding-to-Zone-X, timestamps, matching, safety, expiry, restart, and privacy acceptance matrix. |
| `friendly-bot/tests/e2e/verify_zone_x_seed.rb` | Standard-library Ruby/Psych verifier that extracts the canonical fenced YAML and compares its parsed tree with `zone-x.json`; it is a test verifier, not runtime code. |

## 3. Consumed contracts and reconciliation rules

I04 consumes the following planned exports; execution is blocked for a focused upstream repair if G1/G2 implements incompatible names, values, or transactional semantics. I04 never reaches through an upstream package to ORM models, raw HTTP, or a second session.

| Producer | I04 consumption |
| --- | --- |
| F01 `friendly_bot.domain` | `DiscussionFlow`, every registered `DiscussionAction` model, `ActionEvent`, `ActionEventDiscussionFlowTrigger`, `NextFlowMode`, `OpenSelectionState`, `SelectionTransitionEngine`, `validate_for_publication`, `PublishedFlowDefinition`, and publication context/root kinds. |
| F01 `persistence.uow.UnitOfWork` | The transaction supplied by T02 ingress plus user lock, flow version/open-selection mutations, conversation, service, attendance, outbound-delivery, diagnostic, profile, and match repository DTO operations. No I04-owned `AsyncSession` or table exists. |
| T02 | `TelegramGateway`, `TelegramPoller`, `OutboundDeliveryWorker`, `OnboardingService`, `OperationalAccountService`, `ServiceAttendanceService`, `ServiceDeliveryScheduler`, `ServiceLifecycleService`, Telegram inbound/outbound DTOs, preflight, and runtime lock. The I04 `ApplicationDispatcher.dispatch` satisfies the documented poller dispatch seam: `async dispatch(*, user_id: UUID, incoming: IncomingTelegramMessage, unit_of_work: UnitOfWork, now: datetime) -> DispatchResult`. |
| R03 | `ConstrainedRouter`, `CandidateAssembler`, `OpenRouterGateway`, `PersonaMaintenanceService`, `MatchingService`, prompt-safe `IncomingText`, `RoutingTerminal`, `RoutingDecision`, and `RoutingResult.selected_keys`. `DiscussionFlow.next_flow_mode` remains the sole configuration/reuse authority. I04 supplies no structured Telegram ID, DOB, contact URL, UUID, raw profile record, event payload, or diagnostic to an R03 prompt DTO. |

The published F01 `9bc9e84c6f553d10c1173c786bcb48b4a8513e86`, T02 `23350575bfaff92de934d2b47cdb89c498c0ad0d`, and R03 `db386457aaa72a413ba1cd1afe69ef6e6b2a2ce3` contracts share the normalized role rule: `users.role` is the one role source, `operational_profiles` has no duplicate role column, and matching joins profile attributes to exact `users.role`. I04 fixture setup and assertions use that same rule and are ready to consume the implemented contracts at G1/G2.

F01 now includes `SendServiceChoiceButtonsAction` for the canonical `send_service_choice_buttons` discriminator. Its `choice_source` is fixed to `resolved_service_options`, its `service_bound` value is fixed to `true`, and the preceding typed attendance/switch action supplies local choice records; the I04 executor renders them without embedding a service list or executable behavior in JSON. R03 now returns ordered `RoutingDecision` values in `RoutingResult.selected_keys`. These resolved contracts are consumed directly; I04 adds no adapter.

## 4. Typed executor registry and action context

The registry is closed and keyed by the concrete F01 action class, not a user-controlled string or a callable stored in JSON:

```python
type ActionExecutor[A: DiscussionAction] = Callable[[A, ActionContext], Awaitable[None]]

@dataclass(frozen=True, slots=True)
class ActionDependencies:
    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticPort

class ActionExecutorRegistry:
    def register(self, action_type: type[A], executor: ActionExecutor[A]) -> None:
        raise NotImplementedError
    def resolve(self, action: DiscussionAction) -> ActionExecutor[DiscussionAction]:
        raise NotImplementedError
    def assert_complete(self, action_types: frozenset[type[DiscussionAction]]) -> None:
        raise NotImplementedError
```

`register` rejects a second executor for the same concrete action class. `resolve` raises `UnregisteredActionExecutorError(type(action).__name__)`; it never falls back to a generic handler. `assert_complete` compares the exact F01 registered action-class set to the registry before seed publication and process startup. This makes an unimplemented configuration type a startup failure rather than a partially executed conversation.

```python
@dataclass(slots=True)
class ActionContext:
    user: UserRecord
    incoming: IncomingTelegramMessage | None
    flow: DiscussionFlow
    flow_version: FlowVersionRecord
    branch: OpenSelectionState
    unit_of_work: UnitOfWork
    now: datetime
    correlation_id: UUID
    local_values: Mapping[str, JsonValue]
    telegram: TelegramGateway
    services: ServiceAttendanceService
    lifecycle: ServiceLifecycleService
    matching: MatchingService
    diagnostics: DiagnosticPort
    _terminal_event: ActionEvent | None = None

    def render(self, template: str) -> str:
        raise NotImplementedError
    def emit(self, event: ActionEvent) -> None:
        raise NotImplementedError
    async def enqueue_text(self, text: str) -> None:
        raise NotImplementedError
    def match_request_id(self) -> UUID:
        raise NotImplementedError
    def render_uuid(self, template: str) -> UUID:
        raise NotImplementedError
    def selected_service_id(self) -> UUID:
        raise NotImplementedError
    def set_match_assignment(self, assignment: MatchAssignmentRecord | None) -> None:
        raise NotImplementedError
    @property
    def terminal_event(self) -> ActionEvent | None:
        raise NotImplementedError
```

`render` permits only the validated template names from F01's published context schema and renders locally. `emit` permits exactly one event, rejects `error` from a normal executor, and prevents later actions. Its payload is held only in `local_values` for the matching direct child; it is never appended to R03 input. The context queues all Telegram messages/activity/photos/buttons as T02 `NewOutboundDelivery` records under the provided UoW. It does not cross the Telegram network boundary.

The complete initial registry is: `send_message`, `send_buttons`, `send_service_choice_buttons`, `send_photo`, `show_activity`, `save_incoming`, `add_service_attendance`, `select_service_attendance`, `enter_service_checkpoint`, `enter_selected_service_checkpoint`, `enter_selected_service_latecomer_flow`, `resolve_service_switch_options`, `find_and_reserve_server`, `find_and_reserve_safety_responder`, `confirm_human_match`, `release_human_match`, `notify_matched_human`, `notify_previous_human`, `notify_all_admins`, `exclude_previous_human_from_next_attempt`, `share_human_contact`, `mark_safety_request_pending`, `end_service_interactions`, and `return_to_nearest_checkpoint`.

The message/button/photo/activity executors render fixed validated configuration and enqueue T02 deliveries. Service executors call T02's exact attendance/lifecycle APIs. Matching executors call only `MatchingService.reserve_normal`, `reserve_safety`, and rematch/release operations, then emit the declared result key. Notification/contact executors use persisted match DTOs, validated contact URLs, and fixed local templates. `notify_all_admins` creates one sanitized diagnostic/notification delivery per admin. The retry boundary classifies only safe, idempotent local work as retryable; after its finite safe retry budget, the runner emits `ActionEvent(key="error")` and retains the failed selection for retry.

## 5. Terminal action-event dispatch and unhandled errors

`ActionRunner.run(flow, context)` executes actions in declared order. A normal action continues; an emitted event stops the list. The runner searches **only** `flow.next_flows` for an `ActionEventDiscussionFlowTrigger` with exactly the emitted key. It executes that child with the same UoW/correlation id and a context carrying the local event payload. It does not consult sibling selections, reusable-past selections, root flows, parent/ancestor flows, the router, or the LLM. It does not continue parent actions after a child event path starts. F01 publication already guarantees each declared non-error outcome has exactly one direct child; the runner preserves that invariant at runtime.

Unexpected action failure after safe retry exhaustion creates a sanitized diagnostic and terminal `error`. A direct `error` child executes exactly like another direct event child. If and only if no direct `error` child exists, the runner invokes this non-configurable application function:

```python
DEFAULT_UNHANDLED_ERROR_TEXT = "Sorry, an error occurred. Error log: {telegram_user_id}."

async def send_unhandled_action_error(context: ActionContext) -> None:
    await context.enqueue_text(
        DEFAULT_UNHANDLED_ERROR_TEXT.format(telegram_user_id=context.user.telegram_user_id)
    )
```

The rendered ID remains a local Telegram/outbox value; it is absent from `KeySelectionRequest`, persona requests, match-ranking prompts, diagnostics safe context, and logs. The function is not a `DiscussionFlow`, action discriminator, registry member, `next_flow`, flow-version JSON object, or a system/service/timestamp root. No configuration can change its text or redirect it. Error events never bubble. The runner must prove both that a direct error child suppresses this sender and that an ancestor error child cannot suppress it.

## 6. Application dispatcher, routing terminals, state, and diagnostics

`FriendlyBotApplication` is the T02 poller's dispatcher. For an incoming private message already claimed, normalized, persisted, and user-locked by T02, it opens no additional database transaction. It uses this order:

1. Let T02 onboarding/account application services establish or update identity state when their configured path is active.
2. Resolve exact configured command and button direct children without model routing. A service-bound button is rechecked through T02 lifecycle/attendance at execution time, so a post-boundary Zone X button queues exactly `Sorry, the service is over!`.
3. For ordinary text, ask R03's `ConstrainedRouter` with every valid current, reusable-past, system-global, and service-global candidate. The selected candidate keys are applied at most once each through F01's `SelectionTransitionEngine`; current remains a preference, not exclusion, and native reply text is context only.
4. Execute actions and event children under the same UoW. Apply a transition only after its actions/events succeed; return a leaf or never-mind request by F01's nearest branch checkpoint rule. Timestamp roots remain independent current branches and do not close onboarding or another checkpoint branch.
5. Schedule persona maintenance through R03's cursor-safe service after relevant persisted conversation work. Failed maintenance does not erase history or block a safe completed user transition.

`RuntimeRoutingPolicy` is `RuntimeRoutingPolicy(no_match_text: str | None)`: it accepts an approved fixed configured no-match text or `None`. `RoutingTerminal.DONE` applies the ordered selected keys and sends nothing itself. `RoutingTerminal.CLARIFY` enqueues exactly `Sorry, which message were you referring to?`; it does not choose an arbitrary candidate. `RoutingTerminal.NO_MATCH` enqueues `no_match_text` when supplied, otherwise exactly `Sorry, I didn't understand your request.`. Both terminal messages are local fixed application behavior, never router prose. No-match configuration is not the error fallback and cannot alter `DEFAULT_UNHANDLED_ERROR_TEXT`.

Every caught application error or debug diagnostic creates a sanitized F01 diagnostic record with correlation ID and requests durable notification fan-out to every admin. Sanitization permits stable operation names/reason codes and the locally needed recipient ID for outbound notification routing, but never source text, raw Telegram payload, token, endpoint, DOB, OpenRouter request body, contact URL, or other person's Telegram ID. Any action failure records a diagnostic before its direct/error/default recovery. All user-facing sends remain durable T02 outbox entries.

## 7. Composition and local process lifecycle

`build_application(settings)` creates exactly one F01 UoW factory, T02 Telegram client/preflight/runtime lock/poller/outbox/scheduler services, R03 gateway/router/persona/matching services, `ActionExecutorRegistry`, seed publisher, and `FriendlyBotApplication`; dependencies are passed explicitly. It validates the registered action set and loads/publishes Zone X idempotently before acquisition of the polling lock. It does not instantiate a second client, scheduler, session, outbox, or poller.

`run_application()` preflights and clears Telegram webhook, acquires the T02 PostgreSQL runtime lock, starts the long-poll loop, outbox loop, and scheduler cadence under one `asyncio.TaskGroup`, and closes the group on `SIGINT`/`SIGTERM`. A lost runtime-lock connection is fatal: it stops future polling, scheduling, and sends, drains only already committed work safely, closes clients/UoW resources, and exits nonzero. A restart resumes the durable offset, catches up legal timestamp deliveries once, and leaves uncertain sends held.

`__main__.py` calls `asyncio.run(run_application())`. It prints only a redacted local startup summary (project, loopback host/port, and no secrets); configuration/preflight/seed/runtime-lock failure exits nonzero before polling. I04 Pass 1 starts none of these processes.

## 8. Zone X development seed

`friendly-bot/seeds/zone-x.json` is production-shaped development data, not an illustrative fixture. It is the JSON serialization of the entire canonical fenced YAML mapping in `docs/examples/zone-x-service-example.md`: `system_global_root_excerpt` and the `service` object. It preserves every key, scalar, boolean, timestamp string including `+08:00`, audience enum, action discriminator/parameter, event key, trigger, template, button ID/payload, ordered action list, ordered child list, next-flow mode, return action, highkey flag, and all seven timestamp roots. JSON adds no comment field, default error root, executor name, placeholder, altered copy, implicit venue/contact approval, or test-only branch.

Seed publication validates the system excerpt, Zone X service root, latecomer root, and each timestamp root with their correct F01 root kind/context schema; creates/updates the `services` record by stable key; publishes immutable hash-addressed versions idempotently; and associates each timestamp with its root version. It never rewrites a published definition or active selection. The local documentation says clearly that the venue, copy, names, map/contact URLs, and service schedule are development data requiring separate production approval.

The exact semantic verifier extracts the canonical YAML fence using Ruby's standard `YAML`/Psych parser with aliases disabled, parses the JSON with Ruby's standard `JSON`, compares both trees with equality, and exits nonzero with the first differing JSON Pointer. This means YAML formatting changes are accepted only when semantics stay equal and JSON copy/configuration drift fails before seed publication.

## 9. Acceptance and authority impact

The final I04 suite proves registry completeness, direct child-only terminal dispatch, direct/default error paths, default sender exclusion from every JSON/root/prompt, configured no-match and default ambiguity behavior, candidate/reuse/checkpoint behavior, diagnostics sanitation/admin fan-out, all Zone X timestamps/audiences/highkey/latecomer/expiry/button paths, normal/rematch/safety matching, restart/idempotency, JSON/YAML semantic equality, privacy-negative assertions, and local `python -m friendly_bot` composition with fakes/no live polling.

One final, explicitly authorized G3 runtime canary uses only `bot2`/UID `504`, `friendly-bot-u504`, `.runtime/u504/`, and loopback `5832`: validate namespace, run migration, prove `pg_isready`, publish/load seed, construct the application, start it with Telegram/OpenRouter network fakes or controlled local test mode, prove no duplicate durable work across restart, then tear down only the exact Compose project. It does not send a production Telegram message or seek production approval.

The existing product and architecture masterplans already define every approved I04 behavior. This dated implementation design adds module boundaries, integration contracts, and acceptance evidence only; it has no product-specification impact and does not modify a living authority.
