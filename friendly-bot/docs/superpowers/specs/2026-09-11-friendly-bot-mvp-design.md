# Friendly Bot MVP Design Specification

| Field | Value |
| --- | --- |
| Status | Approved design; implementation not yet started |
| Decision owner | Ryan The |
| Effective date | 2026-09-11 |
| Scope | Friendly Bot local MVP in `friendly-bot/` |
| Product authority | [`../../masterplans/product-specification.md`](../../masterplans/product-specification.md) |
| Architecture authority | [`../../masterplans/architecture.md`](../../masterplans/architecture.md) |

## 1. Purpose and scope

Friendly Bot is a low-barrier Telegram connection point for youths who may be shy to approach a physical connect point, raise their hand during an altar call, or ask lingering questions in person. The MVP supports NBNC onboarding, service-aware conversation flows, server login and attendance, constrained LLM routing, and interest-based introductions to a friendly human.

The MVP is a Python application run locally on Ryan's machine. It uses Telegram long polling, a local PostgreSQL database, a local scheduler, and OpenRouter for constrained routing and matching. It does not include a hosted runtime, an admin UI, bot-relayed human chat, or free-form LLM responses.

`NBNC` is the product term. The MVP does not distinguish newcomers from new believers.

## 2. Design approach

Flow definitions are recursive, typed, versioned JSON documents stored in PostgreSQL. Runtime state is relational and records which flow child sets are open for each user. Python owns the permitted trigger and action types and validates every flow version before publication.

This approach preserves future admin configurability without spreading a recursive graph across many configuration tables or requiring a code deployment for every service-copy change.

## 3. DiscussionFlow definition

### 3.1 Core model

```python
class NextFlowMode(StrEnum):
    ONE_AND_ONCE_ONLY = "one_and_once_only"
    ALLOW_MANY = "allow_many"
    CHECKPOINT = "checkpoint"


@dataclass
class DiscussionFlow:
    key: str
    trigger: DiscussionFlowTrigger | None
    actions: list[DiscussionAction]
    next_flows: list["DiscussionFlow"]
    next_flow_mode: NextFlowMode
    return_actions: list[DiscussionAction] = field(default_factory=list)
```

`trigger=None` is permitted only for a root flow invoked automatically by system initialization, service entry, or a service timestamp.

An empty `actions` list is meaningful: selecting the flow performs no side effect and exposes its `next_flows`. A flow with both `actions=[]` and `next_flows=[]` is a no-op and produces a validation warning.

### 3.2 Next-flow modes

The mode belongs to the parent flow and governs the reuse and return behavior of its `next_flows` group.

- `ONE_AND_ONCE_ONLY`: after one child succeeds, remove the parent selection from current and do not retain it in past. None of that parent's children can be selected through that occurrence again.
- `ALLOW_MANY`: after a child succeeds, move the parent selection from current to past. Every child, including the same child, remains reusable on later Telegram updates.
- `CHECKPOINT`: provides the same reusable choice behavior as `ALLOW_MANY` and also becomes a branch return destination. Initial entry runs `actions`; a later return runs `return_actions` and exposes the same `next_flows` again.

Within one Telegram update, the same flow key may execute at most once. The router may select several different applicable flow keys, one validated key per routing iteration, until it returns `done`.

### 3.3 Validation rules

- Flow keys are unique within a published version.
- The system-global root is a `CHECKPOINT` with `trigger=None`.
- Every service-global root is a `CHECKPOINT` with `trigger=None`.
- Every service timestamp owns exactly one root `DiscussionFlow`; that root has `trigger=None`.
- A timestamp root may use any next-flow mode and inherits the service checkpoint as an ancestor.
- A checkpoint has at least one child.
- `return_actions` is permitted only on a checkpoint.
- Trigger and action discriminators must name registered Python types.
- Button IDs are stable, namespaced identifiers.
- Template variables must be declared and resolvable before publication.
- Every non-error action event declared by an action type has exactly one direct child handler.
- A flow may define at most one direct `error` event handler.
- Event-driven cycles without a Telegram-input boundary are invalid.

## 4. Trigger model

All triggers extend `DiscussionFlowTrigger`.

```python
MessageDiscussionFlowTrigger(llm_gist: str)
ButtonDiscussionFlowTrigger(button_id: str)
CommandDiscussionFlowTrigger(command: str)
AutomaticDiscussionFlowTrigger(reason: str)
ActionEventDiscussionFlowTrigger(event_key: str)
AnyOfDiscussionFlowTrigger(triggers: list[DiscussionFlowTrigger])
```

Ordinary text uses only `llm_gist`; there is no strict-keyword path. Exact slash commands use `CommandDiscussionFlowTrigger`. Exact Telegram button callbacks use `ButtonDiscussionFlowTrigger`. Telegram-addressed commands such as `/login@friendly_bot` normalize to `/login`.

Service timestamps invoke their root flow from scheduler context, so the root itself has no trigger object. `ActionEventDiscussionFlowTrigger` handles an event emitted while executing its direct parent flow. It is deterministic, is never offered to the LLM, and does not match events emitted by siblings, reusable past selections, or ancestors.

`AnyOfDiscussionFlowTrigger` permits a single flow to accept more than one trigger mechanism without duplicating its actions.

## 5. Action model

All actions extend `DiscussionAction`. The MVP includes typed actions for:

- sending fixed messages;
- sending buttons with stable IDs;
- sending pictures and captions;
- showing Telegram typing or upload activity;
- saving the incoming message verbatim;
- setting or changing user fields;
- adding or changing service attendance;
- ranking, assigning, releasing, and notifying human matches;
- sharing a validated Telegram contact URL;
- generating a user persona; and
- returning to the nearest checkpoint.

The design rejects a generic `behavior`, handler-name, or arbitrary-code action. Complex behaviors use explicit Python action subclasses with validated parameters. Normal matching uses a dedicated server-only action; safety matching uses a separate leader-or-staff-only action. Neither action accepts a configurable role list.

```python
@dataclass(frozen=True)
class ActionEvent:
    key: str
    payload: ActionEventPayload | None = None


class DiscussionAction(BaseModel):
    type: str


class FindAndReserveServerAction(DiscussionAction):
    type: Literal["find_and_reserve_server"]
    emitted_event_keys: ClassVar[frozenset[str]] = frozenset(
        {"human_match.found", "human_match.not_found"}
    )


class FindAndReserveSafetyResponderAction(DiscussionAction):
    type: Literal["find_and_reserve_safety_responder"]
    emitted_event_keys: ClassVar[frozenset[str]] = frozenset(
        {"safety_match.found", "safety_match.not_found"}
    )
```

The action configuration contains validated parameters only. Its registered Python executor receives the internal action context and may call `context.emit(ActionEvent(...))`; administrators cannot supply a handler name or executable behavior. Event payloads remain in the local execution context and are never automatically included in an OpenRouter prompt.

An action may emit one terminal `ActionEvent` through the execution context. Emitting an event stops the remaining actions in that flow, then the harness selects the one direct `next_flow` whose `ActionEventDiscussionFlowTrigger.event_key` matches. Action subclasses declare their possible non-error event keys so publication can require complete, unambiguous direct handlers.

After safe retry exhaustion, an unexpected action failure emits the reserved `error` event and stops the remaining actions. A direct `error` child customizes recovery for that flow. If the direct children do not contain an `error` trigger, the harness calls a hardcoded error sender with: “Sorry, an error occurred. Error log: {telegram_user_id}.” The hardcoded handler is not a `DiscussionFlow`, is absent from published JSON, and cannot be customized by an administrator. Error events never bubble to ancestors. The failed selection remains available for a safe retry. The Telegram user ID is rendered locally for the affected user and admins and is never included in an OpenRouter request.

An outcome-emitting action must be the final action in its list because later actions would be unreachable. Ordinary actions that finish successfully without emitting an event continue to the next action. The parent transition and the emitted event's child actions are committed as one idempotent processing unit.

Telegram user IDs and DOBs are never inserted into OpenRouter action inputs.

## 6. Open selections, current focus, and past reuse

### 6.1 Persisted open selection

The system does not persist a `DiscussionFlowInstance` or map Telegram message IDs to flows. It stores the minimum activation state required to recover open child groups from an immutable flow version.

```python
@dataclass
class OpenFlowSelection:
    id: UUID
    user_id: UUID
    flow_version_id: UUID
    parent_flow_key: str
    service_id: UUID | None
    is_current: bool
    is_global_interruptive: bool
    ancestor_flow_keys: list[str]
    checkpoint_flow_keys: list[str]
    opened_at: datetime
    last_focused_at: datetime
```

The referenced parent definition supplies the currently available `next_flows`. System-global, service-global, current, and reusable-past selections are all assembled into the router's single `open_selections` collection. Global interruptive flows are not passed in a separate field.

Current is a soft importance signal, not an eligibility boundary. Its size is not capped. A timestamp may add a current selection while another prompt awaits a reply.

### 6.2 Selection transition

When a child is selected from a current parent:

1. Execute the child actions.
2. Remove the parent from current.
3. If the parent mode is `ONE_AND_ONCE_ONLY`, remove that parent selection entirely.
4. If the parent mode is `ALLOW_MANY`, retain it as a reusable past selection.
5. If the parent mode is `CHECKPOINT`, retain it in the branch checkpoint path.
6. If the selected child has children, create or deduplicate its open selection and append it to current.
7. Leave unrelated current selections unchanged.

Selecting an `ALLOW_MANY` parent from past leaves it reusable in past and appends the selected child's child set to current when applicable.

### 6.3 Native replies and ambiguity

Telegram supplies the text of a natively replied-to message. The router receives this text as a strong context hint, but it may still select any open flow. No Telegram message-ID-to-flow mapping is required.

If identical or otherwise ambiguous contexts prevent a confident choice, the model returns the reserved key `system.clarify_ambiguous_context`; the harness sends: “Sorry, which message were you referring to?”

Old non-service buttons remain usable as long as their parent selection remains reusable. Service-bound buttons stop at `interaction_ends_at` and send: “Sorry, the service is over!”

## 7. Checkpoint traversal

Every user branch has a guaranteed checkpoint ancestor:

```text
System root checkpoint
└── Service root checkpoint, when attending a service
    └── Timestamp root flow
        └── Nested discussion flows
```

When a selected leaf has `next_flows=[]`, the engine traverses that branch's `checkpoint_flow_keys` from nearest to farthest, runs the nearest checkpoint's `return_actions`, and exposes its children again.

“Never mind” is a global interruptive flow that uses the same traversal:

- from a checkpoint descendant, return to the nearest checkpoint;
- from the checkpoint itself, pop it and return to the parent checkpoint;
- from the service checkpoint, return to the system checkpoint; and
- at the system checkpoint, repeat its return actions because no higher checkpoint exists.

Parallel timestamp branches carry independent checkpoint paths. A leaf never returns to an unrelated newer prompt.

## 8. Constrained model behavior

### 8.1 Routing

For each incoming message, OpenRouter receives:

- the user's persona;
- all unsummarized conversation messages since the last persona cursor;
- the current incoming message;
- natively replied-to message text, when present;
- all open selections and their candidate flow keys;
- current, past, global, and service context labels; and
- the reserved `done`, no-match, and clarification keys.

The model returns exactly one permitted key per routing call. The harness validates it, executes only configured actions, removes the selected key from the same-update candidate set, and asks again until the model returns `done`. The LLM never writes user-facing bot prose.

No-match uses an optional configured fallback; the system default sends: “Sorry, I didn't understand your request.” A repeatable global flow for “the user wants to speak to a human” remains independently eligible.

### 8.2 Persona

Full conversation history is stored long term. The persona may retain everything the user disclosed. Persona generation runs after 48 hours of inactivity or when the unsummarized segment reaches `PERSONA_MAX_UNSUMMARIZED_TOKENS`, configured in `hyperparameters.py` for the selected model. The cursor advances after successful persona generation; source messages remain stored.

Structured Telegram user IDs and DOB fields are never sent to OpenRouter. User-authored content may be sent as written. The user's stored name is the only structured identifying field included.

### 8.3 OpenRouter policy

The MVP uses `qwen/qwen3.7-flash`, declared in `hyperparameters.py`. Every request requires zero-data-retention routing and denies provider data collection. Prompt logging remains disabled. If no compliant endpoint is available, the request fails closed and emits the reserved `error` event; it never silently relaxes privacy requirements.

The OpenRouter API key is a secret supplied outside source control.

## 9. Users, roles, and onboarding

### 9.1 Identity and roles

All people share a `users` identity record. Operational roles are `nbnc`, `server`, `leader`, and `staff`, with capability inheritance `staff > leader > server`. Admin is an independent superuser flag and does not automatically make a person eligible for human matching.

An NBNC does not provide a DOB. Prefilled operational profiles used for login contain normalized name and DOB. A Telegram account may occupy at most one operational login, and an operational profile may have at most one logged-in Telegram account.

Profiles may declare `always_available=True`. Safety matching accepts staff or leaders who are either always available or attending the current service. Normal matching always requires service attendance.

### 9.2 NBNC onboarding

For an unknown Telegram user, `/start` or any ordinary non-special message begins:

1. “Hey! Welcome to The Zone! Glad to see you here today!”
2. “How may I address you?”
3. Save the next reply exactly as the name.
4. Resolve applicable service attendance behavior.
5. Enter the service checkpoint or the system basic-features checkpoint.

For an existing user, `/start` welcomes them back and offers “Continue” and “Change my name.” Changing the name stores the next supplied reply exactly.

Outside a service, NBNCs receive basic options including directions to Star and information about NCC.

### 9.3 Operational login and management

`/login` starts a name-and-DOB login flow. If the matching operational profile already has a Telegram account attached, login fails until `/logout` is used from that account or an admin manually clears it. Stronger verification is outside the MVP.

On first successful server login, the bot asks for hobbies, interests, and conversation topics for matching. `/manage` exposes “Edit interests.” Operational profiles also store `cg_name`, a validated Telegram contact URL, matching capacity, and availability.

`/logout` detaches the Telegram account without deleting the shared user or operational profile.

No slash command has implicit behavior. `/start`, `/login`, `/logout`, `/manage`, and any future `/cancel` work only through configured `CommandDiscussionFlowTrigger` flows.

## 10. Services and attendance

### 10.1 Service model

Each service defines:

- name and stable key;
- `highkey`;
- administrative timezone, defaulting to Asia/Singapore;
- door-open and door-close timestamps;
- `interaction_ends_at`;
- a service-global checkpoint root;
- a latecomer flow;
- versioned timestamp roots; and
- attendee records and delivery state.

Each service timestamp contains one automatically invoked root:

```python
@dataclass
class ServiceTimestamp:
    key: str
    occurs_at: datetime
    audience: ServiceAudience
    root_flow: DiscussionFlow
```

The root flow has `trigger=None`. Durable timing claims ensure the scheduler executes each logical recipient delivery once. Existing attendees remain subscribed to later timestamps after door close and until the configured interaction boundary.

### 10.2 Audiences

Supported audiences are:

- `ALL_NBNCS`
- `ALL_SERVERS`
- `ALL_LEADERS`
- `SERVICE_NBNCS`
- `SERVICE_SERVERS`
- `SERVICE_LEADERS`
- `ALL_SERVICE_ATTENDEES`

Role inheritance applies to audience membership: leader audiences include staff, while server audiences include leaders and staff. Admin status alone does not add a user to an operational audience.

### 10.3 Attendance rules

- With exactly one ongoing highkey service before door close, the bot assumes attendance and enrolls the NBNC.
- With multiple ongoing services and at least one highkey service, the bot requires a choice among services and provides no “none” option.
- With only lowkey services, the bot asks whether and which service the NBNC attends.
- Only one overlapping service is active per NBNC; switching preserves historical attendance but changes active routing and matching.
- After door close but before `interaction_ends_at`, the bot asks whether the NBNC is at an applicable service. Confirmation enrolls them and runs the latecomer flow.
- At `interaction_ends_at`, new attendance and service-bound interactions stop.

The service-global checkpoint exposes a repeatable flow for “I am actually attending another service or timing.”

### 10.4 Zone X development service

Zone X is the first seeded development service and the end-to-end acceptance fixture for the service contract.

Its canonical configuration and conversation walkthrough are in the [Zone X first development service](../../examples/zone-x-service-example.md). The implementation must ship an equivalent JSON seed rather than treating Zone X as disposable sample content.

- Before service: directions to Star, what to expect, and connect with a friendly human.
- During service: ask a question about service. The router selects only approved fixed answers, including “Where is the toilet?” and “Who is Jesus?” Unknown questions offer a human connection.
- After service: “Connect with us” and “Ask a question” both route to the human-matching flow.
- Marketing, reminders, doors, service start, service end, thank-you, and interaction-end moments are independently configurable timestamp roots.

## 11. Human matching

### 11.1 Normal connection

The flow asks:

1. “What is one thing that interests you? (Nothing is a valid answer too!)”
2. Whether the NBNC would like to join the matched human and meet new friends or have the human join them.

The model ranks only users whose operational role is exactly `server` and who satisfy current-service attendance and available-capacity constraints. Leaders and staff are never candidates for normal matching. Ranking uses the NBNC's stated interest, the server's interests, and `cg_name`. Capacity is configurable per server and defaults to one.

The server is assigned immediately without accept or decline. They receive the NBNC's chosen name, interest, meeting preference, service name, and notice that the NBNC may contact them. The NBNC receives the server's validated Telegram contact URL.

If no eligible server is available, the MVP says nobody is currently available. It never selects a server who is absent.

The NBNC also receives “{name} is not responding…”. Selecting it:

1. releases the original server's capacity;
2. notifies the original server;
3. excludes that server from the request's next attempt; and
4. ranks and assigns another eligible contact.

A match consumes capacity until `interaction_ends_at`, rematch, or admin intervention.

### 11.2 Safety connection

Safety is a repeatable system-global interruptive flow. It uses the normal matching mechanism but targets staff or leaders only. An eligible responder has `always_available=True` or attends the current service.

If no responder qualifies, the bot sends an admin-configured urgent-support message, alerts all admins, and leaves the request pending. Safety matching never falls back to an ordinary server.

## 12. Local runtime and persistence

The MVP starts with:

```bash
python -m friendly_bot
```

One local async runtime owns:

- Telegram `getUpdates` long polling;
- durable Telegram update cursor and deduplication;
- service timestamp scheduling and catch-up;
- durable outbound delivery and attempt records;
- persona maintenance;
- admin error and diagnostic notification; and
- per-user serialized conversation processing.

PostgreSQL runs locally through Docker Compose. The process clears any configured Telegram webhook before polling. All conversation, flow version, open selection, attendance, match, scheduler, and delivery state survives process restarts.

On restart, the scheduler claims overdue timestamp work and the Telegram poller resumes from its durable update offset. Definite delivery failures may retry; ambiguous Telegram sends are recorded and not blindly replayed, preventing likely duplicates.

## 13. Data boundaries

The initial schema includes records for:

- users and operational profiles;
- operational logins and availability;
- services, attendees, timestamps, and timing deliveries;
- immutable published flow versions;
- current and reusable-past open flow selections;
- conversation messages and persona cursors;
- human-match requests, assignments, exclusions, and capacity;
- Telegram update cursor and processed-update deduplication;
- outbound delivery attempts; and
- sanitized admin diagnostic records.

Database constraints enforce unique Telegram identity attachment, one active overlapping service per NBNC, unique published flow keys within a version, nonnegative capacity, idempotent timing deliveries, and idempotent update processing.

## 14. Reliability, security, and observability

- Full conversation data is retained long term and protected by database access control, local filesystem permissions, encrypted backups when backups are enabled, and application-layer authorization.
- Structured Telegram IDs and DOBs never enter OpenRouter prompts.
- OpenRouter calls require ZDR and denied provider data collection.
- Secrets are redacted from exceptions, logs, and admin messages.
- Every emitted application error and debug diagnostic creates a sanitized notification for every admin plus a structured local record with a correlation ID.
- Per-user processing is serialized so two rapid Telegram updates cannot corrupt conversation state.
- Flow publication is rejected on invalid recursion, unknown types, unresolved templates, duplicate keys, or invalid checkpoint structure.
- Action events are dispatched only to direct child triggers; an unhandled `error` invokes the hardcoded harness error sender without ancestor bubbling or a configured default flow.
- All configured user-facing prose is sent by the harness, never generated by the router.

## 15. Verification strategy

Implementation follows test-driven development. Coverage includes:

- recursive flow parsing and publication validation;
- every trigger and action subtype;
- declared action-event outcomes, direct event dispatch, custom error handling, and default error handling;
- current-to-past transitions for all three next-flow modes;
- repeated `ALLOW_MANY` choices;
- leaf and “never mind” checkpoint traversal, including nested checkpoints;
- multiple current branches created by timestamps;
- constrained multi-key routing and ambiguity fallback;
- onboarding, login, logout, and server management;
- highkey, lowkey, overlapping, door-close, latecomer, and interaction-end rules;
- normal, rematch, capacity, and safety matching;
- scheduler catch-up and idempotent delivery;
- Telegram update deduplication and restart recovery;
- OpenRouter prompt field exclusion and fail-closed privacy settings; and
- secret redaction and admin diagnostics.

## 16. Explicitly outside the MVP

- Telegram Serverless.
- Cloud hosting, webhook ingress, and managed scheduling.
- An admin UI or flow-builder UI.
- Bot-relayed human chat.
- Human accept/decline or timeout workflows.
- Strong server identity verification.
- Free-form LLM-generated user-facing answers.
- Automated handling when no normal server is available beyond the fixed unavailability response.
- NBNC subtype distinction.
- Conversation deletion and retention-policy UI.

## 17. Acceptance requirements

The MVP is accepted when a clean local setup can start the bot, database, scheduler, and workers; load and run Zone X from its canonical JSON seed as the end-to-end acceptance fixture; onboard NBNCs; authenticate and manage operational users; route only to configured keys; preserve current and reusable-past selections; return correctly through nested checkpoints; deliver scheduled messages and latecomer flows; perform eligible human matching and rematching; invoke the hardcoded error sender when `error` has no direct child; protect OpenRouter fields and policies; survive restarts without duplicate logical work; and notify admins of sanitized failures.
