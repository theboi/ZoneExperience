from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from friendly_bot.config.settings import DatabaseSettings, TelegramSettings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRIENDLY_BOT_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_redacted_url_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FRIENDLY_BOT_DATABASE_URL",
        "postgresql+asyncpg://u:secret@127.0.0.1:5832/db",
    )

    assert "secret" not in DatabaseSettings().redacted_url()


def test_database_settings_reads_url_from_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FRIENDLY_BOT_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://dotenv-user:dotenv-password@127.0.0.1:5432/dotenv-db\n"
    )

    assert DatabaseSettings().redacted_url() == (
        "postgresql+asyncpg://dotenv-user:***@127.0.0.1:5432/dotenv-db"
    )


def test_database_settings_environment_overrides_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "FRIENDLY_BOT_DATABASE_URL",
        "postgresql+asyncpg://environment-user:environment-password@127.0.0.1:5432/environment-db",
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "FRIENDLY_BOT_DATABASE_URL=postgresql+asyncpg://dotenv-user:dotenv-password@127.0.0.1:5432/dotenv-db\n"
    )

    assert DatabaseSettings().redacted_url() == (
        "postgresql+asyncpg://environment-user:***@127.0.0.1:5432/environment-db"
    )


def test_telegram_settings_reads_secret_token_from_project_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=dotenv-telegram-token\n")

    telegram_settings = TelegramSettings()

    assert isinstance(telegram_settings.telegram_bot_token, SecretStr)
    assert (
        telegram_settings.telegram_bot_token.get_secret_value()
        == "dotenv-telegram-token"
    )
