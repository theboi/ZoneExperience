import pytest
from pydantic import ValidationError

from friendly_bot.config.settings import DatabaseSettings


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
