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
    ENABLE_LOCAL_AUTH: bool = False
    CORS_ORIGINS: str = "*"

    # External Webhooks
    TV_WEBHOOK_SECRET: str | None = None

    # ✅ NEW (ADR-003): URL for the AI microservice
    # This setting is now loaded from the .env file
    AI_SERVICE_URL: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    METRICS_ENABLED: bool = True

    # R3 commercial gate: keep billing/charging disabled until Alpha retention approval.
    BILLING_ENABLED: bool = False

    # R5 commercial gate: automatic execution and Copy Trading are prohibited until
    # a separately approved commercial decision; no current code path reads this as true.
    COPY_TRADING_ENABLED: bool = False
    AUTO_TRADE_ENABLED: bool = False
    TRADE_LIVE_ENABLED: bool = False

    # Historical connector gate: disabled until owner approval and connector acceptance.
    HISTORY_CONNECTOR_ENABLED: bool = False

    # Temporal Forward Decisioning gate: metadata/explainability can ship before routing changes.
    TEMPORAL_DECISIONING_ENABLED: bool = False
    HISTORY_READER_ACCOUNT_ALIAS: str | None = None
    HISTORY_SESSION_SECRET_REF: str | None = None
    HISTORY_ALLOWED_CHANNEL_IDS: str = ""


settings = Settings()


def validate_r5_noncommercial_controls() -> dict[str, bool]:
    """Fail closed if any prohibited commercial/execution flag is enabled during R5 HOLD."""
    controls = {
        "billing_enabled": settings.BILLING_ENABLED,
        "copy_trading_enabled": settings.COPY_TRADING_ENABLED,
        "auto_trade_enabled": settings.AUTO_TRADE_ENABLED,
        "trade_live_enabled": settings.TRADE_LIVE_ENABLED,
    }
    enabled = [name for name, value in controls.items() if value]
    if enabled:
        raise RuntimeError(f"R5 noncommercial gate rejected enabled controls: {', '.join(enabled)}")
    return controls

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
