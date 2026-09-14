# Colima Runtime and Lock Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Run Friendly Bot through Colima without Docker Desktop and prevent concurrent startup health checks from falsely losing the PostgreSQL advisory lock.

**Architecture:** Colima remains the Docker engine, and the Docker CLI Compose plugin retains the existing fixed Compose namespace. \`TelegramRuntimeLock\` serializes only its \`SELECT 1\` ownership probes; it continues to use one dedicated non-pooled PostgreSQL session and still fails closed when that session is genuinely lost.

**Tech Stack:** Python 3.12+, asyncio, asyncpg, pytest, Docker Compose v5 plugin, Colima, uv, Ruff, mypy.

## Global Constraints

- Use the fixed \`friendly-bot-ryanthe\` project and network, \`friendly-bot-ryanthe-postgres\` volume, \`127.0.0.1:5833\` address, and \`.runtime/ryanthe/postgres.env\` credential path.
- Keep Docker Desktop out of operator requirements; require Colima, the Docker CLI, and the Homebrew Compose plugin against the \`colima\` context.
- Never stage, print, commit, or push \`.env\` or \`.runtime/\` values.
- A lost advisory-lock session must remain permanently unhealthy and must never be reacquired automatically.
- Every production behavior change follows red-green verification before refactoring.

---

## File Structure

- Modify \`src/friendly_bot/telegram/runtime_lock.py\`: serialize health probes that share the dedicated asyncpg session.
- Modify \`tests/unit/telegram/test_runtime_lock_unit.py\`: prove concurrent health calls do not overlap on the session and preserve healthy ownership.
- Modify \`.env.example\`: align the local placeholder URL with the guarded PostgreSQL username and host port.
- Modify \`tests/unit/config/test_settings.py\`: make the shared dotenv test reject the former username and port.
- Modify \`README.md\`: replace Docker Desktop instructions and failure guidance with the Colima plus Compose-plugin setup.

### Task 1: Serialize runtime-lock health checks

**Files:**
- Modify: \`src/friendly_bot/telegram/runtime_lock.py:52-134\`
- Modify: \`tests/unit/telegram/test_runtime_lock_unit.py:16-203\`

**Interfaces:**
- Consumes: \`TelegramRuntimeLock.ensure_healthy() -> None\` called concurrently by \`_poll_forever\`, \`_outbox_forever\`, and \`_schedule_forever\`.
- Produces: \`ensure_healthy()\` permits concurrent callers to complete sequentially on one live connection, while preserving \`TelegramRuntimeLockLostError\` for a closed, failed, or invalid connection.

- [x] **Step 1: Write the failing test**

Add a test-only connection whose first health \`fetchval()\` waits on an event and whose second overlapping health query raises \`RuntimeError\`. Start one \`ensure_healthy()\` task, wait until its query begins, start a second task, release the first query, and require both tasks to complete without closing the connection.

\`\`\`python
async def test_concurrent_health_checks_are_serialized_on_the_lock_session() -> None:
    connection = SerializedHealthConnection()
    lock = await TelegramRuntimeLock.acquire(lambda: _factory(connection))

    first = asyncio.create_task(lock.ensure_healthy())
    await connection.health_started.wait()
    second = asyncio.create_task(lock.ensure_healthy())
    await asyncio.sleep(0)
    connection.release_health.set()

    await asyncio.gather(first, second)

    assert connection.closed is False
    assert connection.health_query_count == 2
\`\`\`

- [x] **Step 2: Run test to verify it fails**

Run: \`uv run pytest --basetemp /private/tmp/friendly-bot-runtime-lock tests/unit/telegram/test_runtime_lock_unit.py::test_concurrent_health_checks_are_serialized_on_the_lock_session -q\`

Expected: FAIL with \`TelegramRuntimeLockLostError\`, because the second \`ensure_healthy()\` reaches the same direct connection before the first \`SELECT 1\` completes.

- [x] **Step 3: Write minimal implementation**

Initialize \`self._health_probe_lock = asyncio.Lock()\` in \`TelegramRuntimeLock.__init__\`. Wrap the existing body of \`ensure_healthy()\` in \`async with self._health_probe_lock:\` without changing its loss, close, result-validation, or cancellation branches.

\`\`\`python
async def ensure_healthy(self) -> None:
    async with self._health_probe_lock:
        connection = self._required_live_connection()
        if connection.is_closed():
            await self._lose_health_connection()
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")
        try:
            result = await connection.fetchval(_HEALTH_SQL)
        except asyncio.CancelledError:
            await self._lose_health_connection()
            raise
        except Exception as error:
            await self._lose_health_connection()
            raise TelegramRuntimeLockLostError(
                "runtime_lock_connection_lost"
            ) from error
        if type(result) is not int or result != 1:
            await self._lose_health_connection()
            raise TelegramRuntimeLockLostError("runtime_lock_connection_lost")
\`\`\`

- [x] **Step 4: Run focused lock tests to verify they pass**

Run: \`uv run pytest --basetemp /private/tmp/friendly-bot-runtime-lock tests/unit/telegram/test_runtime_lock_unit.py -q\`

Expected: PASS, including the new concurrent-health test and the existing cancellation and loss tests.

- [x] **Step 5: Commit and push the verified lock change**

\`\`\`bash
git add src/friendly_bot/telegram/runtime_lock.py tests/unit/telegram/test_runtime_lock_unit.py
git commit -m "fix: serialize Telegram runtime lock health checks"
git push origin main
\`\`\`

### Task 2: Align the local runtime template and Colima instructions

**Files:**
- Modify: \`.env.example:4\`
- Modify: \`tests/unit/config/test_settings.py:103-107\`
- Modify: \`README.md:27-30,42,108\`

**Interfaces:**
- Consumes: generated \`POSTGRES_USER=friendly_bot\` and Compose binding \`127.0.0.1:5833:5432\`.
- Produces: the copyable \`.env\` template uses \`friendly_bot\` at port \`5833\`; the runbook requires no Docker Desktop and describes Compose discovery when installed with Homebrew.

- [x] **Step 1: Write the failing template expectation**

Change the shared dotenv assertion to require the guarded values.

\`\`\`python
assert database_settings.redacted_url() == (
    "postgresql+asyncpg://friendly_bot:***@127.0.0.1:5833/friendly_bot"
)
\`\`\`

- [x] **Step 2: Run test to verify it fails**

Run: \`uv run pytest --basetemp /private/tmp/friendly-bot-config tests/unit/config/test_settings.py::test_complete_example_loads_all_settings_from_one_shared_dotenv -q\`

Expected: FAIL because \`.env.example\` still supplies \`friendly_bot_user\` and port \`5432\`.

- [x] **Step 3: Correct the template and runbook**

Set the template URL to:

\`\`\`dotenv
FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://friendly_bot:replace_with_database_password@127.0.0.1:5833/friendly_bot
\`\`\`

Replace Docker Desktop prerequisite and failure-guide text with these operator steps:

\`\`\`sh
brew install colima docker docker-compose
colima start
docker context show
docker compose version
\`\`\`

Explain that \`docker context show\` must print \`colima\`, and that a Homebrew Compose installation may need \`cliPluginsExtraDirs\` to include \`/opt/homebrew/opt/docker-compose/lib/docker/cli-plugins\` in \`~/.docker/config.json\`; existing Docker configuration must be preserved.

- [x] **Step 4: Run configuration and documentation verification**

Run: \`uv run pytest --basetemp /private/tmp/friendly-bot-config tests/unit/config/test_settings.py -q\`

Run: \`docker context show && docker compose version && docker compose -p friendly-bot-ryanthe -f compose.yaml config --quiet\`

Expected: configuration tests PASS; context prints \`colima\`; Compose reports a version; the guarded Compose file validates without output.

- [x] **Step 5: Commit and push the verified operator corrections**

\`\`\`bash
git add .env.example README.md tests/unit/config/test_settings.py
git commit -m "docs: configure Friendly Bot with Colima"
git push origin main
\`\`\`

### Task 3: Verify the repaired runtime before controlled Telegram smoke test

**Files:**
- Modify: no source files
- Test: \`tests/\`, guarded PostgreSQL runtime, Alembic migration runtime

**Interfaces:**
- Consumes: pushed source changes, ignored local \`.env\`, Colima Docker context, and fixed guarded PostgreSQL namespace.
- Produces: evidence that static checks, tests, migrations, direct lock integration, and a stable live runtime startup completed before the operator sends \`/start\`.

- [x] **Step 1: Run full automated verification**

Run: \`uv run pytest --basetemp /private/tmp/friendly-bot-full-tests -q\`

Run: \`uv run ruff check . && uv run ruff format --check . && uv run mypy src/friendly_bot\`

Expected: all tests and static checks PASS. Integration tests requiring \`FRIENDLY_BOT_DATABASE_URL\` in the process environment may skip; their live equivalents run in later steps.

- [ ] **Step 2: Validate the guarded Colima database and migrations**

Run: \`uv run python scripts/db_runtime_check.py verify\`

Run: \`uv run python scripts/db_runtime_check.py up\`

Run: \`uv run alembic upgrade head\`

Expected: the guard accepts the fixed namespace, PostgreSQL becomes healthy on \`127.0.0.1:5833\`, and Alembic reaches \`head\` without emitting credentials.

- [ ] **Step 3: Run direct PostgreSQL lock integration coverage**

Run: \`FRIENDLY_BOT_DATABASE_URL="$(sed -n 's/^FRIENDLY_BOT_DATABASE_URL=//p' .env)" uv run pytest --basetemp /private/tmp/friendly-bot-lock-integration tests/integration/telegram/test_runtime_lock.py -q\`

Expected: PASS. The test verifies exclusive acquisition and genuine connection-loss fail-closed behavior against the guarded PostgreSQL instance.

- [ ] **Step 4: Start and monitor the live runtime**

Run: \`uv run python -m friendly_bot\`

Expected: it stays running through the first scheduler interval without \`TelegramRuntimeLockLostError\`; no credentials appear in output. Stop only if it crashes. Once stable, leave it running and ask the operator to send \`/start\` from the controlled Telegram account.

- [x] **Step 5: Commit and push final verification documentation only if it changes**

If a verification note is added, stage only that note and commit it with \`docs: record Colima runtime verification\`; otherwise leave the verified source commits as the final pushed state.

## Execution Status

- Runtime-lock regression: complete and pushed in `a8df15c`.
- Colima runbook, dotenv template, and portable guard-canary coverage: complete and pushed in `1a4b6f3`.
- Full local verification: 328 passed, 109 skipped because they require unavailable external services or the fixed `ryanthe` runtime profile.
- Guarded database, migration, lock integration, and live Telegram smoke test: blocked outside the fixed UID-501 `/Users/ryanthe/Dev/ZoneExperience` runtime. Do not bypass this guard from another account or checkout.
