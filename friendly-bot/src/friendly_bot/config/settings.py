from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRIENDLY_BOT_", env_file=".env")

    database_url: PostgresDsn

    def redacted_url(self) -> str:
        return make_url(str(self.database_url)).render_as_string(hide_password=True)


class TelegramSettings(BaseSettings):
    """Read the Telegram bot token from the runtime environment."""

    model_config = SettingsConfigDict(env_prefix="", env_file=".env")

    telegram_bot_token: SecretStr
