# T02 Telegram and Service Delivery Design

| Field | Value |
| --- | --- |
| Status | Planning complete; execution blocked pending `PG` and `G1` |
| Date | 2026-09-12 |
| Owner | T02 |
| Authority inputs | Product specification, architecture masterplan, approved MVP design, Zone X fixture, implementation masterplan, EdenMind transport reference, and F01 exact persistence-contract handoff at `origin/main` `9b5b4e5c7fef5b3a215572cc3f74174590dc5a2d` |
| Scope | Telegram typed transport, webhook clearing, durable polling/outbound semantics, onboarding/login/manage/logout application services, service attendance, timestamp scheduling, audiences, catch-up, and expiry |
| Excluded ownership | Schema/migrations and generic flow state engine (F01); OpenRouter, persona, and matching (R03); action registry, runtime composition, CLI, and Zone X JSON seed (I04); living authorities |

## 1. Purpose and settled boundaries

T02 turns the approved local Telegram and service-delivery behavior into isolated, testable application packages. The process is a single local long-polling owner; it clears a pre-existing webhook before `getUpdates`, uses no webhook ingress, and stores all cursor, update, outbox, attempt, service attendance, and timestamp-claim state in F01's PostgreSQL boundary. Telegram is a typed wire boundary: raw payloads, response bodies, bot tokens, and URLs containing tokens are never retained or logged.

This package adapts reliability concepts from EdenMind's `tuition/telegram.py`: strict parse-or-uncertain responses, caller-owned offsets, a webhook preflight, error classification, and no replay after an ambiguous send. It deliberately does **not** import EdenMind code or reproduce its tuition records, enrolment, owner commands, multipart delivery, or tuition-specific policy.

The product and architecture masterplans already settle the observable behavior in this scope. This dated design introduces module-level delivery detail only; it has **no product-specification impact** and requires no living-authority edit.

## 2. Reconciled F01 consumed contracts

F01's published planning handoff removes the provisional interface names used during early T02 analysis. T02 will consume the following contracts after F01 has implemented `G1`; it does not create tables, sessions, migrations, or substitute repositories.

| F01 contract | T02 use |
| --- | --- |
| `persistence.uow.UnitOfWork` | One transaction per claimed update, per-user state change, timestamp recipient claim, or outbound attempt; its exception path rolls back. |
| `UpdateRepository.claim_update(telegram_update_id: int, *, received_at: datetime) -> bool`; `PollStateRepository.get() -> PollStateRecord`; `.advance_monotonically(next_update_offset: int, *, at: datetime) -> PollStateRecord` | Claim duplicate protection before business processing and advance the durable cursor only in the same successful commit. |
| `DeliveryRepository.claim_timestamp_delivery(service_timestamp_id: UUID, user_id: UUID) -> bool`; `.enqueue(delivery: NewOutboundDelivery) -> OutboundDeliveryRecord`; `.claim_next_safe(*, now: datetime) -> OutboundDeliveryRecord | None`; `.renew_claim(delivery_id, *, claim_token, now) -> OutboundDeliveryRecord`; `.start_attempt(delivery_id, correlation_id, *, claim_token, started_at) -> DeliveryAttemptRecord`; `.finish_attempt(delivery_id, attempt_id, outcome, *, now, retry_at=None, safe_error=None, confirmed_telegram_message_id=None) -> None` | Claim in one committed UoW; it supplies immutable provider-neutral `claim.message` plus opaque `claim_token`/expiry. Map that message only at T02's Telegram boundary, then start in a new committed UoW with the token before sending. `DeliveryClaimLostError` means no send. Renew only a live matching token; finish in a final UoW with exact parsed retry timing/safe result data. No ORM row, session, migration, or persistence shim. |
| `OpenSelectionRepository.expire_service_bound(service_id: UUID, *, at: datetime) -> int`; `.apply(transition: SelectionTransition | CheckpointReturnTransition) -> None` | Expire Zone X-bound selections at `interaction_ends_at` and apply F01-owned transitions without inferring expiry from memory. |
| `SelectionTransitionEngine` plus `OpenSelectionRepository.apply(...)` | T02 asks F01's generic engine to enter or return flow selections; it does not reimplement checkpoint traversal. |
| `UserRepository.resolve_telegram_sender(telegram_user_id: int, *, received_at: datetime) -> UserRecord`; `.require_by_telegram_id(telegram_user_id: int) -> UserRecord`; `.set_display_name(user_id: UUID, display_name: str, *, at: datetime) -> UserRecord`; `OperationalProfileRepository.find_by_login_identity(normalized_name: str, dob: date) -> OperationalProfileRecord | None`; `.update_interests(profile_id: UUID, interests: list[str], *, at: datetime) -> OperationalProfileRecord`; `OperationalLoginRepository.attach(profile_id: UUID, user_id: UUID, *, at: datetime) -> LoginAttachmentResult`; `.detach_for_user(user_id: UUID, *, at: datetime) -> OperationalLoginRecord | None` | Resolve users, preserve exact names, and atomically attach/detach operational logins. `UserRecord.role` is the sole operational-role source; `OperationalProfileRecord` has no role field. |
| `ServiceRepository.list_ongoing(*, now: datetime) -> list[ServiceRecord]`; `.list_due_timestamps(*, now: datetime) -> list[ServiceTimestampRecord]`; `.list_audience_user_ids(audience: ServiceAudience, service_id: UUID | None, *, now: datetime) -> list[UUID]`; `AttendanceRepository.start_or_switch(user_id: UUID, service_id: UUID, *, attendee_kind: str, started_at: datetime) -> AttendanceStartResult`; `.active_for_user(user_id: UUID, *, now: datetime) -> AttendanceRecord | None`; `.end_active_for_service(service_id: UUID, *, ended_at: datetime) -> int` | Resolve attendance, audiences, due work, switching, and service end. |
| `ConversationRepository.record_incoming(*, user_id: UUID, source_message_id: int, body: str, replied_to_body: str | None, occurred_at: datetime) -> ConversationMessageRecord`; `DiagnosticRepository.record(*, correlation_id: UUID, severity: str, safe_summary: str, safe_context: dict[str, JsonValue], at: datetime) -> DiagnosticRecord`; `.enqueue_admin_notifications(diagnostic_id: UUID, *, at: datetime) -> int` | Persist normalized inbound context and sanitized diagnostic fan-out. |

These are F01's exact published protocols. T02 consumes them directly after G1 and never reads ORM rows, creates a persistence shim, or duplicates a repository method. The resulting T02 package exports the DTOs below for I04 composition.

```python
class TelegramGateway(Protocol):
    async def clear_webhook(self) -> TelegramWebhookCleared | TelegramApiFailure: ...
    async def get_updates(self, *, offset: int, timeout_seconds: int) -> TelegramUpdates | TelegramApiFailure: ...
    async def send(self, request: OutboundTelegramMessage) -> TelegramSendOutcome: ...

class TelegramIngress(Protocol):
    async def process(self, update: IncomingTelegramUpdate, *, received_at: datetime) -> ProcessedUpdate: ...

class ServiceDeliveryScheduler(Protocol):
    async def run_once(self, *, now: datetime) -> SchedulerRunResult: ...

class OnboardingService(Protocol):
    async def handle(self, message: IncomingTelegramMessage, *, now: datetime) -> OnboardingResult: ...
```

`I04` composes these contracts with F01's flow engine and R03's router/matching APIs. T02 never imports `app.py`, action-registry code, or the Zone X JSON seed.

## 3. Telegram wire and long-polling design

`telegram.models` contains frozen, minimal DTOs: `TelegramUser(id)`, `TelegramChat(id, kind)`, `TelegramMessage(message_id, sent_at, chat, sender, text, reply_text, callback_data)`, `IncomingTelegramUpdate(update_id, message | None)`, and `TelegramUpdates(tuple[...])`. A parser accepts only the fields required by Friendly Bot; malformed envelopes and unsupported field types return `TelegramResponseUncertain("telegram_response_malformed")`, never a partially trusted update. Commands normalize `/login@friendly_bot` to `/login` before flow matching. Private-chat and positive-integer checks happen at the transport boundary.

`TelegramApiClient` uses direct `httpx.AsyncClient` calls, no generic HTTP retry middleware, and timeouts with a long-poll read margin. It exposes `getWebhookInfo`, `deleteWebhook(drop_pending_updates=False)`, `getUpdates(offset, timeout, allowed_updates=["message", "callback_query"])`, and `sendMessage`. Webhook clearing is idempotent when Telegram reports no webhook. A configured webhook that cannot be read or cleared is a startup failure; polling must not race it. Transport logs only method class, outcome code, correlation ID, and redacted identifiers—never payload text, raw JSON, Bot API endpoint, or token.

The poller obtains the singleton durable offset, calls the gateway with that offset, and processes updates in ascending `update_id`. Within a `UnitOfWork`, it claims the update, locks the resolved Friendly Bot user before conversation/service work, persists the normalized inbound message, calls the application dispatcher, queues outbound work, records the next offset as `update_id + 1`, and commits. A duplicate update is a committed no-op except that the cursor advances monotonically; a failure before commit leaves both claim and offset uncommitted for safe reprocessing. The poller never moves the cursor backwards and never skips a returned update.

One PostgreSQL advisory/session runtime lock guards the complete poller/scheduler/outbox runtime. Losing that connection stops future polling, timestamp claims, and sends rather than reacquiring silently. This is single-runtime ownership, not a process-memory mutex.

## 4. Durable outbound semantics

Every user-facing send is first a `NewOutboundDelivery` with an operation-derived idempotency key and immutable JSON payload. The worker commits `claim_next_safe(now)` before mapping immutable provider-neutral `claim.message` to a Telegram request. In a new transaction it calls token-fenced `start_attempt(claim.id, correlation_id, claim_token=claim.claim_token, started_at=...)` and commits before crossing the Telegram network boundary. A `DeliveryClaimLostError` produces no send. The final transaction calls `finish_attempt` with exact parsed retry timing, sanitized error, and confirmed message ID where known; `sending` and `uncertain` are never automatically replayed.

| Telegram outcome | Durable disposition |
| --- | --- |
| Parsed successful response with positive `message_id` | Mark delivery `sent`, record message ID and completed attempt. |
| Parsed 429 with positive `retry_after` | Mark retryable with that bounded next-at; apply the safe account-wide pause before another send. |
| Parsed 5xx or a parsed definite temporary failure | Mark retryable with bounded backoff and attempt count. |
| Parsed permanent 4xx rejection | Mark terminal `rejected`; emit sanitized diagnostics when policy requires it. |
| Timeout, transport exception after request start, malformed envelope, impossible body/status combination, or cancellation during send | Mark `uncertain`/held and do **not** replay automatically. |

Retries happen only for parsed definite non-sends, have a configured finite maximum, and preserve the same idempotency key. An uncertain delivery is visible to diagnostics/admin recovery but cannot be blindly replayed because Telegram might already have delivered it. Restart recovery reacquires only expired **unstarted** safe claims; a started/uncertain claim stays held. Delivery ordering is scoped by recipient cadence when required, but no tuition multipart semantics are introduced.

## 5. Onboarding and operational account application services

Incoming command behavior enters only through a configured F01 `CommandDiscussionFlowTrigger`; T02 recognizes a normalized command merely to supply it to the flow/application dispatcher. There is no hardcoded `/cancel` behavior.

`OnboardingService` handles an unknown private Telegram user receiving `/start` or ordinary text: queue the approved welcome, ask “How may I address you?”, then store the next text exactly as the display name and resolve service attendance. Existing `/start` queues the configured welcome-back choices; a selected name-change path stores the next reply exactly. The service does not send flow prose itself—configured flow actions do—except for fixed transport error/expiry responses already approved by product behavior.

`OperationalAccountService` implements the configured login journey. It matches the prefilled normalized name and DOB, atomically attaches the Telegram identity only if the operational profile is unoccupied, and records `operational_logins`. A profile attached to another Telegram account fails without revealing which account. First successful operational-user login—server, leader, or staff, read from `UserRecord.role` rather than an operational-profile role—opens the configured interests/topics capture; `/manage` exposes configured “Edit interests”; `/logout` detaches only the Telegram attachment and login record, never deletes the shared identity/profile. Unique F01 constraints plus locked repository mutation, rather than an in-memory check, guarantee one Telegram account per operational login and vice versa.

## 6. Service attendance, lifecycle, and expiry

`ServiceAttendanceService.resolve_for_new_nbnc` reads services using their administrative timezone and accepts only the following settled outcomes:

1. Exactly one ongoing highkey service before doors close: atomically enroll as ordinary attendance and enter its service checkpoint.
2. Multiple ongoing services with any highkey: emit the configured forced service-choice outcome; no “none” option.
3. Only lowkey ongoing services: emit the configured confirmation/choice outcome.
4. Doors closed but before `interaction_ends_at`: confirmation enrolls with latecomer status and invokes the selected service's `latecomer_flow`.
5. At or after `interaction_ends_at`: emit `service_attendance.ended`; do not create attendance or service-bound selections.

Only one overlapping active service may exist for an NBNC. Switching preserves historical attendance by ending the prior active overlap and activates the selected service under the F01 user lock/constraint. The old Zone X check-in button calls the same resolver at click time, so it follows latecomer or ended behavior rather than recording stale ordinary attendance.

At interaction end, `ServiceLifecycleService.end_interactions` expires service-bound selections, releases service-bound matching capacity through R03's later implementation contract, returns the user to the system checkpoint through F01's engine, and causes any old service button to enqueue the fixed “Sorry, the service is over!” response. Conversation history remains retained. New timestamp scheduling and attendance for that service stop at the boundary; attendees can still use non-service system paths.

## 7. Timestamp scheduler, audiences, catch-up, and idempotency

`ServiceDeliveryScheduler.run_once(now)` selects timestamps due at or before `now`, including overdue timestamps after a restart. It excludes timestamps/service interactions that are no longer legally deliverable at the applicable service boundary, expands one audience from the authoritative database, and for each recipient inserts the unique F01 timestamp-delivery claim before it creates the timestamp-root flow selection and outbox delivery. A conflict means another run owns that logical recipient delivery; no second send or selection is created.

Supported audience values are exactly `ALL_NBNCS`, `ALL_SERVERS`, `ALL_LEADERS`, `SERVICE_NBNCS`, `SERVICE_SERVERS`, `SERVICE_LEADERS`, and `ALL_SERVICE_ATTENDEES`. Leader membership includes staff; server membership includes leaders and staff; admin status alone grants no operational-audience membership. `ALL_NBNCS` marketing/reminder recipients need not attend. Timestamp roots create independent current branches while preserving an existing user prompt and that branch's checkpoint ancestry.

The Zone X fixture is the acceptance oracle: marketing, one-day reminder, doors open, service start, service end, thank-you, and interaction-end are separate claims. Doors-open check-in remains time-sensitive; `SERVICE_NBNCS` service-start reaches only attendees; existing attendees remain eligible after door close until interaction end. The scheduler does not copy Zone X seed data or action registration—that belongs to I04.

## 8. Verification and execution gate

No T02 product code is authorized until the coordinator's `PG` receipt and F01's implemented G1 evidence are both reachable from `origin/main`. The executor first rechecks F01's actual exports against Section 2, then runs each TDD task in the accompanying plan under the shared-main mutation lock. Focused tests cover strict parsing, webhook clearing, durable offset/duplicate recovery, definite-versus-ambiguous sends, runtime-lock loss, onboarding/login/manage/logout exclusivity, all service lifecycle branches, audience expansion, timestamp catch-up, and duplicate scheduler runs. The full T02 suite and static checks run once at the final execution checkpoint.
