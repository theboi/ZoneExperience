# Friendly Bot MVP: operator launch guide

This is the local operator runbook for the Friendly Bot MVP. Run every command from this directory:

```sh
cd /path/to/ZoneExperience/friendly-bot
```

This is a live Telegram runtime, not a harmless preview. Starting it clears a configured webhook before long polling (while preserving pending updates), processes queued deliveries, and runs scheduled work. Use the intended bot token and database only.

## Runtime boundary

The local database is deliberately isolated to the macOS user and checkout that invoke the runtime guard:

| Setting | Required value |
| --- | --- |
| macOS user ID | The invoking user's `id -u` |
| Compose project and network | `friendly-bot-u<uid>` |
| PostgreSQL volume | `friendly-bot-u<uid>-postgres` |
| PostgreSQL address | `127.0.0.1:5833` |
| Local PostgreSQL credentials | `.runtime/u<uid>/postgres.env` |

Do not override these values, change the port, or point the bot at an arbitrary or shared database. The guarded runtime script rejects a different account, checkout, project, network, volume, or port before it invokes Docker.

## Prerequisites

- Run the guarded commands as the macOS user who owns this checkout. The guard derives the local UID namespace automatically.
- Install Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).
- Install Colima, the Docker CLI, and Docker Compose with Homebrew; Docker Desktop is not required:

  ```sh
  brew install colima docker docker-compose
  colima start
  docker context show
  docker compose version
  ```

  `docker context show` must print `colima`. If `docker compose version` reports an unknown command after the Homebrew install, merge a `cliPluginsExtraDirs` entry for `$(brew --prefix docker-compose)/lib/docker/cli-plugins` into `~/.docker/config.json`, preserving its existing settings, then rerun the command.
- Have a dedicated Telegram bot token and an OpenRouter API key ready. Never commit either value.
- Confirm the OpenRouter account/key privacy setting excludes Friendly Bot input/output logging (or logging is disabled globally). The application fails closed without the attestation below.

## Launch

1. Install the pinned project environment and validate the guarded database namespace.

   ```sh
   uv sync
   uv run python scripts/db_runtime_check.py verify
   ```

   If this reports `Docker Compose v2 plugin is unavailable`, start Colima, confirm `docker context show` prints `colima`, then install or register the Homebrew Compose plugin as described above and rerun the command. Do not work around the guard with a differently named Compose project or another database.

   Else try restarting/starting `colima stop -f; colima start` first.

2. Start the local PostgreSQL container. On its first run, the guard creates a mode-`600`, ignored credential file at `.runtime/u<uid>/postgres.env`, where `<uid>` is the value of `id -u`.

   ```sh
   uv run python scripts/db_runtime_check.py up
   ```

3. Create the local environment file and replace every placeholder. Copy the PostgreSQL password from the generated `.runtime/u<uid>/postgres.env` file into the database URL; do not paste it into source control, logs, tickets, or chat.

   ```sh
   cp .env.example .env
   ```

   Set these values in `.env`:

   ```dotenv
   FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://friendly_bot:<POSTGRES_PASSWORD>@127.0.0.1:5833/friendly_bot
   TELEGRAM_BOT_TOKEN=<dedicated_telegram_bot_token>
   OPENROUTER_API_KEY=<openrouter_api_key>
   OPENROUTER_MODEL=google/gemini-2.5-flash
   FRIENDLY_BOT_OPENROUTER_ENFORCE_ZDR=true
   FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION=disabled-globally-or-friendly-bot-key-excluded
   ```

   The default model supports strict JSON-schema output through a ZDR-capable provider. For an explicitly approved non-ZDR test, set `OPENROUTER_MODEL=qwen/qwen3.7-flash` and `FRIENDLY_BOT_OPENROUTER_ENFORCE_ZDR=false`. Leave `data_collection` protection enabled; restore ZDR before normal use.

   The literal attestation value is required. It does not contain a secret; it records the operator's confirmation that the configured OpenRouter account/key meets the privacy requirement.

4. Validate configuration locally, then apply the schema migrations. The validation command does not contact Telegram or OpenRouter and does not print credentials.

   ```sh
   uv run python -c 'from friendly_bot.config.settings import DatabaseSettings, TelegramSettings; from friendly_bot.routing.openrouter_gateway import OpenRouterGateway; DatabaseSettings(); TelegramSettings(); OpenRouterGateway.from_environment(); print("configuration accepted")'
   uv run alembic upgrade head
   ```

   If the migration reports that PostgreSQL is not accepting connections yet, wait for the container health check to become healthy and rerun the same migration command. Do not change the configured port.

5. Start the runtime.

   ```sh
   uv run python -m friendly_bot
   ```

   The process publishes the bundled Zone X seed idempotently, verifies Telegram webhook state, obtains one PostgreSQL advisory lock, then runs polling, outbox delivery, and service scheduling together. A second process against the same database should refuse to start rather than compete for Telegram updates.

   The authored configuration lives in `seeds/system_global.py` and `seeds/services/zone_x.py`. Each module exports one statically typed object and is validated through the same Pydantic publication boundary before any flow is stored. `uv run mypy` checks both seed modules and application source.

   To inspect OpenRouter routing during local debugging, run:

   ```sh
   uv run python -m friendly_bot --debug
   ```

   This prints each LLM prompt and its formatted response JSON to that terminal. It can include user message content and must only be used in a private local terminal.

6. Before inviting real users, send `/start` from a controlled Telegram test account. An unnamed account receives the welcome/name prompt; reply with a name and confirm the appropriate Zone X or system flow opens. A named account opens the system prompt directly. Leave the process running only if this check succeeds.

To reset the database, run
```sh
uv run python scripts/db_runtime_check.py reset --confirm-reset
uv run alembic upgrade head
```

## Stopping and local data lifecycle

Press `Ctrl-C` in the terminal running Friendly Bot. It stops the polling, outbox, and scheduler loops and releases the runtime lock.

To stop PostgreSQL while preserving the local MVP data:

```sh
uv run python scripts/db_runtime_check.py down
```

To delete all local PostgreSQL data and immediately recreate the container, stop Friendly Bot first, then run:

```sh
uv run python scripts/db_runtime_check.py reset --confirm-reset
```

`reset` deletes the local `friendly-bot-u<uid>-postgres` volume. It is irreversible for this local database; use it only when a clean MVP database is intended.

If the generated `.runtime/u<uid>/postgres.env` file is missing, `reset` creates a new one after deleting the old volume. Before running migrations, replace the password in `FRIENDLY_BOT_DATABASE_URL` in `.env` with the new `POSTGRES_PASSWORD` from that file.

## Operator failure guide

| Symptom | Correct response |
| --- | --- |
| `Docker Compose v2 plugin is unavailable` | Start Colima, confirm the `colima` Docker context, then install/register the Homebrew Compose plugin and rerun the guarded command. Docker Desktop is not required. |
| `unexpected macOS user`, `unexpected checkout`, or another `unexpected runtime` error | Run from the intended `ZoneExperience/friendly-bot` checkout as its owning macOS user; do not pass overrides to bypass the guard. |
| Alembic cannot connect to PostgreSQL | Confirm the guarded `up` command completed, use the generated local password in `.env`, and keep `127.0.0.1:5833`. |
| `OpenRouter configuration is invalid` or a privacy-attestation error | Verify the API key is present and the attestation exactly matches the value shown above; recheck the OpenRouter privacy setting. |
| `could not load Python seed module` or `Python seed module must export an object` | Check the seed file for a valid Python import and the expected named exported seed object. Run `uv run mypy` to find static type errors before starting the bot. |
| An error message names an error log | Open the named file under `.runtime/error-logs/`. It contains the local traceback and safe context, is permission-restricted, and is already excluded from Git. |
| OpenRouter routing is temporarily unavailable or returns an invalid response | Friendly Bot records a redacted diagnostic, writes a local error log, sends its filename to the user, commits that update, and continues polling. Restore the provider rather than weakening the privacy configuration. |
| Telegram webhook preflight fails | Check the dedicated bot token and Telegram connectivity. The runtime will not poll unless it can safely inspect and, when configured, clear the webhook. |
| `telegram_runtime_already_running` | Another Friendly Bot process owns the database's polling lock. Stop that process; do not run two pollers. |

The `.env` file and `.runtime/` directory are intentionally ignored by Git. Keep secrets there only and rotate a credential immediately if it is exposed.
