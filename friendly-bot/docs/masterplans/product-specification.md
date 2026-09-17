# Friendly Bot Product Specification Masterplan

> Living product specification for Friendly Bot's complete approved user and system behavior.

| Field | Value |
| --- | --- |
| Authority type | Product specification |
| Scope | Friendly Bot local MVP for Telegram NBNC engagement, service flows, operational users, and human connection |
| Decision owner | Ryan The |
| Related authorities | [`architecture.md`](architecture.md), [`../superpowers/specs/2026-09-11-friendly-bot-mvp-design.md`](../superpowers/specs/2026-09-11-friendly-bot-mvp-design.md) |

## 1. Product purpose and boundaries

Friendly Bot gives youths a private, low-barrier way to connect with The Zone when they may be uncomfortable approaching a physical connect point, raising their hand, or asking questions publicly. It guides NBNCs through fixed, approved conversations; adapts those conversations to real-world services; and introduces them to eligible friendly humans.

The product term is `NBNC`. The MVP does not distinguish newcomers from new believers. The model selects only configured flows. It may rewrite a configured `send_message_paraphrased` text while retaining protected content, or answer an addressed `send_message_llm` action using only its configured `source`. It cannot add facts, assumptions, or output outside the locally validated action response.

The MVP runs locally on the decision owner's machine. Human connection shares a matched person's Telegram contact URL; bot-relayed human chat is outside this specification.

## 2. Users, contexts, and journeys

### 2.1 Roles

All people have one shared identity. Operational roles are NBNC, server, leader, and staff, with capability inheritance `staff > leader > server`. Role is stored once on that shared user identity; the operational profile does not duplicate it. Admin is an independent superuser capability and does not automatically make someone a matching target.

Operational profiles store login identity, interests, `cg_name`, contact URL, availability, capacity, and service attendance. NBNCs do not provide DOBs. Prefilled operational accounts authenticate using normalized name and DOB.

### 2.2 NBNC onboarding

An unknown Telegram user who sends `/start` or ordinary text receives a welcome, is asked how the bot may address them, and has the next reply stored exactly as their name. An existing user who sends `/start` is welcomed back and may continue or change their name.

Outside an active service, an NBNC can request directions to Star and information about NCC. During a service, the service's approved checkpoint and timestamp flows govern the available experience.

### 2.3 Operational-user journey

`/login` asks for name and DOB and attaches the Telegram account only if the matching operational profile is unoccupied. First login asks for interests and conversation topics. `/manage` offers “Edit interests.” `/logout` detaches the Telegram account. Slash commands exist only when a configured command-triggered flow exposes them.

### 2.4 Human connection

The bot asks for one interest and whether the NBNC wants to join the human or have the human join them. It ranks only attending users whose operational role is exactly `server` and who have available capacity. Leaders and staff are excluded from normal matching. The chosen server is assigned without accept or decline, notified with limited NBNC context, and their Telegram URL is shared with the NBNC.

The NBNC may report that the human is not responding. The bot releases and notifies the old match, excludes them from the next attempt, and rematches. If no normal server qualifies, the bot says nobody is currently available.

Safety requests use the same matching foundation but target staff or leaders who are attending or marked always available. If none qualify, the bot sends configured urgent-support information, alerts all admins, and keeps the request pending.

## 3. Frontend experience

Telegram is the only MVP interface. The experience uses private messages, inline buttons, photos, native reply context, and Telegram activity indicators.

Button IDs are stable and namespaced. Non-service reusable buttons may be used later. Service buttons stop at the service interaction boundary and respond that the service is over.

The bot uses warm, direct onboarding language and calm, actionable error language. It never exposes internal error details, secrets, DOBs, or another person's Telegram ID. The default error response includes the affected person's own Telegram ID as its error-log reference. When routing context is ambiguous, it asks: “Sorry, which message were you referring to?” When no configured flow matches, it says: “Sorry, I didn't understand your request.”

## 4. Application and domain behavior

### 4.1 Discussion flows

A discussion flow contains an optional trigger, ordered typed actions, recursive next flows, a next-flow mode, and checkpoint return actions when applicable.

- `ONE_AND_ONCE_ONLY` permits one successful choice and is not retained in past.
- `ALLOW_MANY` remains reusable in past, including repeating the same choice later.
- `CHECKPOINT` remains reusable and receives control when a descendant branch ends or the user says never mind. A checkpoint may have no local children when its available options belong to an event-level or global selection.

System-global and service-global roots are automatic checkpoints. Every timestamp has one automatic root flow. Empty action lists may be used to expose children without sending or processing anything.

The application persists every open child group. Current groups are a soft routing preference; past reusable and global groups remain eligible. Native reply text is additional context, not a hard routing boundary. One incoming message may select several different configured flows only when it contains several independent requests; one request selects only its most specific configured flow.

Actions may emit one terminal `ActionEvent`. The harness deterministically selects a matching event-triggered direct child without consulting the LLM. Action events do not bubble through ancestors. An unexpected failure emits the reserved `error` event; a direct child may handle it, otherwise the harness invokes its non-configurable hardcoded error sender: “Sorry, an error occurred. Error log: {telegram_user_id}.” The default error path is not a discussion flow.

### 4.2 Services

Services define highkey behavior, door times, an interaction boundary, a service checkpoint, a latecomer flow, timestamp roots, attendees, and delivery audiences.

One highkey service before door close enrolls automatically. Multiple services with any highkey require a forced service choice. Lowkey-only services require confirmation. After door close but before interaction end, confirmation still enrolls the NBNC and runs the latecomer flow. One overlapping service is active per NBNC.

Timestamp audiences are all NBNCs, all servers, all leaders, service NBNCs, service servers, service leaders, or all service attendees. Role inheritance applies to operational audiences.

Zone X is the first seeded development service and end-to-end acceptance fixture. It provides directions, what-to-expect information, human connection before service, constrained fixed-answer service questions during service, and human connection after service. Its implemented configuration must be equivalent to the canonical [Zone X service document](../examples/zone-x-service-example.md) and live in statically typed Python modules that export the system-global and service objects.

### 4.3 Records and persistence

PostgreSQL stores users, operational profiles and logins, service configuration and attendance, immutable flow versions, open flow selections, conversations and persona cursors, human matches and capacity, Telegram update state, timestamp deliveries, outbound delivery attempts, and sanitized diagnostics.

Full conversations are retained long term. Personas summarize the user's disclosed context without deleting source messages.

## 5. Processing, integrations, and derived behavior

Telegram updates arrive through long polling. Processing is idempotent and serialized per user. A local scheduler delivers due timestamp roots and catches up overdue work after restart. A durable delivery worker records Telegram send attempts.

OpenRouter routes messages and ranks human matches. It receives persona, unsummarized conversation context, replied-to text, and all open configured choices. It returns only valid configured flow identifiers, reserved harness outcomes, and replies for addressed configured actions. A `send_message_paraphrased` reply directly answers the question while retaining every fact, qualification, and instruction in its text. A `send_message_llm` reply uses only the facts in its action `source`; it does not rely on prior knowledge, the candidate gist, user assertions, or inference. The harness validates the result locally and renders configured content.

Persona regeneration occurs after 48 hours of inactivity or the model-relative unsummarized-token threshold configured in `hyperparameters.py`.

## 6. Reliability, security, privacy, and observability

The application never sends structured Telegram IDs or DOBs to OpenRouter. The user's name is the only structured direct identifier included; user-authored message content may be sent as written. All OpenRouter requests require zero data retention and denied provider collection and fail closed when those constraints cannot be met.

Secrets are external to source control and redacted from logs and notifications. Database constraints protect login ownership, service activity, matching capacity, flow keys, and idempotency. Conversation and operational data require application authorization and restricted local database access.

Every emitted application error and debug diagnostic creates a sanitized notification for every admin and a structured diagnostic record. Action failures stop later actions and retry safe failures. After retry exhaustion, the direct `error` child runs when configured; otherwise the harness calls the hardcoded error sender. That sender is application code, not configurable JSON or a root discussion flow, and leaves the failed selection available for a safe retry. The Telegram user ID used as the error-log reference is rendered locally and is never sent to OpenRouter.

## 7. Acceptance requirements

- New and existing NBNC onboarding follows the approved name and service rules.
- Operational login, first-login interests, management, and logout preserve exclusive Telegram attachment.
- Recursive flows validate, publish immutably, and execute only registered triggers and actions.
- Action events select only matching direct child flows; custom and default error paths behave without ancestor bubbling.
- Current, reusable-past, and global selections route according to their approved importance and reuse semantics.
- Leaves and never-mind requests return through the nearest checkpoint on the selected branch.
- Service enrollment, overlaps, latecomers, timestamps, audiences, and expiry behave as specified.
- The canonical Zone X Python seed modules expose the approved before, during, and after behaviors with locally validated paraphrased or source-grounded model replies and pass the end-to-end service acceptance suite.
- Normal, rematch, capacity, and safety matching never select ineligible people.
- Restart recovery does not duplicate logical Telegram updates or timestamp delivery.
- OpenRouter prompt construction and provider policy enforce the approved privacy boundary.
- All failures use fixed user-facing copy and produce sanitized admin diagnostics.

## 8. Product boundaries

The product specification excludes hosted deployment, webhook operation, Telegram Serverless, an admin UI, bot-relayed human chat, accept/decline and timeout workflows, stronger server verification, unbounded user-facing LLM answers, NBNC subtype distinctions, automated normal-match escalation, and conversation-deletion UI.
