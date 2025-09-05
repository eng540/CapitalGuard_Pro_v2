# --- START OF FILE: src/capitalguard/interfaces/telegram/handlers.py ---
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from .commands import register_commands
from .management_handlers import register_management_handlers
from .conversation_handlers import register_conversation_handlers
from capitalguard.infrastructure.db.user_repository import UserRepository

async def register_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the callback from the 'Agree and Start' button.
    Registers the user and edits the message to confirm.
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the button press

    user_repo = UserRepository()
    telegram_id = update.effective_user.id
    
    user = user_repo.find_by_telegram_id(telegram_id)
    if not user:
        # Register the user with the default type 'trader'
        user_repo.register_user(telegram_id=telegram_id, user_type='trader')
        confirmation_text = "✅ <b>تم تسجيلك بنجاح!</b>\n\nأهلاً بك في CapitalGuard. استخدم /help الآن لعرض الأوامر المتاحة."
        await query.edit_message_text(text=confirmation_text, parse_mode='HTML')
    else:
        # User might have clicked the button again
        await query.edit_message_text(text="أنت مسجل بالفعل. استخدم /help للمتابعة.", parse_mode='HTML')


def register_all_handlers(application: Application):
    """The central function that collects and registers all bot handlers."""
    # Register standard command handlers (/start, /help, /open etc.)
    register_commands(application)
    
    # ✅ NEW: Register the handler for the registration confirmation button.
    application.add_handler(CallbackQueryHandler(register_user_callback, pattern="^user_register_confirm$"))

    # Register the main conversation handler for creating recommendations (/newrec, /settings)
    register_conversation_handlers(application)
    
    # Register handlers for managing existing recommendations (button callbacks etc.)
    register_management_handlers(application)
# --- END OF FILE ---