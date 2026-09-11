# T02 Telegram and Service Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the typed Telegram, durable delivery, onboarding, attendance, and timestamp-scheduling packages for the Friendly Bot local MVP.

**Architecture:** A strict direct Telegram gateway returns typed outcomes; PostgreSQL-owned F01 repositories make updates, outbox records, and timestamp claims durable. Small application services make onboarding and lifecycle decisions, while F01's generic flow state engine retains responsibility for flow transitions and I04 retains central composition/action registration.

**Tech Stack:** Python 3.12, `httpx`, Pydantic v2, SQLAlchemy async/PostgreSQL and pytest/pytest-asyncio supplied by F01.

**Spec:** `friendly-bot/docs/superpowers/specs/2026-09-12-friendly-bot-mvp/2026-09-12-t02-telegram-service-delivery-design.md`

## Global Constraints

- Begin only after a coordinator-passed `PG` receipt and implemented F01 G1 evidence are reachable from `origin/main`; inspect F01's actual exports before Task 1.
- Use F01 `UnitOfWork`, repositories, immutable flow definitions, and `SelectionTransitionEngine`; create no migration, ORM model, local session, or persistence shim.
- Telegram uses long polling only. Clear a webhook before polling; do not create webhook ingress, hosted runtime, or second poller.
- Persist/update state before side effects; retry only parsed definite non-sends; never blindly replay an ambiguous send.
- Keep bot tokens, raw Telegram payloads, response bodies, DOBs, and user text out of logs and diagnostics.
- Commands are configured flow triggers; `/cancel` has no implicit behavior. T02 owns no action registry, runtime composition, or Zone X seed.
- At every independently testable task: focused test, exact owned-path stage, commit directly to `main`, fetch/merge newer `origin/main`, rerun affected test, push, and prove remote ancestry while holding `/tmp/friendly-bot-main-mutation-u504.lock`.

## File structure

| File | Responsibility |
| --- | --- |
| `src/friendly_bot/telegram/models.py` | Minimal frozen inbound/outbound Telegram DTOs and typed send/API outcomes. |
| `src/friendly_bot/telegram/client.py` | Redacted direct Bot API calls, strict envelopes, webhook clearing and long-poll reads. |
| `src/friendly_bot/telegram/poller.py` | Durable offset/duplicate processing and ordered polling loop. |
| `src/friendly_bot/telegram/outbox.py` | Claim/send/attempt state machine with definite-vs-uncertain semantics. |
| `src/friendly_bot/telegram/runtime_lock.py` | PostgreSQL session advisory-lock guard for one runtime. |
| `src/friendly_bot/onboarding/service.py` | Unknown/existing NBNC and operational login/manage/logout decisions. |
| `src/friendly_bot/services/attendance.py` | Highkey/lowkey/latecomer/switch/ended attendance outcomes. |
| `src/friendly_bot/services/scheduler.py` | Due timestamp claims, audience expansion, catch-up, root-selection/outbox creation. |
| `src/friendly_bot/services/lifecycle.py` | Interaction-end expiry and system-checkpoint return request. |
| `tests/unit/{telegram,onboarding,services}/` | Isolated DTO, parser, policy, and outcome tests. |
| `tests/integration/{telegram,onboarding,services}/` | PostgreSQL durability, restart, exclusivity, lifecycle, and scheduler contracts. |

### Task 1: Typed Telegram model and strict Bot API client

**Files:**
- Create: `friendly-bot/src/friendly_bot/telegram/__init__.py`, `models.py`, `client.py`
- Create: `friendly-bot/tests/unit/telegram/test_models.py`, `test_client.py`

**Interfaces:**
- Produces: `IncomingTelegramUpdate`, `TelegramMessage`, `OutboundTelegramMessage`, `TelegramUpdates`, `TelegramSendConfirmed`, `TelegramSendRetry`, `TelegramSendRejected`, `TelegramSendUncertain`, `TelegramApiClient`.
- Consumes: `pydantic.SecretStr`; no domain or ORM object.

- [ ] **Step 1: Write failing strict parsing and outcome tests.**

```python
async def test_non_200_success_envelope_is_uncertain(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(200, json={"ok": True, "result": {"message_id": 17}})
    assert await client.send(OutboundTelegramMessage(chat_id=42, text="Hi", idempotency_key="x")) == TelegramSendConfirmed(17)

def test_callback_and_reply_are_normalized_without_raw_payload() -> None:
    update = parse_update(callback_fixture())
    assert update.message.callback_data == "zone_x.attendance.here"
    assert not hasattr(update, "raw_payload")
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_models.py tests/unit/telegram/test_client.py -q`

Expected: FAIL because the Telegram package is absent.

- [ ] **Step 3: Implement the narrow wire boundary.**

```python
@dataclass(frozen=True, slots=True)
class TelegramSendUncertain:
    code: Literal["telegram_transport_error", "telegram_response_malformed"]

async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome:
    try:
        response = await self._client.post(self._endpoint("sendMessage"), json={"chat_id": request.chat_id, "text": request.text})
    except (httpx.TimeoutException, httpx.TransportError):
        return TelegramSendUncertain("telegram_transport_error")
    return _parse_send_envelope(response)
```

Validate positive IDs and non-empty text; parse `429` only with a positive `retry_after`; classify 5xx as retryable and permanent 4xx as rejected. Do not log the endpoint, payload, or body.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_models.py tests/unit/telegram/test_client.py -q`

Expected: all malformed, retry, rejection, callback, reply, and success cases pass.

- [ ] **Step 5: Commit this boundary.**

Run: `git add friendly-bot/src/friendly_bot/telegram/__init__.py friendly-bot/src/friendly_bot/telegram/models.py friendly-bot/src/friendly_bot/telegram/client.py friendly-bot/tests/unit/telegram/test_models.py friendly-bot/tests/unit/telegram/test_client.py && git commit -m "feat: add typed Telegram API client"`

### Task 2: Webhook preflight and single-runtime lock

**Files:**
- Create: `friendly-bot/src/friendly_bot/telegram/runtime_lock.py`, `preflight.py`
- Create: `friendly-bot/tests/unit/telegram/test_preflight.py`, `tests/integration/telegram/test_runtime_lock.py`

**Interfaces:**
- Produces: `TelegramPreflight.ensure_polling_ready()`, `TelegramRuntimeLock.acquire()`, `TelegramRuntimeLockLostError`.
- Consumes: Task 1 `TelegramApiClient`; F01's PostgreSQL connection factory only.

- [ ] **Step 1: Write failing preflight/lock tests.**

```python
async def test_preflight_clears_configured_webhook_without_dropping_updates() -> None:
    await preflight.ensure_polling_ready()
    assert fake.calls == [("getWebhookInfo",), ("deleteWebhook", {"drop_pending_updates": False})]

async def test_second_runtime_cannot_acquire_fixed_session_lock(postgres: Postgres) -> None:
    async with await TelegramRuntimeLock.acquire(postgres):
        with pytest.raises(TelegramRuntimeAlreadyRunningError):
            await TelegramRuntimeLock.acquire(postgres)
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_preflight.py tests/integration/telegram/test_runtime_lock.py -q`

Expected: FAIL because preflight and lock modules are absent.

- [ ] **Step 3: Implement webhook clearing and a session-held advisory lock.**

```python
async def ensure_polling_ready(self) -> None:
    info = await self._gateway.get_webhook_info()
    if not isinstance(info, TelegramWebhookInfo): raise TelegramPreflightError("webhook_state_unknown")
    if info.url:
        result = await self._gateway.delete_webhook(drop_pending_updates=False)
        if not isinstance(result, TelegramWebhookCleared): raise TelegramPreflightError("webhook_clear_failed")
```

Use a documented fixed PostgreSQL advisory key on a dedicated, non-pooled connection; expose a health guard that raises on connection loss. Do not reacquire after loss.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_preflight.py tests/integration/telegram/test_runtime_lock.py -q`

Expected: webhook removal preserves pending updates, a second runtime is refused, and lock loss stops work.

- [ ] **Step 5: Commit the preflight/lock checkpoint.**

Run: `git add friendly-bot/src/friendly_bot/telegram/runtime_lock.py friendly-bot/src/friendly_bot/telegram/preflight.py friendly-bot/tests/unit/telegram/test_preflight.py friendly-bot/tests/integration/telegram/test_runtime_lock.py && git commit -m "feat: guard Telegram polling runtime"`

### Task 3: Durable ordered polling and update deduplication

**Files:**
- Create: `friendly-bot/src/friendly_bot/telegram/poller.py`
- Create: `friendly-bot/tests/integration/telegram/test_poller.py`

**Interfaces:**
- Produces: `TelegramPoller.run_once() -> PollResult` and `TelegramIngress.process(update, received_at) -> ProcessedUpdate`.
- Consumes: F01 `UnitOfWork`, `UpdateRepository.claim_update`, poll-state offset storage, `ConversationRepository`, user lock, and Task 1 gateway.

- [ ] **Step 1: Write failing transaction/restart cases.**

```python
async def test_committed_duplicate_is_a_noop_and_offset_is_monotonic(poller: TelegramPoller) -> None:
    await poller.process_one(update(71)); await poller.process_one(update(71))
    assert await poller.polling_offset() == 72
    assert await persisted_message_count() == 1

async def test_precommit_failure_replays_without_cursor_advance(poller: TelegramPoller) -> None:
    with pytest.raises(IngressFailure): await poller.process_one(update(9))
    assert await poller.polling_offset() == 0
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/integration/telegram/test_poller.py -q`

Expected: FAIL because durable polling is absent.

- [ ] **Step 3: Implement one-update transactional processing.**

```python
async with self._uow_factory() as uow:
    if not await uow.updates.claim_update(update.update_id, received_at=now):
        await uow.poll_state.advance_monotonically(update.update_id + 1); return ProcessedUpdate.duplicate()
    user = await uow.users.resolve_telegram_sender(update.message.sender.id)
    await uow.lock_user(user.id)
    await uow.conversations.record_incoming(user_id=user.id, source_message_id=update.message.message_id, body=update.message.text, occurred_at=update.message.sent_at)
    await self._dispatcher.dispatch(user_id=user.id, incoming=update.message, unit_of_work=uow)
    await uow.poll_state.advance_monotonically(update.update_id + 1)
```

Process sorted updates in order and commit before fetching again. Unsupported/no-message updates safely claim and advance only. A dispatcher failure rolls back claim, message, outbox work, and offset together.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/integration/telegram/test_poller.py -q`

Expected: duplicate, rollback, ordered batch, callback/reply persistence, and restart cases pass.

- [ ] **Step 5: Commit durable polling.**

Run: `git add friendly-bot/src/friendly_bot/telegram/poller.py friendly-bot/tests/integration/telegram/test_poller.py && git commit -m "feat: persist Telegram polling cursor and updates"`

### Task 4: Durable outbound delivery state machine

**Files:**
- Create: `friendly-bot/src/friendly_bot/telegram/outbox.py`
- Create: `friendly-bot/tests/unit/telegram/test_outbox.py`, `tests/integration/telegram/test_outbox.py`

**Interfaces:**
- Produces: `OutboundDeliveryWorker.run_once() -> bool`, `NewOutboundDelivery`, durable delivery statuses `pending`, `retry`, `sent`, `rejected`, `uncertain`.
- Consumes: F01 `DeliveryRepository`, Task 1 `TelegramGateway` and typed send outcomes.

- [ ] **Step 1: Write failing definite-versus-ambiguous send tests.**

```python
@pytest.mark.parametrize("outcome, expected", [(TelegramSendRetry(503), "retry"), (TelegramSendRejected(403), "rejected"), (TelegramSendUncertain("telegram_transport_error"), "uncertain")])
async def test_only_parsed_definite_non_sends_retry(outcome: TelegramSendOutcome, expected: str) -> None:
    await worker.run_once(); assert await delivery_status() == expected

async def test_restart_never_replays_started_uncertain_delivery() -> None:
    await worker.run_once(); assert not await restarted_worker.run_once()
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_outbox.py tests/integration/telegram/test_outbox.py -q`

Expected: FAIL because the outbox worker is absent.

- [ ] **Step 3: Implement claim, attempt, and terminal transitions.**

```python
claim = await uow.deliveries.claim_next_safe(now=now)
if claim is None: return False
attempt = await uow.deliveries.start_attempt(claim.id, correlation_id=correlation_id)
outcome = await self._gateway.send(claim.message)
await uow.deliveries.finish_attempt(claim.id, attempt.id, outcome=classify(outcome), now=now)
```

Persist `uncertain` before returning and never place it back in pending. Honour 429 retry-after and finite backoff for 5xx only; record sanitized reason codes, not response bodies.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram/test_outbox.py tests/integration/telegram/test_outbox.py -q`

Expected: exactly one network call per claimed attempt; retry, rejection, uncertainty, 429 pause, and restart hold cases pass.

- [ ] **Step 5: Commit outbound semantics.**

Run: `git add friendly-bot/src/friendly_bot/telegram/outbox.py friendly-bot/tests/unit/telegram/test_outbox.py friendly-bot/tests/integration/telegram/test_outbox.py && git commit -m "feat: add durable Telegram outbox delivery"`

### Task 5: Onboarding and operational-account application services

**Files:**
- Create: `friendly-bot/src/friendly_bot/onboarding/__init__.py`, `service.py`, `accounts.py`
- Create: `friendly-bot/tests/unit/onboarding/test_service.py`, `tests/integration/onboarding/test_accounts.py`

**Interfaces:**
- Produces: `OnboardingService.handle`, `OperationalAccountService.login`, `.manage`, `.logout`, `LoginResult`.
- Consumes: F01 users/profiles/logins/conversation repositories and T02 outbound enqueue port; it returns configured-flow outcome data, not rendered fallback flows.

- [ ] **Step 1: Write failing onboarding/login exclusivity examples.**

```python
async def test_unknown_start_captures_next_name_exactly() -> None:
    await onboarding.handle(message("/start")); await onboarding.handle(message("Alex  Tan "))
    assert await display_name() == "Alex  Tan "

async def test_operational_profile_cannot_attach_to_two_telegram_accounts() -> None:
    assert (await accounts.login(chat(1), "Jordan", DOB)).kind == "attached"
    assert (await accounts.login(chat(2), "Jordan", DOB)).kind == "occupied"
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/onboarding/test_service.py tests/integration/onboarding/test_accounts.py -q`

Expected: FAIL because onboarding modules are absent.

- [ ] **Step 3: Implement transaction-scoped user/account decisions.**

```python
async def logout(self, telegram_user_id: int, *, now: datetime) -> LoginResult:
    async with self._uow_factory() as uow:
        user = await uow.users.require_by_telegram_id(telegram_user_id)
        await uow.lock_user(user.id)
        return await uow.operational_logins.detach_for_user(user.id, at=now)
```

Store first-login interests only through the configured capture path; `/manage` exposes edit-interests state; logout detaches and never deletes profile/history. Do not reveal the occupying account.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/onboarding/test_service.py tests/integration/onboarding/test_accounts.py -q`

Expected: unknown/existing paths, exact-name capture, first login, manage, exclusive attachment, and logout pass.

- [ ] **Step 5: Commit onboarding/accounts.**

Run: `git add friendly-bot/src/friendly_bot/onboarding friendly-bot/tests/unit/onboarding/test_service.py friendly-bot/tests/integration/onboarding/test_accounts.py && git commit -m "feat: add Friendly Bot onboarding and accounts"`

### Task 6: Attendance resolution and interaction expiry

**Files:**
- Create: `friendly-bot/src/friendly_bot/services/__init__.py`, `attendance.py`, `lifecycle.py`
- Create: `friendly-bot/tests/unit/services/test_attendance.py`, `tests/integration/services/test_lifecycle.py`

**Interfaces:**
- Produces: `ServiceAttendanceService.resolve_for_new_nbnc`, `.select_service`, and `ServiceLifecycleService.end_interactions`.
- Consumes: F01 services/attendance/open selections/UoW and `SelectionTransitionEngine`; it emits `service_attendance.selected`, `.choice_required`, `.latecomer`, `.none_available`, or `.ended`.

- [ ] **Step 1: Write failing lifecycle matrix tests.**

```python
@pytest.mark.parametrize("services, now, expected", [(one_highkey(), DOORS_OPEN, "selected"), (two_with_highkey(), DOORS_OPEN, "choice_required"), (one_lowkey(), DOORS_OPEN, "choice_required"), (one_highkey(), DOORS_CLOSE + MINUTE, "latecomer"), (one_highkey(), INTERACTION_END, "ended")])
async def test_attendance_resolution(services: list[Service], now: datetime, expected: str) -> None:
    outcome = await attendance.resolve_for_new_nbnc(USER_ID, services=services, now=now)
    assert outcome.kind == expected

async def test_old_check_in_button_after_doors_close_is_latecomer_not_ordinary() -> None:
    outcome = await attendance.select_service(USER_ID, ZONE_X_ID, now=DOORS_CLOSE + MINUTE)
    assert outcome.kind == "latecomer"
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/services/test_attendance.py tests/integration/services/test_lifecycle.py -q`

Expected: FAIL because service modules are absent.

- [ ] **Step 3: Implement guarded attendance and expiry decisions.**

```python
if now >= service.interaction_ends_at: return AttendanceOutcome.ended(service.id)
if now >= service.doors_close_at:
    return AttendanceOutcome.latecomer(service.id)
return await self._attend_with_overlap_lock(user_id=user_id, service=service, status="ordinary", now=now)
```

At end, call `expire_service_bound(service.id, at=now)`, request release of only service-bound match capacity through the typed matching port, and ask F01 engine for system-checkpoint return. Preserve attendance/conversation history.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/services/test_attendance.py tests/integration/services/test_lifecycle.py -q`

Expected: highkey, lowkey, overlap, latecomer, stale-button, ended, expiry, and history-preservation cases pass.

- [ ] **Step 5: Commit attendance/lifecycle.**

Run: `git add friendly-bot/src/friendly_bot/services/__init__.py friendly-bot/src/friendly_bot/services/attendance.py friendly-bot/src/friendly_bot/services/lifecycle.py friendly-bot/tests/unit/services/test_attendance.py friendly-bot/tests/integration/services/test_lifecycle.py && git commit -m "feat: add service attendance lifecycle"`

### Task 7: Timestamp scheduler, audience expansion, and catch-up

**Files:**
- Create: `friendly-bot/src/friendly_bot/services/scheduler.py`
- Create: `friendly-bot/tests/unit/services/test_scheduler.py`, `tests/integration/services/test_scheduler.py`

**Interfaces:**
- Produces: `ServiceDeliveryScheduler.run_once(now) -> SchedulerRunResult`, `AudienceResolver.resolve(audience, service_id) -> list[UUID]`.
- Consumes: F01 timestamp/delivery repositories, open-selection engine, outbox enqueue port, and Task 6 lifecycle predicate.

- [ ] **Step 1: Write failing audience/idempotency/catch-up tests.**

```python
async def test_leader_audience_includes_staff_but_not_admin_only() -> None:
    assert await resolver.resolve(ServiceAudience.ALL_LEADERS, None) == [LEADER, STAFF]

async def test_concurrent_scheduler_runs_create_one_claim_and_one_delivery() -> None:
    await asyncio.gather(scheduler.run_once(now=NOW), scheduler.run_once(now=NOW))
    assert await timestamp_claim_count(TIMESTAMP, NBNC) == 1

async def test_restart_catches_up_due_timestamp_without_closing_other_current_branch() -> None:
    await restarted_scheduler.run_once(now=NOW)
    assert await current_parent_keys(USER_ID) == {"onboarding.name_capture", "service.zone_x.timestamp.service_questions"}
```

- [ ] **Step 2: Run the red test.**

Run: `cd friendly-bot && uv run pytest tests/unit/services/test_scheduler.py tests/integration/services/test_scheduler.py -q`

Expected: FAIL because scheduler module is absent.

- [ ] **Step 3: Implement claim-before-selection-and-outbox scheduling.**

```python
for user_id in await self._audiences.resolve(timestamp.audience, timestamp.service_id):
    async with self._uow_factory() as uow:
        if not await uow.deliveries.claim_timestamp_delivery(timestamp.id, user_id): continue
        await self._timestamp_roots.open_for_recipient(uow, timestamp, user_id, now=now)
        await uow.deliveries.enqueue(NewOutboundDelivery.for_timestamp(timestamp, user_id))
```

Expand all seven approved audiences with role inheritance; do not grant membership from admin alone. Skip work at the applicable interaction end, yet deliver eligible overdue work after restart. Timestamp root selection adds an independent current branch.

- [ ] **Step 4: Run focused green tests.**

Run: `cd friendly-bot && uv run pytest tests/unit/services/test_scheduler.py tests/integration/services/test_scheduler.py -q`

Expected: all audiences, deadline skip, overdue catch-up, concurrent one-winner claim, and independent checkpoint cases pass.

- [ ] **Step 5: Commit scheduler delivery.**

Run: `git add friendly-bot/src/friendly_bot/services/scheduler.py friendly-bot/tests/unit/services/test_scheduler.py friendly-bot/tests/integration/services/test_scheduler.py && git commit -m "feat: schedule service timestamp delivery"`

### Task 8: T02 contract verification and G2 handoff

**Files:**
- Modify: `friendly-bot/tests/integration/telegram/test_poller.py`, `test_outbox.py`, `tests/integration/services/test_scheduler.py`
- Create: `friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/t02-execution.json`

**Interfaces:**
- Produces: G2-focused evidence and stable T02 imports for I04: `TelegramGateway`, `TelegramPoller`, `OutboundDeliveryWorker`, `OnboardingService`, `OperationalAccountService`, `ServiceAttendanceService`, `ServiceDeliveryScheduler`, `ServiceLifecycleService`.
- Consumes: Tasks 1–7 and current F01 G1 exports.

- [ ] **Step 1: Add final cross-boundary regression examples.**

```python
async def test_restart_does_not_duplicate_update_timestamp_or_ambiguous_send() -> None:
    await first_runtime.run_once(); await restarted_runtime.run_once()
    assert await processed_update_count() == 1
    assert await timestamp_delivery_count() == 1
    assert await uncertain_send_call_count() == 1

async def test_service_start_timestamp_keeps_pending_onboarding_branch_open() -> None:
    await scheduler.run_once(now=SERVICE_START)
    assert await current_parent_keys(USER_ID) == {"onboarding.name_capture", "service.zone_x.timestamp.service_questions"}
```

- [ ] **Step 2: Run the focused test to reveal any missing integration.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram tests/integration/telegram tests/unit/onboarding tests/integration/onboarding tests/unit/services tests/integration/services -q`

Expected: a failure identifies missing T02 durability, lifecycle, or contract behavior before repair.

- [ ] **Step 3: Make only evidence-driven repairs.**

```python
# Required invariant after repair:
assert delivery.status == "uncertain"  # never returned to a retry queue
assert selection.branch_id != onboarding_branch_id  # timestamp preserves the other branch
```

Do not add a webhook, configured default-error flow, tuition policy, schema change, action registry, seed, legacy adapter, or permissive retry fallback.

- [ ] **Step 4: Run complete T02 verification once.**

Run: `cd friendly-bot && uv run pytest tests/unit/telegram tests/integration/telegram tests/unit/onboarding tests/integration/onboarding tests/unit/services tests/integration/services -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot`

Expected: every command exits 0. Record commands, results, F01/G1 commit, T02 remote commit, and redacted runtime endpoint in the execution manifest.

- [ ] **Step 5: Commit final execution evidence.**

Run: `git add friendly-bot/tests/integration/telegram/test_poller.py friendly-bot/tests/integration/telegram/test_outbox.py friendly-bot/tests/integration/services/test_scheduler.py friendly-bot/docs/workers/2026-09-12-friendly-bot-mvp/completion-manifest/t02-execution.json && git commit -m "test: verify Telegram service delivery evidence"`

## Plan Self-Review

Tasks 1–4 cover the entire typed Telegram transport, preflight, single-runtime, poll-state/deduplication, and outbound safety contract. Task 5 covers onboarding and operational account exclusivity. Tasks 6–7 cover highkey/lowkey/latecomer/expiry and all approved timestamp audiences/catch-up/idempotency. Task 8 proves restart behavior and the T02-to-I04 import handoff. Every task includes a concrete failing test, focused red command, implementation shape, focused green command, and exact commit command. No task creates F01/R03/I04-owned code or an alternate persistence/flow path.
