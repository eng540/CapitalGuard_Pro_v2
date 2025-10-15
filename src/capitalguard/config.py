# --- START OF FINAL, COMPLETE, AND SECURE FILE (Version 13.4.0) ---
# src/capitalguard/config.py

from pantic import Field
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
    
    # ✅ CRITICAL FIX: Directly use the REDIS_URL provided by Railway.
    # This is the simplest and most robust way to connect.
    # The default value is for local development. Railway will override this.
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    TELEGRAM_WEBHOOK_URL: str | None = None
    TELEGRAM_CHANNEL_INVITE_LINK: str | None = None
    TELEGRAM_ADMIN_CHAT_ID: str | None = None

    # Admin configuration
    ADMIN_USERNAMES: str | None = None
    ADMIN_CONTACT: str | None = None

    # API / Security
    API_KEY: str | None = None
    CORS_ORIGINS: str = "*"

    # External Webhooks
    TV_WEBHOOK_SECRET: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True


settings = Settings()

# --- END OF FINAL, COMPLETE, AND SECURE FILE ---