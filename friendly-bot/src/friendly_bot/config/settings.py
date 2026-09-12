from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FRIENDLY_BOT_")

    database_url: PostgresDsn

    def redacted_url(self) -> str:
        return make_url(str(self.database_url)).render_as_string(hide_password=True)
