# --- START OF FILE: src/capitalguard/interfaces/telegram/errors.py ---
import logging
from telegram import Update
from telegram.ext import ContextTypes

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # يطبع traceback كامل إلى اللوغ
    logging.exception("Unhandled error in PTB handler", exc_info=context.error)

def register_error_handler(application) -> None:
    application.add_error_handler(on_error)
# --- END OF FILE ---