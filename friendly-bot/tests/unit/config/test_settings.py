from pathlib import Path
from shutil import copyfile

import pytest
from pydantic import SecretStr, ValidationError

from friendly_bot.config.settings import DatabaseSettings, TelegramSettings
from friendly_bot.routing.openrouter_gateway import OpenRouterSettings

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RELEVANT_ENVIRONMENT_VARIABLES = (
    "FRIENDLY_BOT_DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "OPENROUTER_API_KEY",
    "FRIENDLY_BOT_OPENROUTER_INPUT_OUTPUT_LOGGING_ATTESTATION",
)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRIENDLY_BOT_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)


def test_redacted_url_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FRIENDLY_BOT_DATABASE_URL",
        "postgresql+asyncpg://u:secret@127.0.0.1:5832/db",
    )

    assert "secret" not in DatabaseSettings().redacted_url()


def test_database_settings_reads_url_from_explicit_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FRIENDLY_BOT_DATABASE_URL", raising=False)
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://dotenv-user:dotenv-password@127.0.0.1:5432/dotenv-db\n"
    )

    assert DatabaseSettings(_env_file=dotenv_file).redacted_url() == (
        "postgresql+asyncpg://dotenv-user:***@127.0.0.1:5432/dotenv-db"
    )


def test_database_settings_environment_overrides_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "FRIENDLY_BOT_DATABASE_URL",
        "postgresql+asyncpg://environment-user:environment-password@127.0.0.1:5432/environment-db",
    )
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://dotenv-user:dotenv-password@127.0.0.1:5432/dotenv-db\n"
    )

    assert DatabaseSettings(_env_file=dotenv_file).redacted_url() == (
        "postgresql+asyncpg://environment-user:***@127.0.0.1:5432/environment-db"
    )


def test_telegram_settings_reads_secret_token_from_explicit_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("TELEGRAM_BOT_TOKEN=dotenv-telegram-token\n")

    telegram_settings = TelegramSettings(_env_file=dotenv_file)

    assert isinstance(telegram_settings.telegram_bot_token, SecretStr)
    assert (
        telegram_settings.telegram_bot_token.get_secret_value()
        == "dotenv-telegram-token"
    )


def test_complete_example_loads_all_settings_from_one_shared_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A copied template must be valid for every settings model that reads it."""

    for variable_name in _RELEVANT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable_name, raising=False)
    dotenv_file = tmp_path / ".env"
    copyfile(_PROJECT_ROOT / ".env.example", dotenv_file)

    loaded_settings: list[DatabaseSettings | TelegramSettings | OpenRouterSettings] = []
    validation_errors: list[ValidationError] = []
    for settings_class in (DatabaseSettings, TelegramSettings, OpenRouterSettings):
        try:
            loaded_settings.append(settings_class(_env_file=dotenv_file))
        except ValidationError as error:
            validation_errors.append(error)

    assert not validation_errors, [error.errors() for error in validation_errors]
    assert len(loaded_settings) == 3

    database_settings = loaded_settings[0]
    assert isinstance(database_settings, DatabaseSettings)
    assert database_settings.redacted_url() == (
        "postgresql+asyncpg://friendly_bot_user:***@127.0.0.1:5432/friendly_bot"
    )

    telegram_settings = loaded_settings[1]
    assert isinstance(telegram_settings, TelegramSettings)
    assert (
        telegram_settings.telegram_bot_token.get_secret_value()
        == "replace_with_telegram_bot_token"
    )

    openrouter_settings = loaded_settings[2]
    assert isinstance(openrouter_settings, OpenRouterSettings)
    assert openrouter_settings.openrouter_api_key is not None
    assert (
        openrouter_settings.openrouter_api_key.get_secret_value()
        == "replace_with_openrouter_api_key"
    )
    assert (
        openrouter_settings.friendly_bot_openrouter_input_output_logging_attestation
        is False
    )


def test_all_settings_anchor_dotenv_to_project_root_outside_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A CWD change must not redirect the configured default dotenv location."""

    monkeypatch.chdir(tmp_path)
    expected_dotenv_file = _PROJECT_ROOT / ".env"

    assert Path(DatabaseSettings.model_config["env_file"]) == expected_dotenv_file
    assert Path(TelegramSettings.model_config["env_file"]) == expected_dotenv_file
    assert Path(OpenRouterSettings.model_config["env_file"]) == expected_dotenv_file
    assert expected_dotenv_file != Path.cwd() / ".env"
