# --- START OF FILE: src/capitalguard/interfaces/telegram/__init__.py ---
from telegram.ext import Application
from capitalguard.config import settings
from capitalguard.boot import build_services
from .handlers import register_all_handlers

def build_telegram_app() -> Application:
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    services = build_services()
    app.bot_data.update(services)
    register_all_handlers(app, services)
    return app
# --- END OF FILE ---