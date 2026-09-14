# Telegram Provider Failure and Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make expected OpenRouter and Telegram polling failures non-fatal while restoring a deterministic `/start` onboarding entry point.

**Architecture:** `FriendlyBotApplication` owns the user-visible routing-failure result because it still holds the claimed-update transaction, user identity, diagnostic repository, and durable outbox.  The poll loop only backs off typed Telegram API failures; it does not swallow safety or consistency faults.  The application composes the existing onboarding and attendance services before normal model routing so `/start` and first-contact onboarding never require OpenRouter.

**Tech Stack:** Python 3.13, asyncio, pytest/pytest-asyncio, Pydantic, SQLAlchemy/PostgreSQL, existing Friendly Bot routing/Telegram/onboarding services.

## Global Constraints

- Work directly on `main`; the user explicitly prohibited branches and worktrees.
- Preserve one PostgreSQL advisory runtime lock and allow genuine lock loss to stop the process.
- Preserve `GatewayError` response redaction: diagnostics and outputs use only static codes and fixed copy.
- A durable ingress update is consumed only when its fallback diagnostic/output and cursor can commit in the same transaction.
- Do not add a second database, Compose project, polling client, webhook path, migration, or generic provider fallback.
- Commit and push each independently verified task to `origin/main`.

---

### Task 1: Classify and commit closed routing failures

**Files:**
- Modify: `src/friendly_bot/app.py:181-321`
- Modify: `tests/e2e/test_application_dispatch.py`

**Interfaces:**
- Consumes: `GatewayTransportError`, `GatewayProtocolError`, `UnitOfWork.diagnostics`, and `_enqueue_fixed_text`.
- Produces: `DispatchResult(kind="failed")` and `_record_routing_failure(...)` with no provider/user text.

- [x] **Step 1: Write failing application tests**

Add a router double whose `route_update_in_uow` raises each closed gateway error.  Assert the observable output and diagnostic facts, not the double call:

```python
result = await application.dispatch(user_id=USER.id, incoming=message("help"), unit_of_work=uow)

assert result.kind == "failed"
assert [delivery.payload for delivery in uow.deliveries.enqueued] == [
    {"text": "Sorry, an error occurred. Error log: 73."}
]
assert diagnostics.reason_codes == ["routing.provider_unavailable"]
```

Use a separate literal expectation for protocol failure:

```python
assert diagnostics.reason_codes == ["routing.provider_invalid_response"]
```

- [x] **Step 2: Run the two tests and verify RED**

Run: `uv run pytest tests/e2e/test_application_dispatch.py -q -k 'routing_failure'`

Expected: FAIL because `GatewayTransportError` and `GatewayProtocolError` escape `FriendlyBotApplication.dispatch`.

- [x] **Step 3: Add the narrow application boundary**

Import only `GatewayTransportError` and `GatewayProtocolError`.  Wrap the single
`route_update_in_uow(...)` call, map the two exact exception types to literal reason codes,
record `severity="error"` / `safe_summary="routing provider failed"`, fan out the diagnostic,
and queue `DEFAULT_UNHANDLED_ERROR_TEXT.format(telegram_user_id=user.telegram_user_id)`.
Extend `DispatchResult.kind` with `"failed"`.  Do not catch `GatewayPrivacyConfigurationError`,
`asyncio.CancelledError`, database errors, or a generic `Exception`.

- [x] **Step 4: Run the focused application suite and verify GREEN**

Run: `uv run pytest tests/e2e/test_application_dispatch.py -q`

Expected: PASS, including normal direct-command, router-terminal, callback, and the new failure tests.

- [x] **Step 5: Commit and push Task 1**

Run: `git add src/friendly_bot/app.py tests/e2e/test_application_dispatch.py && git commit -m "fix: contain routing provider failures" && git push origin main`

### Task 2: Back off safely after Telegram poll failures

**Files:**
- Modify: `src/friendly_bot/app.py:1190-1198`
- Create: `tests/unit/test_runtime_loops.py`

**Interfaces:**
- Consumes: `PollResult.gateway_failure` and `_wait_for_stop(stop_event, seconds=0.5)`.
- Produces: one wait before retry after a typed Telegram API failure, with the durable cursor still owned by `TelegramPoller`.

- [x] **Step 1: Write a failing loop test**

Use a real `asyncio.Event`, a fake healthy lock, and a fake poller that yields once and returns
`PollResult(0, (), TelegramApiError(502))`.  Start `_poll_forever`, yield control briefly,
and assert that `run_once` has been called once before setting the stop event.  The production
change that makes this fail is removing the post-failure idle wait, which would immediately
call `run_once` again.

- [x] **Step 2: Run the focused loop test and verify RED**

Run: `uv run pytest tests/unit/test_runtime_loops.py -q`

Expected: FAIL because the current loop immediately begins the next poll after a typed failure.

- [x] **Step 3: Add the typed-failure wait**

Store `result = await runtime.poller.run_once(...)`.  When `result.gateway_failure is not None`,
await `_wait_for_stop(stop_event, seconds=0.5)`.  Leave exceptions unhandled so cancellation,
lock loss, and incomplete transactions retain their fail-closed semantics.

- [x] **Step 4: Run Telegram and loop tests and verify GREEN**

Run: `uv run pytest tests/unit/test_runtime_loops.py tests/unit/telegram tests/integration/telegram -q`

Expected: PASS; returned poll failures neither advance the cursor nor cause an immediate retry loop.

- [x] **Step 5: Commit and push Task 2**

Run: `git add src/friendly_bot/app.py tests/unit/test_runtime_loops.py && git commit -m "fix: back off after telegram poll failures" && git push origin main`

### Task 3: Compose deterministic onboarding before model routing

**Files:**
- Modify: `src/friendly_bot/app.py:202-321,1078-1148`
- Modify: `seeds/zone-x.json`
- Modify: `tests/e2e/test_application_dispatch.py`
- Modify: `tests/e2e/test_seed_runtime.py` (or the existing seed/runtime test module found during implementation)

**Interfaces:**
- Consumes: `OnboardingService.handle_in_uow`, `ServiceAttendanceService.resolve_for_new_nbnc_in_uow`, `PublishedZoneX`, `_open_root`, and `normalize_command`.
- Produces: `DispatchResult(kind="onboarding")`; an unnamed-user `/start` path with no `ConstrainedRouter` call; a saved display name followed by selected/late/system root opening.

- [x] **Step 1: Write failing `/start` boundary tests**

Add a router double that raises `AssertionError` if called.  Use the real onboarding service with a transaction-shaped fake UoW and a literal unnamed user.  Assert:

```python
started = await application.dispatch(user_id=USER.id, incoming=message("/start"), unit_of_work=uow)
assert started.kind == "onboarding"
assert [delivery.payload for delivery in uow.deliveries.enqueued] == [
    {"text": "Hey! Welcome to The Zone! Glad to see you here today!\n\nHow may I address you?"}
]
```

Then dispatch `"Ari"` against the persisted user fixture and assert its display name is
`"Ari"`, the router has still not been called for that name-capture update, and the system
or selected Zone X root has been opened according to the literal service time fixture.
Add an existing-user `/start` test that opens the system root without model routing.

- [x] **Step 2: Run the new `/start` tests and verify RED**

Run: `uv run pytest tests/e2e/test_application_dispatch.py -q -k 'start or onboarding'`

Expected: FAIL because the current application sends `/start` to `route_update_in_uow` and
does not own an `OnboardingService`.

- [x] **Step 3: Compose onboarding and attendance in `FriendlyBotApplication`**

Add an injected `OnboardingService` field and construct it once in `build_application` from
the existing UoW factory.  For non-callback text, call `handle_in_uow` before opening normal
branches.  Map `name_capture` to the exact fixed two-sentence prompt and return `"onboarding"`.
Map `name_captured` to `resolve_for_new_nbnc_in_uow(..., services=(self._zone_x.service,))`;
open the service root for `selected`, the latecomer root for `latecomer`, and the system root
for `none_available` or `ended`.  Map `existing_start` to the system root.  Only the ordinary
`existing` result continues to direct command/model routing.  Reuse caller-owned UoW methods
throughout.

- [x] **Step 4: Make the system no-service root visibly configured**

Move the existing literal `"What would you like help with?"` from the system root's
`return_actions` into its root `actions` as `{"type": "send_message", "text": ...}` while
leaving the checkpoint return copy intact.  Add or update the seed validation test to assert
the parsed system root has this action; do not make `/start` an OpenRouter message trigger.

- [x] **Step 5: Run focused onboarding/application/seed tests and verify GREEN**

Run: `uv run pytest tests/unit/onboarding tests/unit/services/test_attendance.py tests/e2e/test_application_dispatch.py tests/e2e/test_seed_runtime.py -q`

Expected: PASS, with `/start` and first-contact paths avoiding the model router and existing
flow routing behavior retained for named users.

- [x] **Step 6: Commit and push Task 3**

Run: `git add src/friendly_bot/app.py seeds/zone-x.json tests/e2e && git commit -m "fix: compose deterministic telegram onboarding" && git push origin main`

### Task 4: Verify the runtime contract before Telegram smoke testing

**Files:**
- Modify: `README.md` only if its `/start` wording needs a correction after the accepted tests
- Modify: `docs/superpowers/plans/2026-09-14-telegram-provider-failure-and-start.md`

**Interfaces:**
- Consumes: merged `main`, the guarded operator-owned PostgreSQL namespace, and ignored local `.env`.
- Produces: fresh automated evidence and a controlled Telegram test request.

- [x] **Step 1: Run the full automated suite from a fresh base directory**

Run: `uv run pytest --basetemp /private/tmp/friendly-bot-provider-start-tests -q`

Expected: all tests pass; integration tests without a supplied database URL may skip only according to their existing markers.

- [x] **Step 2: Run static verification**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot`

Expected: all commands exit zero.

- [ ] **Step 3: Run the guarded database verification**

Run: `uv run alembic upgrade head && uv run pytest tests/integration/telegram/test_runtime_lock.py -q`

Expected: migration is current and genuine advisory-lock loss still fails closed.

- [ ] **Step 4: Commit verification documentation and push, if changed**

Run: `git add README.md docs/superpowers/plans/2026-09-14-telegram-provider-failure-and-start.md && git commit -m "docs: record telegram failure verification" && git push origin main`

Commit only if Task 4 changed a tracked document.  Otherwise run `git status --short` and leave no unrelated changes.

## Plan self-review

- **Spec coverage:** Task 1 implements the provider's reserved error result with redacted diagnostics; Task 2 implements EdenMind-style safe poll backoff; Task 3 implements product sections 9.2 and 9.3's deterministic start boundary; Task 4 proves the runtime and lock safety were not weakened.
- **Placeholder scan:** The tasks identify exact files, interfaces, output text, reason codes, red/green commands, and commit commands.  No execution placeholder is left.
- **Type consistency:** `DispatchResult` gains only `"failed"` and `"onboarding"`; `PollResult.gateway_failure` remains the existing typed API failure; all onboarding and attendance mutations use their existing caller-owned-UoW methods.
