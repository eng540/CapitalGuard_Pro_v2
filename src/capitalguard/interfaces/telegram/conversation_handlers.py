# --- START OF FINAL MODIFIED FILE (V6): src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import logging
import uuid
from typing import List, Dict, Any, Set
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    review_final_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, build_channel_picker_keyboard
)
from .commands import (
    main_creation_keyboard, change_method_keyboard,
    newrec_entry_point, settings_cmd
)
from .parsers import parse_quick_command, parse_text_editor
from .auth import ALLOWED_USER_FILTER

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

# --- State Definitions ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW, I_PARTIAL_PROFIT_PERCENT, I_PARTIAL_PROFIT_PRICE) = range(3, 11)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"
REV_TOKENS_MAP = "review_tokens_map"
REV_TOKENS_REVERSE = "review_tokens_rev"

# --- Helper Functions ---
def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """A centralized function to clean up all conversation-related data."""
    review_key = context.user_data.pop('current_review_key', None)
    if review_key: context.bot_data.pop(review_key, None)
    review_token = context.user_data.pop('current_review_token', None)
    if review_token: context.user_data.pop(f"pubsel:{review_token}", None)
    for key in (CONVERSATION_DATA_KEY, 'last_interactive_message_id', 'original_query_message'):
        context.user_data.pop(key, None)

# ... (Other helpers like _ensure_token_maps, _get_or_make_token_for_review, etc. remain the same) ...
def _ensure_token_maps(context: ContextTypes.DEFAULT_TYPE) -> None:
    if REV_TOKENS_MAP not in context.bot_data: context.bot_data[REV_TOKENS_MAP] = {}
    if REV_TOKENS_REVERSE not in context.bot_data: context.bot_data[REV_TOKENS_REVERSE] = {}
def _get_or_make_token_for_review(context: ContextTypes.DEFAULT_TYPE, review_key: str) -> str:
    _ensure_token_maps(context)
    rev_map: Dict[str, str] = context.bot_data[REV_TOKENS_REVERSE]
    tok_map: Dict[str, str] = context.bot_data[REV_TOKENS_MAP]
    if review_key in rev_map: return rev_map[review_key]
    candidate = uuid.uuid4().hex[:8]
    while candidate in tok_map: candidate = uuid.uuid4().hex[:8]
    tok_map[candidate] = review_key
    rev_map[review_key] = candidate
    return candidate
def _resolve_review_key_from_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> str | None:
    _ensure_token_maps(context)
    return context.bot_data[REV_TOKENS_MAP].get(token)
def _load_user_active_channels(user_tg_id: int) -> List[Dict[str, Any]]:
    with SessionLocal() as s:
        user = UserRepository(s).find_or_create(user_tg_id)
        channels = ChannelRepository(s).list_by_user(user.id, only_active=True)
        return [{"id": ch.id, "telegram_channel_id": int(ch.telegram_channel_id), "username": ch.username, "title": ch.title} for ch in channels]

# --- Core Conversation Handlers ---

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    # ... (This function remains the same) ...
    return I_REVIEW

async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("جارٍ الحفظ والنشر...")
    token = query.data.split(":")[2]
    review_key = _resolve_review_key_from_token(context, token)
    draft = context.bot_data.get(review_key) if review_key else None
    if not draft:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec.")
        return ConversationHandler.END
    
    trade_service = get_service(context, "trade_service")
    try:
        # The service is now silent, so the handler is responsible for the final message.
        saved_rec = trade_service.create_recommendation(**draft, user_id=str(update.effective_user.id))
        _, report = trade_service.publish_recommendation(rec_id=saved_rec.id, user_id=str(update.effective_user.id))
        
        if report.get("success"):
            await query.edit_message_text(f"✅ تم الحفظ والنشر بنجاح للتوصية #{saved_rec.id}.")
        else:
            await query.edit_message_text(f"⚠️ تم حفظ التوصية #{saved_rec.id}، ولكن فشل النشر (قد لا تكون هناك قنوات مرتبطة).")
    except Exception as e:
        log.exception("Handler failed to save/publish recommendation.")
        await query.edit_message_text(f"❌ فشل الحفظ/النشر: {e}")
    finally:
        _clean_conversation_state(context)
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    await update.message.reply_text("تم إلغاء المحادثة الحالية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def unexpected_input_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get(CONVERSATION_DATA_KEY) or context.user_data.get('current_review_key'):
        if update.message:
            await update.message.reply_text("⚠️ مدخل غير متوقع. تم إنهاء المحادثة الحالية.")
    _clean_conversation_state(context)
    return ConversationHandler.END

# ... (Other handlers like channel picker, method selection, interactive builder remain largely the same,
# but now they correctly lead to the silent services and clean up state) ...

def register_conversation_handlers(app: Application):
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_USER_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER),
        ],
        states={
            # ... (All states remain the same) ...
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            MessageHandler(filters.COMMAND, unexpected_input_fallback),
            CallbackQueryHandler(unexpected_input_fallback),
        ],
        name="new_recommendation_conversation",
        persistent=True,
        per_user=True,
        per_chat=False,
        per_message=False,
    )
    app.add_handler(creation_conv_handler)
# --- END OF FINAL MODIFIED FILE (V6) ---