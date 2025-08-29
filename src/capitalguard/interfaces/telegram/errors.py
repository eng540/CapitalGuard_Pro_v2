#--- START OF FILE: src/capitalguard/interfaces/telegram/errors.py ---
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes

log = logging.getLogger(__name__)

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # يسجّل الاستثناء بالتفاصيل للمطور
    log.exception("Unhandled Telegram error", exc_info=context.error)
    # محاولة إبلاغ المستخدم برسالة ودّية (إن أمكن)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ حدث خطأ غير متوقع. تم تسجيله، يرجى المحاولة مجددًا."
            )
    except Exception:
        pass

def register_error_handler(application: Application) -> None:
    application.add_error_handler(_error_handler)
#--- END OF FILE ---