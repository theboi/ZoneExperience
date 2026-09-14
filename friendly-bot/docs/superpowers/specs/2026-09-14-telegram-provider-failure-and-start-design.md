# Telegram Provider Failure and Start Design

| Field | Value |
| --- | --- |
| Status | Approved implementation direction from the existing Friendly Bot product authority |
| Scope | Contain OpenRouter routing faults per Telegram update, avoid polling hot loops, and make `/start` deterministic rather than model-routed |
| Product authority | `docs/superpowers/specs/2026-09-11-friendly-bot-mvp-design.md` sections 8.3 and 9.2 |
| Reference implementation | `edenmind-api/src/edenmind/tuition/runtime.py` |

## Problem

An OpenRouter request may exhaust its bounded retry budget.  The gateway deliberately
raises the closed `GatewayTransportError` without response bytes.  Friendly Bot currently
lets that error cross `TelegramIngress`, `TelegramPoller`, and `_poll_forever`; the runtime
`TaskGroup` then cancels the outbox and scheduler and exits.

The same inspection found that the published Zone X seed has no `/start` command trigger,
and `OnboardingService` is not composed at the runtime dispatch boundary.  Consequently
`/start` reaches model routing even though it is a deterministic product command.

## Design

### Fault classes

The runtime distinguishes three classes, following EdenMind's Telegram runtime boundary.

1. A `GatewayTransportError` or `GatewayProtocolError` is an expected, closed routing
   failure.  `FriendlyBotApplication` records a diagnostic with only a static reason code,
   queues the existing code-owned error text, commits the incoming update and cursor, and
   returns a typed failed dispatch result.  It never log-dumps provider content, prompts,
   credentials, or user-authored text.
2. A typed `TelegramApiFailure` returned by `getUpdates` is a safe polling failure.  The
   poll loop preserves its cursor and waits for the existing short idle interval before the
   next poll, rather than busy-looping.
3. Lock loss, configuration refusal, cancellation, database inability to commit, and
   unknown programming faults remain fail-closed.  They cannot be converted into a
   successful consumed update because doing so could conceal an incomplete state mutation
   or permit operation without the singleton lock.

### Deterministic start path

The application dispatches text through `OnboardingService.handle_in_uow` before it opens
normal routing.  For an unnamed user this consumes `/start` or their first ordinary message
and queues the approved welcome/name prompt without calling OpenRouter.  On the next text,
the service stores the exact display name.  The application evaluates the bundled Zone X
service through `ServiceAttendanceService.resolve_for_new_nbnc_in_uow` and opens the selected
service, latecomer, or system root in the same ingress transaction.  An existing user's
`/start` opens the system root directly without OpenRouter.

The existing system-root return copy is made its root action so the no-active-service path
has a configured visible response.  The application uses the already published service
and onboarding services; it creates no new persistence schema, background worker, or
fallback database.

## Acceptance criteria

- A gateway transport or protocol failure produces one redacted diagnostic and one durable
  `Sorry, an error occurred. Error log: <telegram-user-id>.` output, then the update cursor
  commits and the next update can be processed.
- A returned Telegram polling failure preserves the cursor and does not create a hot loop.
- `/start` for an unnamed user does not call the router, queues the approved name prompt,
  and its next text stores the display name before the configured service/system path opens.
- `/start` for a named user does not call the router and opens the system checkpoint.
- Cancellation and true singleton-lock loss still propagate; no broad `except Exception`
  converts them into acknowledged user updates.
- Tests exercise the real application/poller boundaries with controlled provider and
  Telegram doubles; no test inspects provider bodies or credentials.
