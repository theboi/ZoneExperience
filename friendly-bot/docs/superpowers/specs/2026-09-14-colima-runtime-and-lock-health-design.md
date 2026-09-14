# Colima Runtime and Lock Health Design

## Goal

Run Friendly Bot locally with Colima instead of Docker Desktop while retaining
the single guarded PostgreSQL namespace, and prevent a healthy runtime lock
from being incorrectly discarded during concurrent startup checks.

## Selected approach

Colima remains the local Docker engine. The Docker CLI and Compose plugin run
against the `colima` Docker context; Docker Desktop is not required. The
existing `compose.yaml` and guarded `docker compose` lifecycle stay in place,
so the fixed project, network, volume, port, and credentials boundary is not
weakened or duplicated.

The runtime lock continues to own one dedicated non-pooled PostgreSQL session.
Its health checks are serialized inside `TelegramRuntimeLock`, because all
poller, outbox, and scheduler tasks share that one connection and asyncpg does
not allow concurrent queries on it.

## Components

- `README.md` documents Colima, the Docker CLI, and the separately installed
  Compose plugin as prerequisites. It must not instruct operators to install
  Docker Desktop.
- `.env.example` uses the runtime's real PostgreSQL identity:
  `friendly_bot` at `127.0.0.1:5833`. It remains a non-functional placeholder
  because it has no real password.
- `TelegramRuntimeLock` owns an `asyncio.Lock` dedicated to health probes.
  `ensure_healthy()` holds it while checking the existing session, so only one
  `SELECT 1` reaches the connection at a time.
- Runtime tasks continue to call `ensure_healthy()` before each unit of work.
  A real closed or failed session still becomes permanently unhealthy and
  stops all tasks; the change only prevents false failure from overlapping
  health probes.

## Data flow

1. The operator starts Colima and confirms `docker context show` is `colima`.
2. The guarded `up` command uses its fixed Compose project to create the
   loopback-only PostgreSQL container, network, and volume.
3. Friendly Bot acquires the PostgreSQL advisory lock on one direct connection.
4. Poller, outbox, and scheduler each request a health check. The first probe
   completes; later probes wait for it and then query the same still-live
   session.
5. If any serialized probe detects a closed connection, query error, or
   invalid result, the lock is released, marked permanently lost, and the
   runtime stops rather than running without a known lock owner.

## Error handling

- A missing Compose plugin remains an explicit guarded failure; the README
  directs the operator to install it with Homebrew, without Docker Desktop.
- The runtime does not reacquire a lost advisory lock automatically. Restarting
  remains the deliberate recovery action.
- No database project, network, volume, port, or credential path becomes
  configurable through this migration.
- The generated PostgreSQL password and `.env` remain ignored and are never
  logged, staged, committed, or pushed.

## Verification

- Add a unit regression test whose connection double rejects overlapping
  `fetchval()` calls. Concurrent `ensure_healthy()` calls must all complete
  while the session remains open.
- Preserve the tests showing genuine connection loss closes and latches the
  runtime lock.
- Update the shared-dotenv test to assert the corrected example username and
  port.
- Run targeted tests, the full automated suite, Ruff, formatting, and mypy.
- Use the configured Colima PostgreSQL runtime for migration and lock
  integration checks only after the guarded namespace is available.
- Start the live bot only after those checks pass, monitor for a stable startup
  period, and then ask the operator to send `/start` from the controlled test
  account. The operator performs that final externally visible interaction.
