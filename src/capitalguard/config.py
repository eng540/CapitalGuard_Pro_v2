# --- START OF FINAL, COMPLETE, AND MONETIZATION-READY FILE (Version 13.1.0) ---
# src/capitalguard/config.py

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
    TELEGRAM_WEBHOOK_URL: str | None = None     # e.g. https://your-app.up.railway.app/webhook/telegram

    # ✅ NEW: Admin configuration for access control and contact.
    ADMIN_USERNAMES: str | None = None          # Comma-separated list of admin usernames (without @)
    ADMIN_CONTACT: str | None = None            # The public contact handle for the admin (e.g., @AdminUsername)

    # ❌ REMOVED: Legacy variable, no longer used by the new DB-based access control.
    # TELEGRAM_ALLOWED_USERS: str | None = None

    # API / Security
    API_KEY: str | None = None
    CORS_ORIGINS: str = "*"

    # External Webhooks
    TV_WEBHOOK_SECRET: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True


settings = Settings()

# --- END OF FINAL, COMPLETE, AND MONETIZATION-READY FILE ---