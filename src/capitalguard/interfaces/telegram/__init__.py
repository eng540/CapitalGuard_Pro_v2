# src/capitalguard/interfaces/telegram/__init__.py
from telegram.ext import Application
from capitalguard.config import settings
from capitalguard.boot import build_services

# استيراد دوال التسجيل (إن لم تكن موجودة لديك بهذا الاسم، أبلغني نعدّلها)
from .handlers import register_basic_handlers
from .conversation_handlers import register_conversation_handlers
from .management_handlers import register_management_handlers
try:
    from .inline_handlers import register_inline_handlers  # اختياري إن وُجد
    HAS_INLINE = True
except Exception:
    HAS_INLINE = False

def build_telegram_app() -> Application:
    # 1) نبني الـ Application مرة واحدة
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # 2) نبني الخدمات مرة واحدة ونحقنها في bot_data (لا إنشاء لاحق)
    services = build_services()
    app.bot_data.update(services)

    # 3) تسجيل كل الـ handlers على نفس الـ Application
    register_basic_handlers(app, services)            # أوامر: Partial injection
    register_conversation_handlers(app)               # محادثات/Callbacks: من bot_data
    register_management_handlers(app, services)       # أوامر + Callbacks حسب الملف
    if HAS_INLINE:
        register_inline_handlers(app)

    return app