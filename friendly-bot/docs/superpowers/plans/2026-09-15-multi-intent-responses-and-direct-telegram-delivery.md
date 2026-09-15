# Multi-Intent Responses and Direct Telegram Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace iterative key-only routing with one-call multi-intent response planning, serialize interactive branches through structured pending intents, add paraphrased and fixed message actions, and send Telegram presentations directly after state commits without a durable message outbox.

**Architecture:** A response planner assembles model-safe candidates and deterministic reply slots, while OpenRouter returns all matched stable flow keys and paraphrases in one JSON object. Application policy executes every answer flow as a presentation fragment, executes at most one interactive flow, and queues additional interactive matches in a structured per-user table. Actions build immutable presentations in memory; ingress and scheduler transactions commit before a best-effort sender makes one Telegram attempt per presentation.

**Tech Stack:** Python 3.13, asyncio, Pydantic, SQLAlchemy 2, PostgreSQL, Alembic, httpx, pytest/pytest-asyncio, Ruff, mypy, Telegram Bot API, OpenRouter Chat Completions.

**Design Spec:** `docs/superpowers/specs/2026-09-15-multi-intent-responses-and-direct-telegram-delivery-design.md`

## Global Constraints

- Work directly on `main`; the user explicitly prohibited branches and worktrees.
- Do not use an em dash in source code, prompts, configured copy, logs, tests, documentation, or commit messages.
- `send_message` is paraphrased; `send_message_fixed` is exact.
- A successful typed update uses one response-planning operation and has no `system.done` request.
- At most one interactive flow may run for one user at a time.
- Pending intents store stable local flow references, never raw unanswered user text or model-generated persona prose.
- Commit database state and the polling offset before attempting a Telegram send.
- Telegram sends are best effort, attempted once, never retried, and never persisted in an outbound message queue.
- Preserve `timestamp_delivery_claims` so the scheduler does not repeat a timestamp for one recipient.
- Preserve one PostgreSQL advisory runtime lock; genuine runtime-lock loss remains fatal.
- Preserve privacy boundaries: logs contain no user text, generated text, provider bodies, tokens, URLs, or contact details.
- Use TDD for every task. Commit and push each independently verified task to `origin/main`.
- Do not alter the ignored local `.env` or print its values.

---

### Task 1: Add explicit answer-flow and fixed-copy domain semantics

**Files:**
- Create: `src/friendly_bot/domain/templates.py`
- Modify: `src/friendly_bot/domain/actions.py`
- Modify: `src/friendly_bot/domain/flows.py`
- Modify: `src/friendly_bot/domain/publication.py`
- Modify: `src/friendly_bot/actions/executors.py`
- Modify: `src/friendly_bot/actions/registry.py`
- Modify: `src/friendly_bot/actions/runner.py`
- Modify: `seeds/zone-x.json`
- Modify: `tests/unit/domain/test_publication.py`
- Modify: `tests/e2e/test_action_registry.py`
- Modify: `tests/e2e/test_zone_x_seed.py`

**Interfaces:**
- Consumes: existing `DiscussionAction`, `DiscussionFlow`, `ActionExecutorRegistry`, and local template rendering.
- Produces: `SendMessageFixedAction`, `MultiIntentMode`, shared template-token inspection, publication validation for answer flows, and an audited seed.

- [x] **Step 1: Write failing domain and publication tests**

Add exact parsing and publication tests for these target models:

```python
class SendMessageFixedAction(DiscussionActionBase):
    type: Literal["send_message_fixed"]
    text: NonEmptyText


class MultiIntentMode(StrEnum):
    ANSWER = "answer"
    INTERACTIVE = "interactive"
```

Assert that `DiscussionFlow.multi_intent_mode` defaults to `INTERACTIVE`. Assert publication rejects an answer flow with any non-presentation action, any child, or any return action. The only legal answer-flow action classes are `SendMessageAction`, `SendMessageFixedAction`, `SendButtonsAction`, and `SendPhotoAction`.

- [x] **Step 2: Run the domain tests and verify RED**

Run: `uv run pytest tests/unit/domain/test_publication.py tests/e2e/test_action_registry.py tests/e2e/test_zone_x_seed.py -q`

Expected: FAIL because the new action discriminator, mode, executor registration, and publication rules do not exist.

- [x] **Step 3: Extract template inspection and implement the closed models**

Move the shared regular expression into `domain/templates.py` and expose exact pure functions:

```python
TEMPLATE_TOKEN_PATTERN: re.Pattern[str]
URL_PATTERN: re.Pattern[str]


def template_tokens(text: str) -> tuple[str, ...]:
    """Return normalized template tokens in source order, including duplicates."""


def urls(text: str) -> tuple[str, ...]:
    """Return literal HTTP and HTTPS URLs in source order."""
```

Add `SendMessageFixedAction` to the discriminated union, register a `send_message_fixed` executor that calls `context.render(action.text)`, and include it in `_is_presentation_action`. Keep both `send_message` and `send_message_fixed` exact during this task so no published legacy copy changes behavior yet.

Add `multi_intent_mode: MultiIntentMode = MultiIntentMode.INTERACTIVE` to `DiscussionFlow`. Publication must enforce the answer-flow restrictions described in Step 1.

- [x] **Step 4: Audit and update the Zone X seed**

Mark these direct typed-answer nodes with `"multi_intent_mode": "answer"`:

```text
system.global.menu.timings
system.global.menu.directions
system.global.menu.expect
system.global.menu.zone
system.global.menu.connect
service.zone_x.what_to_expect
service.zone_x.service.toilet
service.zone_x.service.who_is_jesus
service.zone_x.service.unknown_question
```

Convert the following categories to `send_message_fixed`: safety and no-responder copy, service timings, all map and link messages, precise venue directions, match-found and match-unavailable status, service-over and attendance status, precise event times, toilet location, approved Jesus description, unknown-question approved copy, and the final interaction-expiry message. Keep greetings, general expectation copy, open-ended questions, and conversational acknowledgements as `send_message`.

Add seed assertions that every URL-bearing action and every safety subtree message is fixed.

- [x] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/unit/domain/test_publication.py tests/e2e/test_action_registry.py tests/e2e/test_zone_x_seed.py -q`

Expected: PASS with registry completeness and immutable publication hashing updated for the new fields.

- [x] **Step 6: Commit and push Task 1**

Run:

```bash
git add src/friendly_bot/domain src/friendly_bot/actions seeds/zone-x.json tests/unit/domain tests/e2e/test_action_registry.py tests/e2e/test_zone_x_seed.py
git commit -m "feat: classify answer flows and fixed copy"
git push origin main
```

### Task 2: Introduce immutable in-memory Telegram presentations

**Files:**
- Create: `src/friendly_bot/telegram/presentations.py`
- Create: `src/friendly_bot/telegram/sender.py`
- Create: `tests/unit/telegram/test_sender.py`
- Modify: `src/friendly_bot/telegram/__init__.py`
- Modify: `src/friendly_bot/telegram/models.py`

**Interfaces:**
- Consumes: `TelegramApiClient.send`, `TelegramAssetResolver`, `TelegramInlineButton`, and existing typed send outcomes.
- Produces: `TelegramPresentation`, `PresentationBuffer`, `BestEffortTelegramSender`, and `DirectSendResult`.

- [x] **Step 1: Write failing presentation and sender tests**

Test immutable text and photo presentations, ordered buffering, photo resolution, and one send attempt. Assert all failure outcomes are returned as stable categories and are not raised:

```python
result = await sender.send_all(
    (
        TelegramTextPresentation(chat_id=73, text="hello", buttons=()),
        TelegramTextPresentation(chat_id=73, text="again", buttons=()),
    )
)

assert result == DirectSendResult(attempted=2, confirmed=2, failed=0)
assert len(gateway.requests) == 2
```

Add cases for `TelegramSendRetry`, `TelegramSendRejected`, `TelegramSendUncertain`, `httpx.TransportError`, malformed assets, and cancellation. Cancellation propagates; every ordinary failure increments `failed` and permits the next presentation attempt.

- [x] **Step 2: Run sender tests and verify RED**

Run: `uv run pytest tests/unit/telegram/test_sender.py -q`

Expected: FAIL because direct presentation types and sender do not exist.

- [x] **Step 3: Implement the presentation boundary**

Use these concrete public shapes:

```python
@dataclass(frozen=True, slots=True)
class TelegramTextPresentation:
    chat_id: int
    text: str
    buttons: tuple[TelegramInlineButton, ...] = ()


@dataclass(frozen=True, slots=True)
class TelegramPhotoPresentation:
    chat_id: int
    asset_key: str
    caption: str
    buttons: tuple[TelegramInlineButton, ...] = ()


type TelegramPresentation = TelegramTextPresentation | TelegramPhotoPresentation


@dataclass(slots=True)
class PresentationBuffer:
    presentations: list[TelegramPresentation] = field(default_factory=list)

    def append(self, presentation: TelegramPresentation) -> None:
        self.presentations.append(presentation)

    def snapshot(self) -> tuple[TelegramPresentation, ...]:
        return tuple(self.presentations)


@dataclass(frozen=True, slots=True)
class DirectSendResult:
    attempted: int
    confirmed: int
    failed: int


class TelegramPresentationSender(Protocol):
    async def send_all(
        self, presentations: tuple[TelegramPresentation, ...]
    ) -> DirectSendResult: ...
```

`BestEffortTelegramSender.send_all` must make one send call per item, resolve a photo immediately before its call, generate only an ephemeral correlation key for the existing transport DTO, and log stable outcome codes through an injected logger. It must not sleep, retry, or retain a request.

- [x] **Step 4: Run sender tests and verify GREEN**

Run: `uv run pytest tests/unit/telegram/test_sender.py tests/unit/telegram/test_client.py -q`

Expected: PASS with existing Telegram response parsing unchanged.

- [x] **Step 5: Commit and push Task 2**

Run:

```bash
git add src/friendly_bot/telegram tests/unit/telegram/test_sender.py
git commit -m "feat: add best effort telegram sender"
git push origin main
```

### Task 3: Persist bounded structured pending intents

**Files:**
- Create: `alembic/versions/0005_pending_flow_intents.py`
- Create: `src/friendly_bot/intents/__init__.py`
- Create: `src/friendly_bot/intents/service.py`
- Create: `tests/unit/intents/test_service.py`
- Create: `tests/integration/persistence/test_pending_intents.py`
- Modify: `src/friendly_bot/persistence/models.py`
- Modify: `src/friendly_bot/persistence/repositories.py`
- Modify: `src/friendly_bot/persistence/uow.py`
- Modify: `tests/integration/persistence/test_migrations.py`
- Modify: `tests/integration/persistence/test_schema_constraints.py`

**Interfaces:**
- Consumes: the existing user lock, published flow-version identity, service identity, and unit-of-work pattern.
- Produces: `PendingFlowIntentRecord`, `NewPendingFlowIntent`, `PendingIntentRepository`, and `PendingIntentService`.

- [x] **Step 1: Write failing repository and service tests**

Cover ordered append, duplicate refresh, five-item cap, 24-hour expiry, user isolation, deletion, and required user locking. Use these records:

```python
@dataclass(frozen=True, slots=True)
class NewPendingFlowIntent:
    user_id: UUID
    flow_key: str
    flow_version_id: UUID
    service_id: UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PendingFlowIntentRecord(NewPendingFlowIntent):
    id: UUID
    position: int
```

Assert a duplicate `(user_id, flow_version_id, flow_key)` moves to the end and refreshes expiry rather than creating another row.

- [x] **Step 2: Run pending-intent tests and verify RED**

Run: `uv run pytest tests/unit/intents/test_service.py tests/integration/persistence/test_pending_intents.py -q`

Expected: FAIL because the table, repository, and service do not exist.

- [x] **Step 3: Add the table and repository**

Create `pending_flow_intents` with UUID primary key, foreign keys to `users`, `flow_versions`, and nullable `services`, integer `position`, timezone-aware `created_at` and `expires_at`, a unique constraint on `(user_id, flow_version_id, flow_key)`, and an index on `(user_id, position)`.

Expose repository methods:

```python
class PendingIntentRepository(Protocol):
    async def list_active(
        self, user_id: UUID, *, now: datetime
    ) -> list[PendingFlowIntentRecord]: ...

    async def append(
        self, intent: NewPendingFlowIntent, *, max_per_user: int
    ) -> PendingFlowIntentRecord: ...

    async def delete(self, intent_id: UUID) -> None: ...

    async def delete_expired(self, user_id: UUID, *, now: datetime) -> int: ...
```

All mutations verify that `UnitOfWork.lock_user(user_id)` has already run, matching open-selection mutation policy.

- [x] **Step 4: Implement the bounded service**

`PendingIntentService.enqueue` uses `expires_at = now + timedelta(hours=24)` and `max_per_user = 5`. When full, it drops the oldest active pending row before appending. `list_active` deletes expired rows first. `remove` deletes one exact record.

- [ ] **Step 5: Run persistence and migration tests and verify GREEN**

Run: `uv run pytest tests/unit/intents/test_service.py tests/integration/persistence/test_pending_intents.py tests/integration/persistence/test_migrations.py tests/integration/persistence/test_schema_constraints.py -q`

Expected: PASS, including upgrade and downgrade of revision `0005_pending_flow_intents`.

- [ ] **Step 6: Commit and push Task 3**

Run:

```bash
git add alembic/versions/0005_pending_flow_intents.py src/friendly_bot/intents src/friendly_bot/persistence tests/unit/intents tests/integration/persistence
git commit -m "feat: persist pending flow intents"
git push origin main
```

### Task 4: Assemble message candidates and deterministic reply slots

**Files:**
- Create: `src/friendly_bot/responses/__init__.py`
- Create: `src/friendly_bot/responses/planner.py`
- Create: `tests/unit/responses/test_planner.py`
- Modify: `src/friendly_bot/routing/contracts.py`
- Modify: `src/friendly_bot/routing/router.py`
- Modify: `tests/unit/routing/test_router.py`

**Interfaces:**
- Consumes: `DiscussionFlow`, `OpenSelectionState`, immutable published definitions, `MultiIntentMode`, and template-token inspection.
- Produces: `message_gists`, `ReplySlot`, `ReplySlotBinding`, `CandidateResponsePlan`, and expanded `RoutingCandidate` values.

- [x] **Step 1: Write failing candidate tests for `any_of`**

Add focused cases proving:

```python
assert message_gists(
    AnyOfDiscussionFlowTrigger(
        type="any_of",
        triggers=[
            ButtonDiscussionFlowTrigger(type="button", button_id="menu.directions"),
            MessageDiscussionFlowTrigger(type="message", llm_gist="asks for directions"),
        ],
    )
) == ("asks for directions",)
```

Also assert multiple message alternatives become one candidate with ordered gists, a button-only `any_of` is excluded, and duplicate child keys across open selections remain rejected.

- [x] **Step 2: Write failing reply-slot tests**

Given a selected child with one `send_message`, two mutually exclusive action-event children, and one checkpoint return message, assert the planner emits deterministic `r0`, `r1`, `r2`, and `r3` slots. Assert fixed messages create no slot and event-only cycles remain rejected by publication.

- [x] **Step 3: Run planner and router tests and verify RED**

Run: `uv run pytest tests/unit/responses/test_planner.py tests/unit/routing/test_router.py -q`

Expected: FAIL because `CandidateAssembler` still accepts only direct message triggers and reply planning does not exist.

- [x] **Step 4: Implement candidate and reply-slot types**

Use these prompt-safe types:

```python
class ReplyTemplateSlot(PromptDTO):
    slot_id: str = Field(pattern=r"^r[0-9]+$")
    template: str = Field(min_length=1)
    template_tokens: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()


class RoutingPromptCandidate(PromptDTO):
    flow_id: str = Field(min_length=1)
    gists: tuple[str, ...] = Field(min_length=1)
    context_label: str = Field(min_length=1)
    multi_intent_mode: Literal["answer", "interactive"]
    reply_slots: tuple[ReplyTemplateSlot, ...] = ()
```

Keep a local `ReplySlotBinding` that maps each model-visible slot to `(flow_key, action_index)` without exposing database UUIDs. Traverse only direct action-event descendants and the applicable checkpoint return closure. Preserve declaration order.

Use these immutable local plan types across routing and action execution:

```python
@dataclass(frozen=True, slots=True)
class ReplySlotBinding:
    slot_id: str
    flow_key: str
    action_index: int
    authored_template: str


@dataclass(frozen=True, slots=True)
class PlannedActionText:
    flow_key: str
    action_index: int
    text: str


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    messages: tuple[PlannedActionText, ...]

    def text_for(self, flow_key: str, action_index: int) -> str | None:
        matches = tuple(
            message.text
            for message in self.messages
            if message.flow_key == flow_key and message.action_index == action_index
        )
        if len(matches) > 1:
            raise ValueError("reply plan contains a duplicate action address")
        return matches[0] if matches else None
```

- [x] **Step 5: Run planner and router tests and verify GREEN**

Run: `uv run pytest tests/unit/responses/test_planner.py tests/unit/routing/test_router.py -q`

Expected: PASS with all eleven current `any_of` flow shapes covered.

- [x] **Step 6: Commit and push Task 4**

Run:

```bash
git add src/friendly_bot/responses src/friendly_bot/routing tests/unit/responses tests/unit/routing/test_router.py
git commit -m "feat: assemble multi intent reply candidates"
git push origin main
```

### Task 5: Replace the key-only gateway with validated one-shot JSON results

**Files:**
- Modify: `src/friendly_bot/routing/contracts.py`
- Modify: `src/friendly_bot/routing/openrouter_gateway.py`
- Modify: `src/friendly_bot/hyperparameters.py`
- Modify: `tests/unit/routing/test_openrouter_gateway.py`

**Interfaces:**
- Consumes: `RoutingPromptCandidate`, `ReplyTemplateSlot`, existing privacy attestation, model setting, provider setting, and bounded-response transport decoder.
- Produces: `MultiIntentRequest`, `KnownFlowRequest`, `PlannedReply`, `PlannedFlowMatch`, `MultiIntentMatches`, `MultiIntentTerminal`, `route_and_plan`, and `plan_known_flow`.

- [ ] **Step 1: Write failing strict-decoder tests**

Define target DTOs:

```python
class PlannedReply(PromptDTO):
    slot_id: str = Field(pattern=r"^r[0-9]+$")
    text: str = Field(min_length=1, max_length=4096)


class PlannedFlowMatch(PromptDTO):
    flow_id: str = Field(min_length=1)
    replies: tuple[PlannedReply, ...] = ()


class MultiIntentMatches(PromptDTO):
    kind: Literal["matches"]
    matches: tuple[PlannedFlowMatch, ...] = Field(min_length=1, max_length=5)


class MultiIntentTerminal(PromptDTO):
    kind: Literal["terminal"]
    terminal: Literal["no_match", "clarify_ambiguous_context"]


class MultiIntentRequest(PromptDTO):
    persona: str = ""
    messages: tuple[str, ...] = ()
    reply_body: str | None = None
    candidates: tuple[RoutingPromptCandidate, ...] = Field(min_length=1)


class KnownFlowRequest(PromptDTO):
    flow_id: str = Field(min_length=1)
    reply_slots: tuple[ReplyTemplateSlot, ...] = ()
    messages: tuple[str, ...] = ()
```

Test unknown fields, duplicate flow IDs, unknown flow IDs, more than five matches, missing or extra slots, duplicate slots, changed template tokens, changed URL order, new brace syntax, oversized text, and em dash rejection. Assert a valid selected flow with one invalid paraphrase falls back only that slot to its authored template.

- [ ] **Step 2: Write failing prompt-layout and request-count tests**

Capture two payloads with different histories and candidates. Assert:

```python
assert first["messages"][0] == second["messages"][0]
assert first["response_format"] == {"type": "json_object"}
assert first["reasoning"] == {"effort": "none"}
assert first["max_tokens"] == 1024
assert first["messages"][1] != second["messages"][1]
```

Assert candidate keys never appear in the system message or response-format declaration. Assert one valid provider response causes exactly one HTTP request.

- [ ] **Step 3: Run gateway tests and verify RED**

Run: `uv run pytest tests/unit/routing/test_openrouter_gateway.py -q`

Expected: FAIL because only key-only decoding is implemented.

- [ ] **Step 4: Implement the stable instruction and local validator**

Replace `_KEY_SELECTION_INSTRUCTION` with a byte-stable `_MULTI_INTENT_RESPONSE_INSTRUCTION`. It must encode every style and preservation rule from the design spec, including the ban on em dashes. Dynamic request JSON remains the second message.

Keep the existing bounded raw-response decoder and secret-clearing behavior. Use `response_format: {"type": "json_object"}`, `reasoning: {"effort": "none"}`, and `max_tokens: 1024`. Do not generate a candidate-specific JSON schema.

Split constants so provider transport retries use `OPENROUTER_HTTP_MAX_ATTEMPTS = 2`; remove routing-semantic reuse of `ROUTING_MAX_ATTEMPTS`.

- [ ] **Step 5: Preserve other gateway capabilities**

Keep persona summary and match ranking operations working. Give each operation its own decoder capability token and static instruction. Do not route persona or match ranking through the multi-intent decoder.

- [ ] **Step 6: Run gateway and privacy tests and verify GREEN**

Run: `uv run pytest tests/unit/routing/test_openrouter_gateway.py tests/unit/persona tests/unit/routing -q`

Expected: PASS with no raw response body, user text, or secret retained by raised errors.

- [ ] **Step 7: Commit and push Task 5**

Run:

```bash
git add src/friendly_bot/routing src/friendly_bot/hyperparameters.py tests/unit/routing tests/unit/persona
git commit -m "feat: return multi intent response plans"
git push origin main
```

### Task 6: Implement one-call multi-intent routing and pending policy

**Files:**
- Modify: `src/friendly_bot/routing/router.py`
- Create: `src/friendly_bot/routing/policy.py`
- Create: `tests/unit/routing/test_policy.py`
- Modify: `tests/unit/routing/test_router.py`
- Modify: `tests/integration/routing/test_router_uow.py`

**Interfaces:**
- Consumes: `ResponseModel.route_and_plan`, assembled candidates, validated model results, and `PendingIntentService`.
- Produces: `RoutedAnswer`, `RoutedInteractive`, `MultiIntentRoutingResult`, and `MultiIntentPolicy.partition`.

- [ ] **Step 1: Write failing one-call routing tests**

Test zero, one, and five returned matches. For every successful case assert `gateway.calls == 1`. Delete expectations involving shrinking candidate sets and `system.done`.

Use the target result:

```python
@dataclass(frozen=True, slots=True)
class RoutedMatch:
    candidate: RoutingCandidate
    reply_plan: ReplyPlan


@dataclass(frozen=True, slots=True)
class MultiIntentRoutingResult:
    answers: tuple[RoutedMatch, ...]
    interactive: RoutedMatch | None
    deferred: tuple[RoutingCandidate, ...]
    terminal: RoutingTerminal | None
```

- [ ] **Step 2: Write failing local-policy tests**

Cover all combinations:

```text
answers only -> all answers, no interactive, no deferred
answers plus one interactive -> all answers plus that interactive
answers plus three interactive -> all answers plus first interactive plus two deferred
safety plus anything -> safety only
duplicate key -> protocol failure
```

Assert local candidate order follows model match order and the policy never opens two interactive flows.

- [ ] **Step 3: Run routing tests and verify RED**

Run: `uv run pytest tests/unit/routing/test_policy.py tests/unit/routing/test_router.py tests/integration/routing/test_router_uow.py -q`

Expected: FAIL because routing remains iterative and has no multi-intent partition.

- [ ] **Step 4: Replace the iterative loop**

Build one `MultiIntentRequest`, call `route_and_plan` once, map validated flow IDs back to local candidates, and pass ordered matches to `MultiIntentPolicy.partition`. Remove `RoutingTerminal.DONE`, `_RESERVED_TERMINALS`, the shrinking candidate dictionary, and routing `max_attempts`.

The router must not write pending intents. It returns deferred candidates so application dispatch can mutate pending state in the same user transaction as the selected flow.

- [ ] **Step 5: Run routing tests and verify GREEN**

Run: `uv run pytest tests/unit/routing/test_policy.py tests/unit/routing/test_router.py tests/integration/routing/test_router_uow.py -q`

Expected: PASS with exactly one model operation per typed update.

- [ ] **Step 6: Commit and push Task 6**

Run:

```bash
git add src/friendly_bot/routing tests/unit/routing tests/integration/routing
git commit -m "feat: route all message intents in one call"
git push origin main
```

### Task 7: Execute answers, one interactive flow, and deferred intents

**Files:**
- Modify: `src/friendly_bot/actions/context.py`
- Modify: `src/friendly_bot/actions/executors.py`
- Modify: `src/friendly_bot/actions/runner.py`
- Modify: `src/friendly_bot/app.py`
- Modify: `tests/e2e/test_action_registry.py`
- Modify: `tests/e2e/test_action_runner.py`
- Modify: `tests/e2e/test_application_dispatch.py`

**Interfaces:**
- Consumes: `PresentationBuffer`, `ReplyPlan`, `MultiIntentRoutingResult`, `PendingIntentService`, and `ResponseModel.plan_known_flow`.
- Produces: reply-plan-aware action contexts, answer-fragment execution, branch-completion reporting, known-flow planning, and pending resumption.

- [ ] **Step 1: Write failing action tests for paraphrased and fixed copy**

Assert:

```python
action_context = context.for_action("system.greeting", 0)
await send_message(
    SendMessageAction(type="send_message", text="Hello {{ user.name }}"),
    action_context,
)
await send_message_fixed(
    SendMessageFixedAction(type="send_message_fixed", text="Call 999 now"),
    context,
)

assert context.presentation_buffer.snapshot()[0].text == "hey Ari"
assert context.presentation_buffer.snapshot()[1].text == "Call 999 now"
```

Add an invalid or absent reply-plan case that renders the authored `send_message` template, records `paraphrase.validation_fallback`, and continues.

- [ ] **Step 2: Write failing application multi-intent tests**

Cover these exact dispatch outcomes:

- Two answer matches append both presentations and do not apply a selection transition.
- One answer and one interactive match append the answer and execute the interactive transition.
- Three interactive matches execute the first and persist the other two in order.
- An interruptive safety match discards all other answers and deferred matches.
- Completing an interactive branch resumes exactly one still-eligible pending intent.
- An expired or ineligible pending intent is deleted and skipped.
- A known button flow with paraphrased actions makes one `plan_known_flow` call.
- A fixed-only button flow makes zero provider calls.

- [ ] **Step 3: Run action and application tests and verify RED**

Run: `uv run pytest tests/e2e/test_action_registry.py tests/e2e/test_action_runner.py tests/e2e/test_application_dispatch.py -q`

Expected: FAIL because contexts still enqueue durable deliveries and dispatch expects key-only routing.

- [ ] **Step 4: Make action execution consume reply plans**

Replace the delivery sequence in `ActionContext` with a shared `PresentationBuffer` and immutable `ReplyPlan`. Add `ActionContext.for_action(flow_key: str, action_index: int) -> ActionContext` and `ActionContext.message_template(action: SendMessageAction) -> str`. The runner supplies a per-action context to each executor. `message_template` looks up validated paraphrased text by the current action address and falls back to the authored template. `send_message_fixed` always ignores reply plans.

Keep pending text-plus-buttons composition local. `flush_presentation` appends one typed presentation to the buffer. `enqueue_text_to` appends a presentation for the explicit recipient.

- [ ] **Step 5: Implement application execution policy**

`FriendlyBotApplication.dispatch` owns one presentation buffer and returns it in `DispatchResult`:

```python
@dataclass(frozen=True, slots=True)
class DispatchResult:
    kind: DispatchKind
    executed_flow_keys: tuple[str, ...] = ()
    presentations: tuple[TelegramPresentation, ...] = ()
```

Execute answer matches through an action-only path that never calls `SelectionTransitionEngine.select_child`. Execute only `routing.interactive` through the normal transition path. Append `routing.deferred` through `PendingIntentService` before returning.

Add explicit branch-completion state to `ActionRunResult`. After completion, rebuild current candidates, delete stale pending rows, and execute at most one valid pending flow. Do not recursively resume a second pending flow during the same update.

- [ ] **Step 6: Implement known-flow response planning**

Before callback, command, onboarding, timestamp, or root execution, assemble its immediate reply slots. Call `plan_known_flow` once only when slots exist. On transport or protocol failure, build an authored fallback `ReplyPlan`, record a safe diagnostic, and execute the locally trusted flow.

- [ ] **Step 7: Run application tests and verify GREEN**

Run: `uv run pytest tests/e2e/test_action_registry.py tests/e2e/test_action_runner.py tests/e2e/test_application_dispatch.py -q`

Expected: PASS with no `NewOutboundDelivery` test doubles and no application-level outbound database writes.

- [ ] **Step 8: Commit and push Task 7**

Run:

```bash
git add src/friendly_bot/actions src/friendly_bot/app.py tests/e2e
git commit -m "feat: execute planned replies and pending intents"
git push origin main
```

### Task 8: Send ingress and scheduler presentations after commit

**Files:**
- Modify: `src/friendly_bot/telegram/poller.py`
- Modify: `src/friendly_bot/services/scheduler.py`
- Modify: `src/friendly_bot/app.py`
- Modify: `tests/integration/telegram/test_poller.py`
- Modify: `tests/integration/services/test_scheduler.py`
- Modify: `tests/unit/test_runtime_loops.py`

**Interfaces:**
- Consumes: `DispatchResult.presentations`, `BestEffortTelegramSender`, timestamp recipient claims, and the existing unit-of-work commit boundary.
- Produces: post-commit direct sending for user updates and scheduled recipients.

- [ ] **Step 1: Write failing ingress ordering tests**

Record transaction exit and gateway-send events. Assert exact ordering:

```python
assert events == [
    "dispatch",
    "offset_advanced",
    "transaction_committed",
    "telegram_send_started",
]
```

Add a process-stop simulation after commit and before send. Assert the offset remains advanced and no outbound row exists. Add a send-failure case proving the next update still processes.

- [ ] **Step 2: Write failing scheduler ordering tests**

For two recipients, assert each timestamp claim and application state commit before its send begins. A failed first recipient send must not stop the second recipient. A second scheduler tick must not resend either claimed recipient.

- [ ] **Step 3: Run ingress and scheduler tests and verify RED**

Run: `uv run pytest tests/integration/telegram/test_poller.py tests/integration/services/test_scheduler.py tests/unit/test_runtime_loops.py -q`

Expected: FAIL because presentations are still delivered by `_outbox_forever`.

- [ ] **Step 4: Send after ingress commit**

Inject a `TelegramPresentationSender` into `TelegramIngress`. Capture `DispatchResult.presentations` inside the unit of work, exit the context to commit, then call `sender.send_all`. Extend `ProcessedUpdate` with `send_attempted` and `send_failed` integer counts for tests and sanitized metrics.

- [ ] **Step 5: Send after each scheduler recipient commit**

Change the timestamp port result to:

```python
@dataclass(frozen=True, slots=True)
class TimestampRootPreparation:
    presentations: tuple[TelegramPresentation, ...]
```

Capture the result inside the recipient unit of work, commit the timestamp claim and state, then call `sender.send_all`. Rename scheduler result fields from enqueued counts to attempted and failed send counts.

- [ ] **Step 6: Remove the outbox runtime loop from composition**

Delete `FriendlyBotRuntime.outbox`, construction of `OutboundDeliveryWorker`, `_outbox_forever`, and its TaskGroup task. The runtime keeps polling, scheduling, preflight, and runtime-lock health checks.

- [ ] **Step 7: Run ingress, scheduler, and runtime tests and verify GREEN**

Run: `uv run pytest tests/integration/telegram/test_poller.py tests/integration/services/test_scheduler.py tests/unit/test_runtime_loops.py -q`

Expected: PASS with direct sends starting immediately after commits.

- [ ] **Step 8: Commit and push Task 8**

Run:

```bash
git add src/friendly_bot/telegram/poller.py src/friendly_bot/services/scheduler.py src/friendly_bot/app.py tests/integration/telegram/test_poller.py tests/integration/services/test_scheduler.py tests/unit/test_runtime_loops.py
git commit -m "refactor: send telegram replies after commit"
git push origin main
```

### Task 9: Delete durable outbound delivery infrastructure

**Files:**
- Create: `alembic/versions/0006_remove_outbound_delivery.py`
- Delete: `src/friendly_bot/telegram/outbox.py`
- Delete: `tests/integration/telegram/test_outbox.py`
- Modify: `src/friendly_bot/telegram/__init__.py`
- Modify: `src/friendly_bot/persistence/models.py`
- Modify: `src/friendly_bot/persistence/repositories.py`
- Modify: `src/friendly_bot/persistence/uow.py`
- Modify: `tests/integration/persistence/test_repositories.py`
- Modify: `tests/integration/persistence/test_migrations.py`
- Modify: `tests/integration/persistence/test_schema_constraints.py`

**Interfaces:**
- Consumes: direct sender integration completed in Task 8.
- Produces: a persistence surface with timestamp claims but no outbound message, attempt, pause, or admin-delivery queue.

- [ ] **Step 1: Write failing migration and schema tests**

After upgrading to head, assert these tables do not exist:

```python
assert {
    "outbound_deliveries",
    "outbound_delivery_attempts",
    "telegram_outbound_pauses",
    "admin_notification_deliveries",
}.isdisjoint(inspector.get_table_names())
assert "timestamp_delivery_claims" in inspector.get_table_names()
```

Assert downgrading one revision recreates the removed schema sufficiently for a subsequent upgrade. Preserve the earlier historical migration tests by targeting their original revision boundaries rather than current head.

- [ ] **Step 2: Run migration tests and verify RED**

Run: `uv run pytest tests/integration/persistence/test_migrations.py tests/integration/persistence/test_schema_constraints.py -q`

Expected: FAIL because all four delivery tables still exist.

- [ ] **Step 3: Remove models, repositories, and unit-of-work access**

Delete `OutboundDelivery`, `OutboundDeliveryAttempt`, `TelegramOutboundPause`, `AdminNotificationDelivery`, their records and errors, `DeliveryRepository`, `SqlAlchemyDeliveryRepository`, `UnitOfWork.deliveries`, and exports used only by the outbox.

Move `claim_timestamp_delivery` to `ServiceRepository` and `SqlAlchemyServiceRepository`, retaining its unique timestamp-recipient insert and service-interaction-closure checks. Change diagnostics fan-out to safe terminal logging and delete `enqueue_admin_notifications` from `DiagnosticRepository`.

- [ ] **Step 4: Add the removal migration**

Revision `0006_remove_outbound_delivery` drops foreign-key dependents first:

```text
admin_notification_deliveries
outbound_delivery_attempts
outbound_deliveries
telegram_outbound_pauses
```

Its downgrade recreates the exact columns, indexes, checks, unique constraints, and foreign keys defined by revisions `0001`, `0002`, and `0003`. It does not touch `timestamp_delivery_claims` or `diagnostic_records`.

- [ ] **Step 5: Remove obsolete tests and prove no references remain**

Run:

```bash
rg -n "OutboundDelivery|NewOutboundDelivery|DeliveryRepository|OutboundDeliveryWorker|telegram_outbound_pauses|admin_notification_deliveries|_outbox_forever" src tests
```

Expected: no matches. References inside historical Alembic revisions and historical design documents are allowed and should be excluded from this check.

- [ ] **Step 6: Run persistence tests and verify GREEN**

Run: `uv run pytest tests/integration/persistence tests/integration/services/test_scheduler.py tests/integration/telegram/test_poller.py -q`

Expected: PASS with timestamp recipient deduplication preserved.

- [ ] **Step 7: Commit and push Task 9**

Run:

```bash
git add alembic/versions/0006_remove_outbound_delivery.py src/friendly_bot/telegram src/friendly_bot/persistence tests/integration
git commit -m "refactor: remove telegram delivery outbox"
git push origin main
```

### Task 10: Contain runtime failures and expose safe latency logs

**Files:**
- Create: `src/friendly_bot/observability.py`
- Modify: `src/friendly_bot/app.py`
- Modify: `src/friendly_bot/telegram/poller.py`
- Modify: `src/friendly_bot/services/scheduler.py`
- Modify: `tests/unit/test_runtime_loops.py`
- Modify: `tests/e2e/test_application_dispatch.py`
- Modify: `tests/unit/telegram/test_sender.py`

**Interfaces:**
- Consumes: typed gateway failures, routing invariant failures, direct-send results, scheduler results, and runtime lock health.
- Produces: stable terminal reason codes, numeric timing observations, and supervised ordinary failures.

- [ ] **Step 1: Write failing fault-containment tests**

Inject one ordinary failure followed by success into polling, scheduler recipient processing, response planning, action execution, and direct Telegram sending. Assert the later operation runs. Add negative tests proving cancellation and `TelegramRuntimeLockLostError` still escape.

Capture logs and assert absence of sentinel user text, model text, URL, token, and raw exception strings.

- [ ] **Step 2: Write failing timing tests**

Inject a monotonic clock and assert these stable fields are emitted as numbers:

```text
routing.operation_ms
routing.http_attempt_count
routing.match_count
routing.answer_match_count
routing.interactive_match_count
routing.pending_intent_count
routing.paraphrase_fallback_count
telegram.direct_send_ms
telegram.direct_send_outcome
runtime.poll_recovery_count
runtime.scheduler_recovery_count
```

- [ ] **Step 3: Run observability tests and verify RED**

Run: `uv run pytest tests/unit/test_runtime_loops.py tests/e2e/test_application_dispatch.py tests/unit/telegram/test_sender.py -q`

Expected: FAIL because the stable observation helper and complete supervision boundaries do not exist.

- [ ] **Step 4: Implement safe observations and supervision**

Create an `observe` helper that accepts an event name plus `str | int | float | bool | None` fields and rejects arbitrary objects. Catch ordinary exceptions around one poll iteration and one scheduler iteration, emit only a stable reason code, wait with bounded backoff, then continue.

Application dispatch converts `GatewayTransportError`, `GatewayProtocolError`, and local `RoutingError` into the existing fixed user error plus a sanitized diagnostic. Action-runner errors remain contained through the existing error event. Direct sender errors remain post-commit and non-fatal.

Do not catch `BaseException`. Call `runtime_lock.ensure_healthy()` outside ordinary iteration recovery so lock loss stops the TaskGroup.

- [ ] **Step 5: Run fault and logging tests and verify GREEN**

Run: `uv run pytest tests/unit/test_runtime_loops.py tests/e2e/test_application_dispatch.py tests/unit/telegram/test_sender.py -q`

Expected: PASS with later operations continuing after every injected ordinary failure.

- [ ] **Step 6: Commit and push Task 10**

Run:

```bash
git add src/friendly_bot/observability.py src/friendly_bot/app.py src/friendly_bot/telegram/poller.py src/friendly_bot/services/scheduler.py tests/unit/test_runtime_loops.py tests/e2e/test_application_dispatch.py tests/unit/telegram/test_sender.py
git commit -m "fix: supervise bot runtime failures"
git push origin main
```

### Task 11: Verify the complete behavior and update operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/masterplans/architecture.md`
- Modify: `docs/masterplans/product-specification.md`
- Modify: `docs/superpowers/plans/2026-09-15-multi-intent-responses-and-direct-telegram-delivery.md`

**Interfaces:**
- Consumes: all prior tasks on `main`, the configured Colima/PostgreSQL environment, and ignored operator-owned environment values.
- Produces: complete automated evidence and an operator-ready live-test checklist.

- [ ] **Step 1: Update current documentation**

Document these exact runtime facts:

- Typed user messages use one-call multi-intent routing.
- `send_message` is paraphrased and `send_message_fixed` is exact.
- Additional interactive matches are stored as structured pending intents.
- Telegram sends occur once after commit and are lost on failure or interruption.
- The outbox worker and its 500 ms polling loop no longer exist.
- Timestamp claims remain to prevent repeated scheduled sends.
- Colima plus the Docker CLI and Compose plugin remain the supported local container runtime.

- [ ] **Step 2: Run the full automated test suite**

Run: `uv run pytest --basetemp /private/tmp/friendly-bot-multi-intent-tests -q`

Expected: all tests pass. Tests requiring an explicitly configured integration database may skip only under their existing marker policy.

- [ ] **Step 3: Run static verification**

Run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/friendly_bot
```

Expected: all commands exit zero.

- [ ] **Step 4: Verify migrations against the configured development database**

Run:

```bash
uv run alembic upgrade head
uv run alembic current
```

Expected: current revision is `0006_remove_outbound_delivery` and startup publication accepts the audited Zone X seed.

- [ ] **Step 5: Run privacy and source scans**

Run:

```bash
rg -n "system\.done|OutboundDeliveryWorker|_outbox_forever|NewOutboundDelivery" src tests README.md docs/masterplans
rg -n $'\u2014' src tests README.md docs/masterplans docs/superpowers/specs/2026-09-15-multi-intent-responses-and-direct-telegram-delivery-design.md docs/superpowers/plans/2026-09-15-multi-intent-responses-and-direct-telegram-delivery.md
```

Expected: both commands return no matches. Historical Alembic revisions and historical dated plans may retain old delivery names, but no em dash is permitted in files changed by this delivery.

- [ ] **Step 6: Benchmark one-call routing**

Using the configured test bot, send each message once after a warm-up request:

```text
where is zone x and what should i expect?
where is zone x and can someone meet me?
can someone meet me and can i change service?
what options are there?
```

Expected terminal evidence:

```text
routing.http_attempt_count=1
routing.operation_count=1
```

Verify p50 update-receipt-to-committed-presentations is at most 1.5 seconds and p95 is at most 3.0 seconds over at least 20 ordinary typed messages. Verify direct Telegram sending starts without a fixed 500 ms delay.

- [ ] **Step 7: Perform controlled failure smoke tests**

Use dependency injection or the existing fake gateways, never secret mutation, to simulate one OpenRouter failure and one Telegram send failure. Verify the bot logs stable reason codes, advances past the affected update according to the design, and handles the next real test message without restarting.

- [ ] **Step 8: Commit and push Task 11**

Run:

```bash
git add README.md docs/masterplans docs/superpowers/plans/2026-09-15-multi-intent-responses-and-direct-telegram-delivery.md
git commit -m "docs: describe multi intent direct delivery runtime"
git push origin main
```

## Plan Self-Review

- **Spec coverage:** Tasks 1, 4, 5, 6, and 7 implement fixed copy, paraphrasing, typed `any_of` routing, all-at-once matches, answer-versus-interactive policy, and one-call reply planning. Tasks 3 and 7 implement bounded pending intents outside persona text. Tasks 2, 8, and 9 implement post-commit best-effort sending and delete the outbox while preserving timestamp claims. Task 10 covers non-fatal ordinary failures and safe terminal logs. Task 11 covers documentation, migration, privacy, performance, and live behavior gates.
- **Placeholder scan:** Every task names exact files, target interfaces, failing tests, implementation behavior, commands, expected results, and a commit boundary. No deferred implementation marker remains.
- **Type consistency:** `RoutingPromptCandidate` owns model-safe gists and reply slots; `RoutedMatch` binds a local `RoutingCandidate` to a validated `ReplyPlan`; `DispatchResult` carries immutable `TelegramPresentation` values; ingress and scheduler send those values only after their unit of work exits successfully.
- **Execution order:** Tasks are dependency ordered and must run sequentially on `main`. A worker must not begin Task 9 until Task 8 has removed all runtime dependency on the durable outbox.
