# Friendly Bot Architecture Masterplan

> Living authority for the stable technical boundaries governing Friendly Bot implementation.

| Field | Value |
| --- | --- |
| Status | Approved |
| Authority type | Architecture |
| Scope | Friendly Bot local MVP runtime, persistence, flow engine, integrations, privacy, and recovery |
| Decision owner | Ryan The |
| Effective date | 2026-09-11 |
| Last evidence review | 2026-09-11 at repository commit `47213f1`; implementation absent |
| Related authorities | [`product-specification.md`](product-specification.md) |
| Historical task artifacts | [`../superpowers/specs/`](../superpowers/specs/) |

## 1. Purpose and authority

This masterplan decides Friendly Bot's stable component, data-ownership, integration, execution, privacy, and recovery boundaries. Product journeys and observable behavior belong to the product specification. Detailed task sequencing belongs to dated implementation plans.

## 2. Current state

The Git repository and `friendly-bot/` directory exist, but no Friendly Bot application, schema, runtime, or tests are implemented. The approved target architecture below governs subsequent implementation.

### 2.1 Implemented evidence

| Capability or boundary | State | Evidence | Last verified |
| --- | --- | --- | --- |
| Repository and `main` branch | Implemented | Git commit `47213f1` | 2026-09-11 |
| Friendly Bot application | Absent | No application files present before this authority bootstrap | 2026-09-11 |
| Database schema and migrations | Absent | No migration files present before this authority bootstrap | 2026-09-11 |
| Automated tests | Absent | No test files present before this authority bootstrap | 2026-09-11 |

## 3. Approved decisions and boundaries

### 3.1 Decision register

| ID | Decision | Scope | Owner | Effective date | Supersedes |
| --- | --- | --- | --- | --- | --- |
| ARCH-001 | Run the MVP as one local Python process using Telegram long polling | Runtime | Ryan The | 2026-09-11 | Hosted/serverless proposals |
| ARCH-002 | Run PostgreSQL locally through Docker Compose | Persistence | Ryan The | 2026-09-11 | SQLite and hosted-database proposals |
| ARCH-003 | Store immutable recursive flow definitions as validated versioned JSON and runtime state relationally | Flow engine | Ryan The | 2026-09-11 | Python-only and fully normalized configuration proposals |
| ARCH-004 | Constrain the LLM to valid flow keys; only the harness sends user-facing content | Model boundary | Ryan The | 2026-09-11 | Free-form response proposal |
| ARCH-005 | Represent branch reuse with `ONE_AND_ONCE_ONLY`, `ALLOW_MANY`, and `CHECKPOINT` | Conversation control | Ryan The | 2026-09-11 | Boolean and separate `FlowSelection` proposals |
| ARCH-006 | Assemble current, reusable-past, system-global, and event-global candidates in one open-selection input | Routing state | Ryan The | 2026-09-11 | Stack and armed-trigger-registry proposals |
| ARCH-007 | Use branch checkpoint ancestry for leaf and never-mind returns | Conversation recovery | Ryan The | 2026-09-11 | Single global checkpoint stack proposal |
| ARCH-008 | Use OpenRouter with mandatory ZDR and denied provider collection, excluding structured Telegram IDs and DOBs | Privacy | Ryan The | 2026-09-11 | Unrestricted provider routing |
| ARCH-009 | Reuse EdenMind Telegram durability concepts without copying product-specific tuition behavior | Integration reliability | Ryan The | 2026-09-11 | Greenfield transport behavior |

## 4. Architecture contract

### 4.1 System context

The local Python runtime integrates with Telegram Bot API, local PostgreSQL, and OpenRouter. Telegram and OpenRouter are the only required remote services. The process is started through `python -m friendly_bot`.

### 4.2 Component boundaries

- **Telegram transport:** typed Bot API requests, long polling, webhook clearing, update cursor, update parsing, outbound sends, cadence, and secret redaction.
- **Conversation application:** command dispatch, onboarding, role-aware behavior, event resolution, checkpoint traversal, and action orchestration.
- **Flow domain:** Pydantic definitions for recursive flows, triggers, actions, publication validation, and immutable versions.
- **Routing gateway:** prompt construction, provider privacy controls, key-only response validation, ambiguity/no-match handling, and repeated same-update selection.
- **Event scheduler:** due-timestamp claims, audience expansion, event lifecycle checks, catch-up, and idempotent delivery creation.
- **Matching domain:** eligibility, inherited roles, attendance, availability, capacity, ranking, assignment, rematch exclusion, and safety pending state.
- **Persona service:** inactivity and token-threshold detection, summary generation, durable cursor advancement, and full-history preservation.
- **Persistence:** SQLAlchemy repositories, PostgreSQL transactions and constraints, migrations, per-user serialization, and durable outbox records.
- **Diagnostics:** structured local logging, sanitization, correlation IDs, and Telegram notification of every emitted error and debug diagnostic to every admin.

Each component exposes typed interfaces and does not import transport-specific structures into domain policy.

### 4.3 Flow configuration boundary

`DiscussionFlow` is the sole flow type. Triggers and actions are discriminated unions of registered subclasses. A flow owns its recursive `next_flows`, next-flow mode, and optional checkpoint `return_actions`.

System, event, and timestamp roots have `trigger=None` and are invoked by application context. System and event roots must be checkpoints; each timestamp owns exactly one root flow.

Flow publication creates an immutable version only after full-graph validation. Active open selections remain pinned to their version when a later version is published.

### 4.4 Runtime state boundary

An open-flow-selection row identifies a user, immutable flow version, parent flow key, event scope, current/global flags, and branch/checkpoint ancestry. Candidate children are loaded from the immutable parent definition rather than copied into rows.

Current is a soft LLM importance label. Reusable past and global selections remain eligible. Native Telegram reply text is contextual evidence only; no message-ID-to-flow association is stored.

State transitions and action effects occur under per-user serialization. A selection is removed, moved to past, retained as a checkpoint, or appended to current only after required actions succeed.

### 4.5 Checkpoint boundary

Checkpoint return is branch-local. Every branch begins at the system checkpoint and, when applicable, the current event checkpoint. Timestamp and nested checkpoints extend that path. A leaf returns to its nearest checkpoint. Never mind returns to the nearest checkpoint or pops the current checkpoint when invoked from it.

### 4.6 Persistence ownership

PostgreSQL is authoritative for users, operational profiles, event definitions, flow versions, open selections, checkpoint ancestry, messages, personas, attendance, matches, update offsets, deduplication, deliveries, timing claims, and diagnostics.

Database constraints and transactions, not process memory, enforce uniqueness and idempotency. The process may cache immutable flow versions but must invalidate by version identity rather than mutate cached definitions.

### 4.7 Telegram integration boundary

The runtime adapts the EdenMind implementation's proven concepts: direct typed Bot API transport, durable update offset, update deduplication, durable outbound attempt records, redacted secrets, definite-failure retry, and no blind replay after an ambiguous send.

The runtime clears any webhook before calling `getUpdates`. One local process owns polling. Telegram updates for a user are serialized in update order.

### 4.8 Scheduling boundary

The local scheduler claims due event-recipient deliveries from PostgreSQL. Timing roots are automatic flows. Claim and completion records make logical delivery idempotent. On restart, overdue work is caught up subject to event interaction rules.

### 4.9 Model and privacy boundary

`hyperparameters.py` owns model identity, persona thresholds, routing attempt limits, timeouts, and non-secret tuning values. Secrets are environment-provided.

The routing gateway strips structured Telegram IDs and DOBs from prompt objects. User-authored content may be transmitted. Requests specify ZDR and denied data collection; absence of a compatible endpoint is an error, not permission to weaken policy. The model output is parsed as one allowed key or rejected.

### 4.10 Failure and diagnostics boundary

Every action supports an optional typed fallback flow. Retriable errors use bounded retries and idempotency keys. Later actions do not run after failure. Default user-facing failures are fixed configuration.

Every emitted error and debug diagnostic creates a sanitized diagnostic record and notification fan-out to every admin. Telegram diagnostics contain a correlation ID and safe summary; secrets and sensitive raw payloads remain excluded.

## 5. Dependencies and related authorities

| Authority | Relationship | Required synchronization |
| --- | --- | --- |
| [`product-specification.md`](product-specification.md) | Architecture implements and is constrained by approved product behavior | Update when product behavior changes technical boundaries |
| [`../superpowers/specs/2026-09-11-friendly-bot-mvp-design.md`](../superpowers/specs/2026-09-11-friendly-bot-mvp-design.md) | Historical approved design source | Promote approved changes into both living authorities when changed |

## 6. Unresolved decisions

No material architecture decisions remain unresolved for MVP planning. Concrete library versions, migration sequencing, and module filenames belong to the implementation plan.

## 7. Explicit exclusions and deferred scope

- Hosted runtime, webhooks, managed scheduler, and production deployment topology.
- Telegram Serverless.
- Admin graphical interface.
- Bot-relayed human chat.
- Free-form model responses.
- Strong operational-user identity verification.
- Normal-match escalation when no server qualifies.

## 8. Superseded and historical material

Earlier design exploration considered Telegram Serverless, Cloud Run, Cloud Scheduler, SQLite, a literal trigger stack, a global armed-trigger registry, Telegram-message-ID-to-flow persistence, separate flow subclasses, `FlowSelection`, and boolean reuse flags. These are not current architecture. The dated design specification records only the consolidated approved result.

## 9. Evidence-backed change history

| Date | Change | Basis | Commit or source |
| --- | --- | --- | --- |
| 2026-09-11 | Bootstrapped the approved Friendly Bot local MVP architecture | User-approved design conversation | Dated design specification |
