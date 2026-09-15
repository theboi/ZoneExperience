# Multi-Intent Responses and Direct Telegram Delivery Design

**Status:** Approved for implementation planning

**Date:** 2026-09-15

## 1. Purpose

Friendly Bot must understand one or more requests in a typed Telegram message, answer every safe informational request it recognizes, and run interactive discussion flows without leaving the user inside multiple competing branches. Ordinary configured copy should vary naturally through model paraphrasing. Protected copy must remain exact. Telegram output should be sent directly after application state commits, without a durable outbound message queue.

This design replaces iterative key selection and the `system.done` sentinel with one model response containing every matched flow.

## 2. Product Decisions

1. A typed message means a Telegram text message entered by the user rather than a callback produced by an inline button.
2. One model request returns all matching discussion-flow keys at once.
3. `system.done` is removed because there is no iterative selection loop to terminate.
4. An informational flow can contribute an answer without opening a conversation branch.
5. At most one interactive discussion flow is active for a user at a time.
6. When one message matches multiple interactive flows, the highest-ranked match starts and the remaining matches are stored as ordered pending intents.
7. A valid pending intent resumes after the active interactive flow completes. At most one pending interactive flow resumes during one incoming update.
8. Pending intents are structured state. They are not stored inside the model-authored persona text.
9. `send_message` means model-paraphrased copy.
10. `send_message_fixed` means exact authored copy with local template rendering only.
11. Telegram messages are best effort. Application state and the Telegram polling offset commit before any send begins.
12. A failed or interrupted send is logged and discarded. It is not retried and must not crash the runtime.
13. Runtime lock loss, invalid startup configuration, explicit cancellation, and process termination remain fatal.
14. Official names and acronyms retain their approved casing. No output may use an em dash.

## 3. Goals

- Reduce normal typed-response latency to one successful OpenRouter operation.
- Recognize typed equivalents of button choices, including `any_of` triggers containing both button and message alternatives.
- Answer multiple informational questions from one user message.
- Serialize interactive conversations so the next user message has one clear branch context.
- Preserve deferred interactive requests as structured, bounded, expiring state.
- Produce casual, mostly lowercase youth-texting copy without changing facts or safety meaning.
- Preserve exact safety, error, contact, link, timing, status, and approved doctrinal copy.
- Remove outbound delivery rows, delivery attempts, leases, retry backoff, rate-limit pause state, and the outbox runtime loop.
- Keep scheduled-recipient claims so each timestamp is processed at most once per recipient.
- Log safe model, routing, action, and Telegram delivery failures in the terminal without logging secrets or user content.

## 4. Non-Goals

- Guaranteed Telegram delivery.
- Retrying a Telegram message after a definite rejection, rate limit, timeout, uncertain response, or process crash.
- Running two interactive branches concurrently for one user.
- Storing raw unanswered user messages as pending state.
- Asking the model to mutate conversation state.
- Enabling or redesigning automatic persona maintenance.
- Changing match-ranking behavior beyond work required to preserve current features.
- Replacing PostgreSQL, Colima, or the existing Compose project.

## 5. Domain Vocabulary

### 5.1 Flow match

A flow match is a model-selected stable `DiscussionFlow.key` that is present in the locally assembled candidate set.

### 5.2 Answer flow

An answer flow is explicitly authored with `multi_intent_mode: "answer"`. It may contain only `send_message`, `send_message_fixed`, `send_buttons`, or `send_photo` actions, has no children, and has no return actions. Its presentation can be included alongside other answers without opening or advancing a branch.

Answer flows are executed as response fragments. They do not call `SelectionTransitionEngine.select_child` and do not mutate open selections.

### 5.3 Interactive flow

An interactive flow is authored with `multi_intent_mode: "interactive"`, which is the safe default. It may contain stateful actions, buttons, event children, or further message choices. Selecting it uses the existing transition engine and may open or advance one branch.

### 5.4 Pending intent

A pending intent is an ordered reference to an interactive flow that matched a previous user message but could not start because another interactive match was chosen first.

It stores only local identifiers needed to reconstruct and revalidate the choice:

- `user_id`
- `flow_key`
- `flow_version_id`
- `service_id`, when applicable
- `position`
- `created_at`
- `expires_at`

It does not store the raw user message, model response, paraphrased copy, or persona text.

### 5.5 Reply slot

A reply slot is a deterministic local identifier for one reachable `send_message` template. A response plan maps each slot to validated paraphrased text. `send_message_fixed` never creates a reply slot.

### 5.6 Telegram presentation

A Telegram presentation is an immutable in-memory text or photo send request with its recipient and optional inline buttons. It exists only from action execution until the post-commit send attempt finishes.

## 6. Multi-Intent Routing

### 6.1 Candidate assembly

For every valid open selection, the candidate assembler examines each direct child. A child is eligible when its trigger is either:

- a direct `MessageDiscussionFlowTrigger`, or
- an `AnyOfDiscussionFlowTrigger` containing at least one `MessageDiscussionFlowTrigger`.

All message gists inside one `any_of` trigger are retained in order. The assembler emits one candidate per child key, not one candidate per gist. Button-only, command-only, automatic, and action-event triggers are excluded from typed routing.

Every local candidate retains its source selection, flow version, service scope, multi-intent mode, and reply-slot definitions. Only the stable key, gists, safe context label, multi-intent mode, and reply templates cross the model boundary.

### 6.2 One-shot model result

The routing gateway exposes one operation:

```python
class ResponseModel(Protocol):
    async def route_and_plan(
        self, request: MultiIntentRequest
    ) -> MultiIntentModelResult: ...

    async def plan_known_flow(
        self, request: KnownFlowRequest
    ) -> PlannedFlowMatch: ...
```

Typed routing returns either matches:

```json
{
  "kind": "matches",
  "matches": [
    {
      "flow_id": "system.global.menu.directions",
      "replies": [
        {
          "slot_id": "r0",
          "text": "the zone is at the star performing arts centre..."
        }
      ]
    },
    {
      "flow_id": "system.global.menu.expect",
      "replies": [
        {
          "slot_id": "r0",
          "text": "you can expect music, a message about jesus..."
        }
      ]
    }
  ]
}
```

or one terminal:

```json
{
  "kind": "terminal",
  "terminal": "no_match"
}
```

The only terminals are `no_match` and `clarify_ambiguous_context`. The response contains no `system.done` value.

The model orders matches by relevance. The gateway accepts at most five unique matches. Every returned flow key must be present in the request candidate set.

### 6.3 Local multi-intent policy

After validation, application code partitions matches into answer and interactive matches.

- Every answer match contributes its presentations in model order.
- Zero interactive matches means the application sends the collected answers and remains in the existing branch.
- One interactive match means the application sends collected answers and executes that interactive flow.
- More than one interactive match means the first match executes and the remaining matches are appended to the pending-intent queue.
- A global interruptive safety match takes priority, executes alone, and prevents all ordinary matches from executing or being queued.
- A duplicate flow key is a protocol failure.

The model proposes matches and paraphrases. It never decides which state transitions are legal.

### 6.4 Pending-intent lifecycle

Pending intents are ordered, unique per user and flow scope, limited to five rows per user, and expire after 24 hours.

Before resuming a pending intent, the application rebuilds the user's current candidate set and requires an exact match on `flow_key`, `flow_version_id`, and `service_id`. Missing, expired, or no-longer-eligible intents are deleted without execution.

When an interactive branch completes and returns to a checkpoint, the application pops the first valid pending intent and starts it. It resumes at most one pending intent per incoming update. This prevents unbounded chains and keeps outgoing messages understandable.

Starting, completing, expiring, or declining an intent removes its row. A new duplicate match refreshes its position and expiry rather than adding another row.

## 7. Reply Planning and Copy Safety

### 7.1 Action semantics

```python
class SendMessageAction(DiscussionActionBase):
    type: Literal["send_message"]
    text: NonEmptyText


class SendMessageFixedAction(DiscussionActionBase):
    type: Literal["send_message_fixed"]
    text: NonEmptyText
```

- `send_message` consumes a validated reply slot, then renders approved local template variables.
- `send_message_fixed` renders its authored template locally and sends it unchanged.
- An invalid or unavailable paraphrase falls back to the authored `send_message` template, records a sanitized diagnostic, and continues.
- `send_buttons.text`, button labels, `send_service_choice_buttons.text`, photo captions, contact-sharing actions, admin notifications, and human notifications remain exact unless a later specification explicitly changes them.

### 7.2 Reply-slot coverage

For each candidate, the planner discovers all reachable `send_message` actions that can run from that selection before the next user input. This includes:

- the selected child actions,
- direct action-event descendants,
- error descendants only when their copy is eligible for paraphrasing,
- checkpoint return actions reached by that selection.

The model returns every slot for each selected flow, including mutually exclusive action-event outcomes. Local execution consumes only the slots reached by actual events.

Known button, command, timestamp, onboarding, and root flows use `plan_known_flow` once when their immediate execution closure contains at least one `send_message`. A fixed-only flow performs no OpenRouter operation.

### 7.3 Stable prompt prefix

The first system message is a versioned, byte-stable instruction. All candidate keys, templates, persona content, history, and current user text appear only in the later user message. The response-format declaration is static and does not enumerate request-specific flow keys.

The fixed instruction requires the model to:

- return only the required JSON object,
- use casual, mostly lowercase youth-texting language,
- use abbreviations modestly and naturally,
- avoid exaggerated slang, phonetic misspellings, added emoji, and caricature,
- preserve every fact, qualification, instruction, intent, and safety meaning,
- preserve every template variable and URL exactly,
- add no facts, promises, contacts, links, or safety advice,
- preserve approved casing for official names and acronyms,
- never use an em dash.

OpenRouter receives `response_format: {"type": "json_object"}`, a small completion-token cap, and disabled reasoning when the configured model supports it. Qwen 3.7 Flash does not provide strict JSON-schema enforcement, so local validation remains mandatory.

### 7.4 Model-output validation

Validation rejects or falls back on:

- unknown top-level or nested fields,
- unknown or duplicate flow keys,
- more than five matches,
- unknown, missing, or duplicate reply slots,
- empty or oversized reply text,
- changed, missing, duplicated, or newly introduced template variables,
- changed, missing, reordered, or newly introduced URLs,
- unresolved `{{` or `}}` syntax,
- an em dash,
- a reply attached to a fixed-only flow,
- a terminal combined with matches.

Facts and tone cannot be proven mechanically. Protected text therefore uses `send_message_fixed`, and ordinary paraphrases require an evaluation corpus.

### 7.5 Fixed-copy migration policy

The following existing messages must become `send_message_fixed`:

- safety escalation and no-responder instructions,
- provider, routing, action, and Telegram error responses,
- messages containing URLs, maps, contact details, or precise directions,
- exact service times, doors-open times, service-over status, and expiry status,
- human-match availability and identity messages,
- approved doctrinal descriptions,
- operational and admin notifications.

Greetings, low-risk conversational questions, general expectations, and other content-owner-approved social copy may remain `send_message`.

## 8. Direct Telegram Delivery

### 8.1 Transaction boundary

Action execution appends immutable presentations to an in-memory `PresentationBuffer` shared by parent, child, and checkpoint contexts. It does not write outbound delivery rows.

For an incoming Telegram update:

1. Claim and record the update inside the existing unit of work.
2. Apply user, flow, attendance, matching, diagnostic, and pending-intent mutations.
3. Advance the Telegram polling offset.
4. Exit the unit of work successfully so all state commits.
5. Send buffered presentations sequentially through Telegram.
6. Log each unsuccessful send and continue with later presentations and later updates.

No Telegram network send occurs while the database transaction or user lock is open.

If the process stops after step 4 and before step 5, the presentation is lost. This is accepted product behavior.

### 8.2 Sender behavior

`BestEffortTelegramSender` converts typed in-memory presentations to the existing `OutboundTelegramMessage` and `OutboundTelegramPhoto` transport DTOs. It resolves photo assets immediately before sending.

For each presentation it performs exactly one `TelegramApiClient.send` call. It does not retry `TelegramSendRetry`, `TelegramSendRejected`, `TelegramSendUncertain`, transport exceptions, or cancellation.

Ordinary `Exception` values are reduced to stable terminal log categories. Raw exception text, Telegram payloads, user messages, model outputs, tokens, URLs, and contact details are not logged. `asyncio.CancelledError` propagates after the current send is abandoned.

### 8.3 Scheduled sends

`timestamp_delivery_claims` remains because it is scheduler business state that prevents a due timestamp from being processed every 30 seconds for the same recipient.

For each newly claimed recipient, the scheduler commits the timestamp claim and prepared application state, then directly sends the resulting in-memory presentations. A failed send remains claimed and is not retried.

The following durable delivery machinery is removed:

- `outbound_deliveries`
- `outbound_delivery_attempts`
- outbound claim leases
- delivery retry scheduling
- the global Telegram rate-limit pause
- `OutboundDeliveryWorker`
- `_outbox_forever`
- the runtime `outbox` dependency

Diagnostic records remain. `admin_notification_deliveries` is removed because it is also an unsent delivery queue. Diagnostics are emitted to sanitized terminal logs. Explicit configured admin messages continue as direct best-effort presentations.

## 9. Error Containment

Expected provider failures must not escape application dispatch.

- Typed routing transport or protocol failure records a sanitized diagnostic, returns fixed error copy, commits the polling offset, and continues.
- Known-flow paraphrase failure uses authored copy and continues.
- Invalid individual paraphrases use authored copy and continue when the selected flow key is valid.
- A local routing invariant failure records a sanitized diagnostic and returns fixed error copy.
- A Telegram send failure occurs after commit, logs a stable category, and continues.
- Scheduler recipient failures are isolated per recipient so later recipients still process.
- Poll, scheduler, and direct-send loop operations catch ordinary exceptions at their iteration boundaries and apply bounded backoff.

The runtime must not swallow `asyncio.CancelledError`, `KeyboardInterrupt`, `SystemExit`, invalid startup configuration, or `TelegramRuntimeLockLostError`.

## 10. Persistence Changes

A new `pending_flow_intents` table stores deferred interactive matches. It has foreign keys to user, flow version, and optional service records, plus ordered position and expiry fields. Repository operations require the existing user lock.

A new Alembic revision:

- creates `pending_flow_intents`,
- drops `outbound_delivery_attempts`,
- drops `outbound_deliveries`,
- drops `admin_notification_deliveries`,
- removes any database object used only by the global Telegram pause,
- preserves `timestamp_delivery_claims`.

Existing immutable published flow versions may contain protected `send_message` actions. The rollout must first add `send_message_fixed` while retaining exact legacy behavior, publish the audited seed, and invalidate existing open selections before enabling paraphrasing. This test deployment may clear its development database instead of migrating active selections. Production data must use an explicit open-selection invalidation or rebase operation.

## 11. Observability

Terminal logs and safe numeric metrics include:

- `routing.operation_ms`
- `routing.http_attempt_count`
- `routing.match_count`
- `routing.answer_match_count`
- `routing.interactive_match_count`
- `routing.pending_intent_count`
- `routing.paraphrase_fallback_count`
- `telegram.direct_send_ms`
- `telegram.direct_send_outcome`
- `runtime.poll_recovery_count`
- `runtime.scheduler_recovery_count`

Logs contain correlation IDs and stable reason codes only where identity is necessary. They never contain user-authored text, generated text, provider bodies, API tokens, URLs, or contact values.

## 12. Performance Requirements

- A successful typed update performs exactly one response-planning operation.
- A successful response-planning operation performs exactly one HTTP attempt unless a transport retry is required.
- No `system.done` follow-up request occurs.
- Fixed-only button, command, root, and timestamp paths perform no OpenRouter request.
- A typed selected flow does not require a second model operation for its immediate action-event or checkpoint-return replies.
- Warm p50 from update receipt to committed presentations is at most 1.5 seconds.
- Warm p95 from update receipt to committed presentations is at most 3.0 seconds.
- Direct Telegram sending begins immediately after commit, with no fixed 500 ms outbox polling delay.

## 13. Acceptance Scenarios

### 13.1 Two informational questions

Input: `where is zone x and what should i expect?`

Expected: one model request returns both answer-flow keys and both reply plans. The bot sends both answers without opening two branches.

### 13.2 Informational plus interactive

Input: `where is zone x and can someone meet me?`

Expected: the directions answer is sent and the connection flow becomes the only active interactive branch.

### 13.3 Two interactive questions

Input: `can someone meet me and can i change service?`

Expected: the first ranked interactive flow starts. The other is stored as one pending intent and resumes only after the active branch completes and the pending candidate remains valid.

### 13.4 Typed button equivalent

Input: `what options are there?`

Expected: the message gist inside the button-plus-message `any_of` trigger is included as a model candidate and selects the same flow as the button.

### 13.5 Invalid paraphrase

The model changes a URL or template variable.

Expected: invalid generated text is never sent. The original authored template is rendered, a sanitized fallback diagnostic is recorded, and the bot continues.

### 13.6 Telegram failure

Telegram times out after the state transaction commits.

Expected: one safe terminal log line is emitted, no retry row is created, and the poller processes the next update.

### 13.7 Runtime lock loss

The advisory lock connection is lost.

Expected: the process stops rather than risking two active Telegram pollers.

## 14. Verification Gates

- Domain, publication, routing, gateway, action, application, scheduler, polling, and migration tests pass.
- Full pytest suite passes from a fresh temporary directory.
- Ruff lint and format checks pass.
- Mypy passes for `src/friendly_bot`.
- Seed verification passes with every protected message classified as fixed.
- Prompt snapshots prove a byte-identical stable system prefix across different users and candidate sets.
- Fault-injection tests prove later updates and scheduler recipients continue after model and Telegram failures.
- A controlled live Telegram smoke test covers one typed multi-answer message, one typed interactive message, one button, one provider failure, and one direct-send failure.
