# I04 Runtime Composition and Zone X Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compose F01, T02, and R03 into one locally runnable Friendly Bot, execute every registered action safely, ship canonical Zone X JSON development data, and produce G3 acceptance evidence.

**Architecture:** I04 supplies a closed concrete-action executor registry and a transaction-scoped `ActionContext`; the action runner stops at its one terminal event and invokes only a matching direct child. `FriendlyBotApplication` satisfies T02's dispatcher seam, delegates policy to the upstream packages, and starts a single durable local runtime through `python -m friendly_bot`. Zone X is loaded from JSON and semantically checked against its canonical YAML source.

**Tech Stack:** Python 3.12, `asyncio`, Pydantic v2 and SQLAlchemy/PostgreSQL contracts from F01, Telegram packages from T02, OpenRouter/routing/persona/matching packages from R03, pytest/pytest-asyncio, Ruff, mypy, Docker Compose, and macOS Ruby standard-library `YAML`/Psych plus `JSON` for the semantic seed verifier.

**Spec:** `friendly-bot/docs/superpowers/specs/2026-09-12-friendly-bot-mvp/2026-09-12-i04-runtime-zone-x-design.md`

## Global Constraints

- Begin execution only after a coordinator-passed `PG`, F01 `G1`, and T02/R03 `G2` execution evidence are all reachable from `origin/main`; inspect the actual G1/G2 exports and manifests first.
- Use F01 `DiscussionFlow`, action unions, validation, transition engine, immutable versions, repositories, and UoW; create no schema, migration, ORM session, direct SQL, compatibility adapter, or fallback persistence path.
- Compose, rather than reimplement, T02 `TelegramGateway`, `TelegramPoller`, `OutboundDeliveryWorker`, onboarding/account, attendance/lifecycle, scheduler, preflight, and runtime-lock services; compose, rather than reimplement, R03 router/persona/matching services.
- Reconcile before Task 1: F01 planning commit `9bc9e84c6f553d10c1173c786bcb48b4a8513e86` makes `users.role` the sole role source, removes `operational_profiles.role`, publishes frozen UoW DTO/repository contracts, and registers `SendServiceChoiceButtonsAction` with fixed `resolved_service_options`/service-bound configuration. R03 planning commit `db386457aaa72a413ba1cd1afe69ef6e6b2a2ce3` exposes ordered `RoutingDecision` values in `RoutingResult.selected_keys`; `DiscussionFlow.next_flow_mode` remains authoritative for configuration/reuse. Consume these published contracts directly without an adapter.
- The default unhandled-error sender is code-owned, has exact text `Sorry, an error occurred. Error log: {telegram_user_id}.`, renders locally, is not configurable, and is absent from JSON and every discussion-flow root.
- Router prompts receive no structured Telegram ID, DOB, contact URL, UUID, raw profile/diagnostic/event data, or free-form output path. User-facing content comes only from fixed action configuration or the fixed local routing/error fallback messages.
- Use no webhooks, hosted process, second poller/scheduler/outbox/session, generic action executor, legacy seed, production Telegram message, or production approval assertion.
- For every task, hold `/tmp/friendly-bot-main-mutation-u504.lock`; run the named focused check, stage only the named owned paths, commit directly to `main`, `git fetch origin`, `git merge --no-edit origin/main`, rerun the named check, push, fetch, and prove `git merge-base --is-ancestor HEAD origin/main` before the next task.
- Runtime mutation commands validate account `bot2` UID `504`, checkout `/Users/bot2/Dev/ZoneExperience`, Compose project/network/volume `friendly-bot-u504`, and loopback PostgreSQL `127.0.0.1:5832` before Docker changes. Generated state stays under ignored `.runtime/u504/`.

## File Structure

| Path | Responsibility |
| --- | --- |
| `src/friendly_bot/actions/context.py` | Local template context and exactly-one terminal event. |
| `src/friendly_bot/actions/registry.py` | Concrete action type registration/completeness. |
| `src/friendly_bot/actions/executors.py` | Fixed-message/media/button, service, match, notification, and checkpoint executor functions. |
| `src/friendly_bot/actions/runner.py` | Ordered execution, direct event child routing, retry/error policy, default error sender. |
| `src/friendly_bot/actions/__init__.py` | Limited public composition API. |
| `src/friendly_bot/app.py` | Dispatcher, routing terminal policy, seed publishing, diagnostics, component lifecycle. |
| `src/friendly_bot/__main__.py`, `src/friendly_bot/__init__.py` | Module entry point and minimal root export. |
| `seeds/zone-x.json` | Whole canonical Zone X document encoded as JSON. |
| `docs/examples/friendly-bot-local-runtime.md` | Exact safe local operation instructions and development-data warning. |
| `tests/e2e/conftest.py` | Deterministic clock plus Telegram/OpenRouter boundary fakes and seeded users. |
| `tests/e2e/test_action_registry.py` | Closed registry and action-context tests. |
| `tests/e2e/test_action_runner.py` | Direct event/error and executor delegation tests. |
| `tests/e2e/test_application_dispatch.py` | Incoming-update routing, selection, no-match/ambiguity, and diagnostics tests. |
| `tests/e2e/test_zone_x_seed.py`, `tests/e2e/verify_zone_x_seed.rb` | Exact YAML/JSON semantic proof and publication tests. |
| `tests/e2e/test_zone_x_journey.py` | Zone X lifecycle and restart end-to-end matrix. |

### Task 1: Closed action registry and transaction-scoped context

**Files:**
- Create: `friendly-bot/src/friendly_bot/actions/__init__.py`, `context.py`, `registry.py`
- Create: `friendly-bot/tests/e2e/test_action_registry.py`, `conftest.py`

**Interfaces:**
- Consumes: F01 `DiscussionAction`, `ActionEvent`, concrete action subclasses, `UserRecord`, `OpenSelectionState`, `DiscussionFlow`, `FlowVersionRecord`, `UnitOfWork`; T02 `IncomingTelegramMessage`, `TelegramGateway`; T02/R03 service ports.
- Produces: `ActionContext`, `ActionExecutorRegistry`, `UnregisteredActionExecutorError`, `DuplicateActionExecutorError`, `TerminalActionEventAlreadyEmittedError`, and `build_action_registry(dependencies: ActionDependencies) -> ActionExecutorRegistry`.

- [ ] **Step 1: Write the failing registry/context tests.**

```python
async def test_registry_is_exactly_complete_for_f01_action_classes() -> None:
    registry = build_action_registry(dependencies)
    registry.assert_complete(concrete_action_types(DiscussionAction))

def test_registry_rejects_duplicate_and_missing_concrete_executors() -> None:
    registry = ActionExecutorRegistry()
    registry.register(SendMessageAction, send_message)
    with pytest.raises(DuplicateActionExecutorError):
        registry.register(SendMessageAction, send_message)
    with pytest.raises(UnregisteredActionExecutorError):
        registry.resolve(EndServiceInteractionsAction())

def test_context_allows_one_non_error_terminal_event() -> None:
    context.emit(ActionEvent(key="human_match.found", payload={"request_id": "local"}))
    with pytest.raises(TerminalActionEventAlreadyEmittedError):
        context.emit(ActionEvent(key="human_match.not_found"))
    with pytest.raises(ReservedActionEventError):
        context.emit(ActionEvent(key="error"))
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_registry.py -q`

Expected: FAIL because the I04 actions package is absent.

- [ ] **Step 3: Implement the closed registry and context.**

```python
class ActionExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[type[DiscussionAction], ActionExecutor[DiscussionAction]] = {}

    def register(self, action_type: type[A], executor: ActionExecutor[A]) -> None:
        if action_type in self._executors:
            raise DuplicateActionExecutorError(action_type.__name__)
        self._executors[action_type] = cast(ActionExecutor[DiscussionAction], executor)

    def resolve(self, action: DiscussionAction) -> ActionExecutor[DiscussionAction]:
        try:
            return self._executors[type(action)]
        except KeyError as exc:
            raise UnregisteredActionExecutorError(type(action).__name__) from exc

    def assert_complete(self, action_types: frozenset[type[DiscussionAction]]) -> None:
        if set(self._executors) != set(action_types):
            raise ActionRegistryCompletenessError(missing=action_types - self._executors.keys(), extra=self._executors.keys() - action_types)

def concrete_action_types(action_union: object) -> frozenset[type[DiscussionAction]]:
    annotated_union = get_args(action_union)[0]
    return frozenset(cast(type[DiscussionAction], item) for item in get_args(annotated_union))
```

Import `get_args`/`cast` from `typing` and derive the concrete class set from F01's discriminated `DiscussionAction` union; do not repeat the schema in a local list. Implement `ActionContext.render()` with only F01-validated dotted values and local output; make all Telegram output an F01 `NewOutboundDelivery` through the supplied UoW. Register exactly the 24 action types named in the I04 design, including `SendServiceChoiceButtonsAction`; no callable, executor class name, or fallback handler is read from configuration.

- [ ] **Step 4: Run the focused test to verify it passes.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_registry.py -q`

Expected: registry equality, duplicate/missing rejection, local rendering, and one-event guard pass.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/src/friendly_bot/actions/__init__.py friendly-bot/src/friendly_bot/actions/context.py friendly-bot/src/friendly_bot/actions/registry.py friendly-bot/tests/e2e/conftest.py friendly-bot/tests/e2e/test_action_registry.py && git commit -m "feat: add typed action composition registry" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_action_registry.py -q && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: focused suite passes after merge; push succeeds; ancestry command exits 0.

### Task 2: Ordered action runner, direct child events, and non-configurable error sender

**Files:**
- Create: `friendly-bot/src/friendly_bot/actions/runner.py`
- Create: `friendly-bot/tests/e2e/test_action_runner.py`

**Interfaces:**
- Consumes: Task 1 registry/context; F01 `ActionEventDiscussionFlowTrigger`, `DiscussionFlow`, `SelectionTransitionEngine`; T02 durable delivery port; F01 diagnostic repository.
- Produces: `ActionRunner.run(flow: DiscussionFlow, context: ActionContext) -> ActionRunResult`, `send_unhandled_action_error(context) -> None`, and `DEFAULT_UNHANDLED_ERROR_TEXT`.

- [ ] **Step 1: Write failing terminal-path tests.**

```python
async def test_terminal_event_executes_one_matching_direct_child_and_stops_parent() -> None:
    result = await runner.run(parent_with_found_child_and_later_action(), context)
    assert result.executed_flow_keys == ["parent", "parent.found"]
    assert fake.outbox.texts == ["child copy"]
    assert "later parent copy" not in fake.outbox.texts

async def test_event_never_bubbles_to_an_ancestor_or_reusable_past_selection() -> None:
    await runner.run(parent_without_direct_found_child_but_ancestor_found_child(), context)
    assert fake.outbox.texts == []
    assert fake.diagnostics.reason_codes == ["action_event.unhandled_non_error"]

async def test_direct_error_child_wins_but_unhandled_error_uses_exact_local_sender() -> None:
    await runner.run(parent_with_direct_error_child(), context)
    assert fake.outbox.texts == ["custom recovery"]
    await runner.run(parent_without_direct_error_child(), context)
    assert fake.outbox.texts[-1] == "Sorry, an error occurred. Error log: 77123."
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_runner.py -q`

Expected: FAIL because `ActionRunner` is absent.

- [ ] **Step 3: Implement ordered execution and direct-only lookup.**

```python
DEFAULT_UNHANDLED_ERROR_TEXT = "Sorry, an error occurred. Error log: {telegram_user_id}."

async def _direct_event_child(parent: DiscussionFlow, event_key: str) -> DiscussionFlow | None:
    matches = [child for child in parent.next_flows
               if isinstance(child.trigger, ActionEventDiscussionFlowTrigger)
               and child.trigger.event_key == event_key]
    if len(matches) > 1:
        raise DirectEventHandlerInvariantError(parent.key, event_key)
    return matches[0] if matches else None

async def send_unhandled_action_error(context: ActionContext) -> None:
    await context.enqueue_text(DEFAULT_UNHANDLED_ERROR_TEXT.format(
        telegram_user_id=context.user.telegram_user_id
    ))
```

Run actions sequentially; on one emitted event, stop immediately and recursively run only the matching direct child with the same UoW/correlation id/local event data. On retryable local failure, use the configured finite safe retry budget; after exhaustion record sanitized diagnostics and route direct `error` or call the exact sender. The default sender is a plain function outside the registry and never appears in flow JSON/root publication. Mark the failed selection unchanged until a safe retry succeeds.

- [ ] **Step 4: Run focused green and negative tests.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_runner.py -q`

Expected: direct child success/error paths, no ancestor bubbling, action stopping, sanitized diagnostics, exact default copy, and retained failed selection pass.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/src/friendly_bot/actions/runner.py friendly-bot/tests/e2e/test_action_runner.py && git commit -m "feat: dispatch terminal action events directly" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_action_runner.py -q && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: focused suite passes after merge; push succeeds; ancestry command exits 0.

### Task 3: Concrete fixed-content, service, matching, notification, and checkpoint executors

**Files:**
- Create: `friendly-bot/src/friendly_bot/actions/executors.py`
- Modify: `friendly-bot/src/friendly_bot/actions/registry.py`
- Modify: `friendly-bot/tests/e2e/test_action_runner.py`

**Interfaces:**
- Consumes: F01 concrete action models and UoW repositories; T02 `ServiceAttendanceService`, `ServiceLifecycleService`, typed Telegram/outbox DTOs; R03 `MatchingService`.
- Produces: `build_action_registry(dependencies)`, every concrete executor, and matching/service `ActionEvent` emission.

- [ ] **Step 1: Write failing delegation and event-key tests.**

```python
@pytest.mark.parametrize(("action", "expected_event"), [
    (FindAndReserveServerAction(service_id="{{ service.id }}"), "human_match.found"),
    (FindAndReserveSafetyResponderAction(service_id="{{ active_service.id | optional }}"), "safety_match.not_found"),
    (SelectServiceAttendanceAction(), "service_attendance.latecomer"),
])
async def test_outcome_action_delegates_once_and_emits_declared_key(action, expected_event) -> None:
    await registry.resolve(action)(action, context)
    assert context.terminal_event.key == expected_event

async def test_match_notification_and_contact_are_local_templates_and_durable_deliveries() -> None:
    await notify_matched_human(NotifyMatchedHumanAction(text="{{ user.name }}"), context)
    await share_human_contact(ShareHumanContactAction(text="{{ matched_server.telegram_url }}"), context)
    assert fake.outbox.texts == ["Alex", "https://t.me/friendly_server"]

async def test_end_interactions_calls_t02_lifecycle_not_a_duplicate_expiry_query() -> None:
    await end_service_interactions(EndServiceInteractionsAction(), context)
    assert fake.lifecycle.calls == [(ZONE_X_ID, NOW)]
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_runner.py -q`

Expected: FAIL because executor functions and registrations are absent.

- [ ] **Step 3: Implement all concrete executors without a generic fallback.**

```python
async def find_and_reserve_server(action: FindAndReserveServerAction, context: ActionContext) -> None:
    assignment = await context.matching.reserve_normal(
        request_id=context.match_request_id(), service_id=context.render_uuid(action.service_id), now=context.now
    )
    context.set_match_assignment(assignment)
    context.emit(ActionEvent(key="human_match.found" if assignment else "human_match.not_found"))

async def select_service_attendance(action: SelectServiceAttendanceAction, context: ActionContext) -> None:
    outcome = await context.services.select_service(context.user.id, context.selected_service_id(), now=context.now)
    context.emit(ActionEvent(key=f"service_attendance.{outcome.kind}", payload=outcome.local_payload()))
```

Implement each of the 24 registered types explicitly. `send_message`, `send_buttons`, `send_service_choice_buttons`, `send_photo`, and `show_activity` enqueue typed T02 output. `save_incoming` preserves the inbound text exactly. `add_service_attendance`, service selection/switch/checkpoint/latecomer actions, and `end_service_interactions` call only T02 APIs. Normal matching calls `reserve_normal`; safety calls `reserve_safety` and never normal matching. Release/exclude/rematch preserve the prior meeting preference. Admin notification creates sanitized diagnostic fan-out; contact sharing validates only the persisted target URL. `return_to_nearest_checkpoint` asks F01's transition engine and applies its returned transition. No executor calls OpenRouter.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_action_registry.py tests/e2e/test_action_runner.py -q`

Expected: every concrete action is registered; transport work is durable; service/matching keys are declared; normal/safety paths remain separate; and checkpoint action delegates to F01.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/src/friendly_bot/actions/executors.py friendly-bot/src/friendly_bot/actions/registry.py friendly-bot/tests/e2e/test_action_registry.py friendly-bot/tests/e2e/test_action_runner.py && git commit -m "feat: compose Friendly Bot action executors" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_action_registry.py tests/e2e/test_action_runner.py -q && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: focused suite passes after merge; push succeeds; ancestry command exits 0.

### Task 4: Application dispatcher, routing terminals, selection state, and sanitized admin diagnostics

**Files:**
- Create: `friendly-bot/src/friendly_bot/app.py`
- Create: `friendly-bot/tests/e2e/test_application_dispatch.py`

**Interfaces:**
- Consumes: Task 2 runner; T02 `TelegramPoller` dispatcher seam, onboarding/account services; F01 transition/open-selection APIs; R03 `ConstrainedRouter.route_update(user_id: UUID, incoming: IncomingText, now: datetime) -> RoutingResult`, `RoutingTerminal`, `PersonaMaintenanceService`.
- Produces: `FriendlyBotApplication.dispatch(*, user_id, incoming, unit_of_work, now) -> DispatchResult`, `RuntimeRoutingPolicy(no_match_text: str | None)`, and `DiagnosticPort` composition.

- [ ] **Step 1: Write failing dispatch-state and terminal tests.**

```python
async def test_router_uses_current_reusable_past_and_global_once_each() -> None:
    fake_router.result = RoutingResult(
        selected_keys=(RoutingDecision("current.help"), RoutingDecision("global.safety")),
        terminal=RoutingTerminal.DONE,
    )
    await app.dispatch(user_id=USER_ID, incoming=message("help"), unit_of_work=uow, now=NOW)
    assert fake_transition.executed_flow_keys == {"current.help", "global.safety"}
    assert fake_router.prompt.reply_text == "previous question"

@pytest.mark.parametrize(("terminal", "configured", "expected"), [
    (RoutingTerminal.CLARIFY, None, "Sorry, which message were you referring to?"),
    (RoutingTerminal.NO_MATCH, None, "Sorry, I didn't understand your request."),
    (RoutingTerminal.NO_MATCH, "Please choose one of the options above.", "Please choose one of the options above."),
])
async def test_router_terminal_has_fixed_ambiguity_and_configured_or_default_no_match(terminal, configured, expected) -> None:
    app.routing_policy = RuntimeRoutingPolicy(no_match_text=configured)
    fake_router.result = RoutingResult(selected_keys=(), terminal=terminal)
    await app.dispatch(user_id=USER_ID, incoming=message("unclear"), unit_of_work=uow, now=NOW)
    assert fake.outbox.texts[-1] == expected

async def test_timestamp_branch_survives_onboarding_and_leaf_returns_nearest_checkpoint() -> None:
    await app.dispatch(user_id=USER_ID, incoming=message("Alex"), unit_of_work=uow, now=NOW)
    assert await open_parent_keys(USER_ID) == {"onboarding.name_capture", "service.zone_x.timestamp.service_questions"}
    assert fake_transition.returned_to == "service.zone_x.timestamp.service_questions"

async def test_diagnostic_fanout_excludes_prompt_and_raw_transport_fields() -> None:
    await app.record_error(RuntimeError("secret raw body"), context)
    assert fake_diagnostics.admin_recipients == {ADMIN_A, ADMIN_B}
    assert "secret raw body" not in fake_diagnostics.safe_context_json
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q`

Expected: FAIL because application composition is absent.

- [ ] **Step 3: Implement the dispatcher without a second transaction or router fallback.**

```python
async def dispatch(self, *, user_id: UUID, incoming: IncomingTelegramMessage,
                   unit_of_work: UnitOfWork, now: datetime) -> DispatchResult:
    direct = await self._direct_trigger_matcher.match(incoming, unit_of_work, now)
    if direct:
        return await self._execute_selected(direct, unit_of_work, incoming, now)
    result = await self._router.route_update(user_id, IncomingText.from_telegram(incoming), now)
    if result.terminal is RoutingTerminal.CLARIFY:
        await self._enqueue_fixed(unit_of_work, user_id, "Sorry, which message were you referring to?")
        return DispatchResult.clarified()
    if result.terminal is RoutingTerminal.NO_MATCH:
        text = self.routing_policy.no_match_text or "Sorry, I didn't understand your request."
        await self._enqueue_fixed(unit_of_work, user_id, text)
        return DispatchResult.no_match()
    return await self._apply_routing_decisions_once(result.selected_keys, unit_of_work, incoming, now)
```

Resolve exact buttons/commands first. Recheck service-bound callbacks through T02 lifecycle and enqueue exact expiry copy after interaction end. For ordinary text, pass all valid current/reusable/global candidates and reply text only to R03; apply each returned key no more than once through F01. Execute actions before applying their state mutation, preserve independent current timestamp branches, and let F01 own nearest checkpoint behavior. Run persona maintenance only with prompt-safe data. Route caught failures through sanitized diagnostic record plus one durable admin notification per admin; do not include a raw error, user text, Bot API data, structured identifier, DOB, URL, or OpenRouter payload.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q`

Expected: deterministic direct triggers, multi-key/reuse/global selection, default/configured no-match, exact ambiguity, checkpoints, service expiry, and diagnostic privacy/fan-out pass.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/src/friendly_bot/app.py friendly-bot/tests/e2e/test_application_dispatch.py friendly-bot/tests/e2e/conftest.py && git commit -m "feat: compose Friendly Bot application dispatcher" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: focused suite passes after merge; push succeeds; ancestry command exits 0.

### Task 5: Canonical Zone X JSON seed and semantic publication proof

**Files:**
- Create: `friendly-bot/seeds/zone-x.json`, `friendly-bot/tests/e2e/test_zone_x_seed.py`, `friendly-bot/tests/e2e/verify_zone_x_seed.rb`
- Modify: `friendly-bot/src/friendly_bot/app.py`

**Interfaces:**
- Consumes: canonical `friendly-bot/docs/examples/zone-x-service-example.md`; F01 publication/version/service/timestamp repositories.
- Produces: `load_zone_x_seed(path: Path) -> ZoneXSeed`, `publish_zone_x_seed(seed, unit_of_work, now) -> PublishedZoneX`, and an executable semantic-equivalence verifier.

- [ ] **Step 1: Write failing semantic and immutable-publish tests.**

```python
def test_zone_x_json_is_semantically_equal_to_canonical_yaml() -> None:
    completed = subprocess.run(
        ["ruby", "tests/e2e/verify_zone_x_seed.rb", "docs/examples/zone-x-service-example.md", "seeds/zone-x.json"],
        text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr

async def test_seed_publishes_system_service_latecomer_and_seven_timestamp_roots_idempotently() -> None:
    first = await publish_zone_x_seed(load_zone_x_seed(SEED), uow, NOW)
    second = await publish_zone_x_seed(load_zone_x_seed(SEED), uow, NOW)
    assert first.service_key == second.service_key == "zone_x_2026_10_18"
    assert await timestamp_keys(ZONE_X_ID) == {"zone_x.marketing_starts", "zone_x.one_day_before", "zone_x.doors_open", "zone_x.service_starts", "zone_x.service_ends", "zone_x.thank_you", "zone_x.interaction_ends"}
    assert await immutable_version_count() == first.version_count

def test_default_error_sender_is_absent_from_seed_and_all_roots() -> None:
    seed = json.loads(SEED.read_text())
    assert "Sorry, an error occurred. Error log:" not in json.dumps(seed)
    assert all(root["key"] != "error" for root in every_root(seed))
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_zone_x_seed.py -q`

Expected: FAIL because Zone X JSON, verifier, and publisher are absent.

- [ ] **Step 3: Create the whole semantic JSON document and verifier.**

Serialize the canonical YAML fence's entire mapping exactly as JSON: `system_global_root_excerpt` and `service`, its highkey/timezone/timestamps, service checkpoint, latecomer flow, every ordered flow/action/trigger/template/button, and all seven timestamp roots. Do not add explanatory data, executable names, default-error branch, fallback copy, production venue approval, or any test-only mutation.

```ruby
require "json"
require "yaml"

markdown = File.read(ARGV.fetch(0))
yaml_text = markdown.match(/^```yaml\n(.*?)^```$/m)&.captures&.first or abort("canonical YAML fence not found")
canonical = YAML.safe_load(yaml_text, aliases: false)
seed = JSON.parse(File.read(ARGV.fetch(1)))
def first_difference(expected, actual, path = "")
  return nil if expected == actual
  if expected.is_a?(Hash) && actual.is_a?(Hash)
    (expected.keys | actual.keys).sort.each do |key|
      child = first_difference(expected[key], actual[key], "#{path}/#{key}")
      return child unless child.nil?
    end
  elsif expected.is_a?(Array) && actual.is_a?(Array)
    [expected.length, actual.length].max.times do |index|
      child = first_difference(expected[index], actual[index], "#{path}/#{index}")
      return child unless child.nil?
    end
  end
  path.empty? ? "/" : path
end
pointer = first_difference(canonical, seed)
abort("Zone X YAML/JSON semantic mismatch at #{pointer}") unless pointer.nil?
```

Make `publish_zone_x_seed` validate each root with the correct F01 root kind/context schema, publish content-hashed immutable versions, upsert by service key, and bind all seven timestamp rows. Repeat publication must return existing hashes without mutating old versions or selections.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_zone_x_seed.py -q && ruby tests/e2e/verify_zone_x_seed.rb docs/examples/zone-x-service-example.md seeds/zone-x.json`

Expected: semantic equality, all action discriminator parsing, immutable/idempotent publication, seven timestamp roots, and default-error negative assertion pass.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/seeds/zone-x.json friendly-bot/src/friendly_bot/app.py friendly-bot/tests/e2e/test_zone_x_seed.py friendly-bot/tests/e2e/verify_zone_x_seed.rb && git commit -m "feat: add canonical Zone X development seed" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_zone_x_seed.py -q && ruby tests/e2e/verify_zone_x_seed.rb docs/examples/zone-x-service-example.md seeds/zone-x.json && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: seed suite/verifier pass after merge; push succeeds; ancestry command exits 0.

### Task 6: Runtime assembly, module CLI, operational diagnostics, and local documentation

**Files:**
- Create: `friendly-bot/src/friendly_bot/__main__.py`, `friendly-bot/docs/examples/friendly-bot-local-runtime.md`
- Modify: `friendly-bot/src/friendly_bot/__init__.py`, `friendly-bot/src/friendly_bot/app.py`, `friendly-bot/tests/e2e/test_application_dispatch.py`

**Interfaces:**
- Consumes: all T02 runtime components and runtime lock; all R03 services; Task 1 registry; Task 5 seed publisher; F01 settings/UoW factory.
- Produces: `build_application(settings) -> FriendlyBotRuntime`, `run_application() -> None`, `main() -> NoReturn` behavior through `python -m friendly_bot`.

- [ ] **Step 1: Write failing composition/lifecycle tests.**

```python
async def test_runtime_builds_one_of_each_component_and_preflights_before_polling() -> None:
    runtime = build_application(test_settings, factories)
    await runtime.run_until_cancelled(cancel_after_first_tick=True)
    assert fake.preflight.calls == ["ensure_polling_ready"]
    assert fake.runtime_lock.acquire_count == fake.poller.start_count == fake.scheduler.start_count == fake.outbox.start_count == 1

async def test_runtime_lock_loss_stops_polling_scheduler_and_outbox_without_reacquire() -> None:
    with pytest.raises(TelegramRuntimeLockLostError):
        await runtime.run_until_lock_loss()
    assert fake.poller.stopped and fake.scheduler.stopped and fake.outbox.stopped
    assert fake.runtime_lock.acquire_count == 1

def test_module_cli_reports_redacted_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_module(monkeypatch, database_url="postgresql+asyncpg://u:secret@127.0.0.1:5832/db", fail_preflight=True)
    assert result.returncode != 0 and "secret" not in result.stderr
```

- [ ] **Step 2: Run the focused test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q`

Expected: FAIL because runtime assembly and module entry point are absent.

- [ ] **Step 3: Implement single-runtime composition and operational guide.**

```python
async def run_application() -> None:
    runtime = build_application(load_settings())
    await runtime.preflight()
    async with runtime.runtime_lock:
        async with asyncio.TaskGroup() as group:
            group.create_task(runtime.poller.run_forever())
            group.create_task(runtime.scheduler.run_forever())
            group.create_task(runtime.outbox.run_forever())
            await runtime.wait_for_shutdown_signal()
            runtime.request_stop()
```

Construct one explicit dependency graph, publish/load Zone X before polling, and register signal handlers for `SIGINT`/`SIGTERM`. Propagate lock loss to stop all three loops and exit nonzero without reacquisition. The guide includes `id -u` validation for `504`, exact Compose namespace/port checks, redacted env creation guidance, `docker compose -p friendly-bot-u504 up -d postgres`, `pg_isready -h 127.0.0.1 -p 5832`, `uv run alembic upgrade head`, `uv run python -m friendly_bot`, and exact-project teardown. State that it must not be used to approve Zone X venue/copy/contact/schedule for production.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q && uv run python -m friendly_bot --help`

Expected: fake composition/loss/redaction tests pass; module help exits 0 without starting a runtime.

- [ ] **Step 5: Commit, reconcile, and prove remote reachability.**

Run: `git add friendly-bot/src/friendly_bot/__init__.py friendly-bot/src/friendly_bot/__main__.py friendly-bot/src/friendly_bot/app.py friendly-bot/docs/examples/friendly-bot-local-runtime.md friendly-bot/tests/e2e/test_application_dispatch.py && git commit -m "feat: add local Friendly Bot runtime entrypoint" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest tests/e2e/test_application_dispatch.py -q && uv run python -m friendly_bot --help && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: focused suite and non-starting CLI help pass after merge; push succeeds; ancestry command exits 0.

### Task 7: Zone X end-to-end matrix, restart/idempotency proof, review, and G3 handoff

**Files:**
- Create: `friendly-bot/tests/e2e/test_zone_x_journey.py`, `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/i04-execution.json`
- Modify: `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/status/i04.md`

**Interfaces:**
- Consumes: all completed I04 tasks and remote G1/G2 manifests.
- Produces: final G3 evidence for coordinator review; it does not modify `PLANNING_GATE.md`, aggregate status, masterplans, or gate state.

- [ ] **Step 1: Write the failing full Zone X acceptance matrix.**

```python
@pytest.mark.parametrize("moment, audience, expected_root", [
    (MARKETING, "ALL_NBNCS", "service.zone_x.timestamp.marketing"),
    (ONE_DAY, "ALL_NBNCS", "service.zone_x.timestamp.one_day_before"),
    (DOORS_OPEN, "ALL_NBNCS", "service.zone_x.timestamp.doors_open"),
    (SERVICE_START, "SERVICE_NBNCS", "service.zone_x.timestamp.service_questions"),
    (SERVICE_END, "ALL_SERVICE_ATTENDEES", "service.zone_x.timestamp.after_service"),
    (THANK_YOU, "ALL_SERVICE_ATTENDEES", "service.zone_x.timestamp.thank_you"),
    (INTERACTION_END, "ALL_SERVICE_ATTENDEES", "service.zone_x.timestamp.interaction_ends"),
])
async def test_zone_x_timestamp_audience_and_root(moment, audience, expected_root) -> None:
    await scheduler.run_once(now=moment)
    assert await claimed_audience_name(expected_root) == audience
    assert await opened_root_key_for(USER_ID) == expected_root

async def test_highkey_latecomer_expiry_and_stale_button_matrix() -> None:
    assert await new_nbnc_at(DOORS_OPEN) == "service.zone_x.home"
    assert await check_in_at(DOORS_CLOSE + MINUTE) == "service.zone_x.latecomer"
    assert await click_service_button_at(INTERACTION_END) == "Sorry, the service is over!"

async def test_normal_rematch_and_safety_paths_remain_separate() -> None:
    assert await normal_match() == "human_match.found"
    assert await rematch_after_not_responding() == "human_match.found"
    assert await safety_with_no_leader_or_staff() == "safety_match.not_found"
    assert fake_normal_pool.was_not_queried_for_safety

async def test_restart_does_not_duplicate_update_timestamp_or_uncertain_send() -> None:
    await first_runtime.tick(); await restarted_runtime.tick()
    assert await processed_update_count() == 1
    assert await timestamp_claim_count() == 1
    assert await uncertain_send_attempt_count() == 1
```

- [ ] **Step 2: Run the end-to-end test to verify it fails.**

Run: `cd friendly-bot && uv run pytest tests/e2e/test_zone_x_journey.py -q`

Expected: FAIL until every cross-package Zone X path is fully composed.

- [ ] **Step 3: Make only evidence-driven I04 integration repairs.**

Repair a failed action mapping, composition call, local template value, test fixture, or I04 integration boundary only when its failing assertion demonstrates the defect. Preserve the exact Zone X copy and action graph; do not alter an upstream package except the blueprint's proven-incomplete-prerequisite recovery rule, and then update the upstream evidence as well. The final matrix must cover directions, what-to-expect, normal match, both meeting choices, not-responding rematch, no normal match, safety found/no-responder, service switch/choice/latecomer/end, toilet/Jesus/unknown service questions, after-service buttons, all timestamp audiences, highkey, expiry, current/reusable-past/checkpoint return, direct event handling, and default-error/privacy negatives.

- [ ] **Step 4: Run the final full verification and local G3 canary once.**

Run: `cd friendly-bot && test -n "${FRIENDLY_BOT_DATABASE_URL:-}" && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot && ruby tests/e2e/verify_zone_x_seed.rb docs/examples/zone-x-service-example.md seeds/zone-x.json && uv run python scripts/db_runtime_check.py --require-uid 504 --require-project friendly-bot-u504 --require-network friendly-bot-u504 --require-volume friendly-bot-u504-postgres --require-port 5832 && docker compose -p friendly-bot-u504 up -d postgres && pg_isready -h 127.0.0.1 -p 5832 && uv run alembic upgrade head && uv run pytest tests/e2e/test_zone_x_journey.py -q && docker compose -p friendly-bot-u504 down`

Expected: all tests/static checks and semantic verifier exit 0; runtime guard validates only UID `504`/exact namespace/port; PostgreSQL readiness, empty migration, and Zone X E2E pass; teardown names only `friendly-bot-u504`. Do not print the database URL or credentials in terminal capture or execution manifest.

- [ ] **Step 5: Review, commit final evidence, reconcile, and prove remote reachability.**

Submit the exact I04 diff, G1/G2 prerequisite commits, full-suite output, semantic verifier result, local runtime canary result, default-error/privacy-negative test names, and restart evidence to the repository review workflow. Resolve every actionable I04-owned finding and rerun its focused command. Then run: `python3 -m json.tool friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/i04-execution.json && git diff --check && git add friendly-bot/tests/e2e/test_zone_x_journey.py friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/status/i04.md friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/i04-execution.json && git commit -m "test: record Zone X G3 acceptance evidence" && git fetch origin && git merge --no-edit origin/main && cd friendly-bot && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot && ruby tests/e2e/verify_zone_x_seed.rb docs/examples/zone-x-service-example.md seeds/zone-x.json && cd .. && git push origin main && git fetch origin && git merge-base --is-ancestor HEAD origin/main`

Expected: manifest JSON and diff checks pass; full suite/static checks/verifier pass after reconciliation; push succeeds; final I04 commit is reachable from `origin/main`. Record evidence as G3-ready for coordinator/Ryan verification, not G3-passed.

## Plan Self-Review

Task 1 provides the only typed action registry/context. Task 2 proves ordered direct-child events and the exact non-configurable default error path. Task 3 maps every canonical action discriminator to one upstream delegation path. Task 4 composes ingress, routing terminals, reuse/current/global/checkpoints, expiry, and sanitized diagnostics. Task 5 makes real Zone X JSON data semantically identical to its canonical YAML and publishes it immutably. Task 6 composes one local runtime and documents account-isolated operation. Task 7 proves all Zone X lifecycle, audience, matching, safety, restart, privacy, full-suite, review, and G3 handoff evidence. No task adds a flow instance, duplicate service/router/persistence path, runtime fallback, configurable default-error root, or production approval.
