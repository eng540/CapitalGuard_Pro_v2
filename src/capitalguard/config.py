from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: str = Field(default="dev")
    DATABASE_URL: str = Field(default="sqlite:///./dev.db")

    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    API_KEY: str | None = None
    CORS_ORIGINS: str = "*"

    TV_WEBHOOK_SECRET: str | None = None

    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True

settings = Settings()
