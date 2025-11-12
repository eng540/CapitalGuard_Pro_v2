# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE (Version 14.0.0 - ADR-003) ---
# src/capitalguard/config.py
"""
✅ THE FIX (ADR-003): Added `AI_SERVICE_URL` to the Settings model.
    - This fixes the `AttributeError: 'Settings' object has no attribute 'AI_SERVICE_URL'`
      that was causing a crash loop when the new `ImageParsingService`
      was initialized by `boot.py`.
"""

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
    
    # ❌ REMOVED: REDIS_URL is now read directly in main.py to avoid startup race conditions.

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

    # ✅ NEW (ADR-003): URL for the AI microservice
    # This setting is now loaded from the .env file
    AI_SERVICE_URL: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True


settings = Settings()

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---