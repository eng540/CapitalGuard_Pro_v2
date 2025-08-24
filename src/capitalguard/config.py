from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Environment / DB
    ENV: str = Field(default="dev")
    DATABASE_URL: str = Field(default="sqlite:///./dev.db")

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    TELEGRAM_ALLOWED_USERS: str | None = None   # comma-separated Telegram user IDs
    TELEGRAM_WEBHOOK_URL: str | None = None     # e.g. https://app.railway.app/webhook/telegram

    # API / Security
    API_KEY: str | None = None
    CORS_ORIGINS: str = "*"

    # External Webhooks
    TV_WEBHOOK_SECRET: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True


settings = Settings()