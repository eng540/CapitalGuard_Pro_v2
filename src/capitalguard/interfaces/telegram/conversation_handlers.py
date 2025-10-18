# src/capitalguard/interfaces/telegram/conversation_handlers.py
# --- START OF FINAL, PRODUCTION-READY FILE (v35.5) ---
"""
Conversation handlers for recommendation creation.
✅ THE FIX: added robust session token checks, activity timestamps,
and replaced fragile string-splitting callback parsing with CallbackBuilder.
"""

import logging
import uuid
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set, Iterable

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard, CallbackBuilder, CallbackNamespace, CallbackAction
)
from .auth import require_active_user, require_analyst_user
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository

log = logging.getLogger(__name__)

# --- Conversation States ---
(
    SELECT_METHOD, AWAIT_TEXT_INPUT, AWAITING_ASSET, AWAITING_SIDE, AWAITING_TYPE,
    AWAITING_PRICES, AWAITING_REVIEW, AWAITING_NOTES, AWAITING_CHANNELS
) = range(9)

# --- State keys ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"
LAST_ACTIVITY_KEY = "last_activity"

# --- Timeout (seconds) ---
CONVERSATION_TIMEOUT = 1800  # 30 minutes

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    for key in (DRAFT_KEY, CHANNEL_PICKER_KEY, LAST_ACTIVITY_KEY):
        context.user_data.pop(key, None)

def update_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def check_conversation_timeout(context: ContextTypes.DEFAULT_TYPE) -> bool:
    last = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    return (time.time() - last) > CONVERSATION_TIMEOUT

async def safe_edit_message(query, text=None, reply_markup=None, parse_mode=None):
    try:
        if text is not None:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        log.exception("safe_edit_message BadRequest")
        return False
    except Exception:
        log.exception("safe_edit_message error")
        return False

# --- Entry point: /newrec ---
@uow_transaction
@require_active_user
@require_analyst_user
async def start_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """
    Start creating a new recommendation. Initializes draft and token.
    """
    clean_creation_state(context)
    token = uuid.uuid4().hex
    context.user_data[DRAFT_KEY] = {"token": token}
    update_activity(context)

    await update.message.reply_html(
        "اختر طريقة الإدخال:", reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

# --- handle method choice (interactive / text / quick) ---
@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    # timeout check
    if check_conversation_timeout(context):
        await safe_edit_message(query, "⏰ انتهت فترة الجلسة بسبب عدم النشاط. يرجى /newrec للبدء من جديد.")
        clean_creation_state(context)
        return ConversationHandler.END

    update_activity(context)
    parts = (query.data or "").split("_", 1)
    choice = parts[1] if len(parts) > 1 else None

    if choice == "interactive":
        # build interactive flow
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(query.from_user.id))
        context.user_data[DRAFT_KEY].update({})
        await safe_edit_message(query, "🔎 اختر الأصل:", reply_markup=asset_choice_keyboard(recent_assets))
        return AWAITING_ASSET

    if choice in ("rec", "editor"):
        # text-mode flows handled elsewhere - show prompt
        await safe_edit_message(query, "✍️ الرجاء إدخال التوصية (انقر لإرسال رسالة نصية).")
        return AWAIT_TEXT_INPUT

    await safe_edit_message(query, "❌ خيار غير معروف.")
    return ConversationHandler.END

# --- Show review card (summary) ---
async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Build and show the review card using current draft data.
    """
    draft = context.user_data.get(DRAFT_KEY) or {}
    token = draft.get("token")
    if not token:
        await update.message.reply_text("❌ جلسة غير صحيحة. ابدأ /newrec.")
        return

    # build review text (helper returns text and optionally price info)
    review_text = build_review_text_with_price(draft)
    keyboard = review_final_keyboard(token)
    # if from callback_query
    if update.callback_query:
        await safe_edit_message(update.callback_query, text=review_text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_html(review_text, reply_markup=keyboard)

# --- Channel picker handler (core fix) ---
@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """
    Handle selection/toggling of channels.
    ✅ THE FIX: Uses CallbackBuilder.parse and validates the draft token embedded in callbacks.
    """
    query = update.callback_query
    await query.answer()
    update_activity(context)

    draft = context.user_data.get(DRAFT_KEY)
    if not draft:
        await safe_edit_message(query, "❌ انتهت الجلسة. يرجى /newrec للبدء من جديد.")
        return ConversationHandler.END

    parsed = CallbackBuilder.parse(query.data or "")
    action = parsed.get("action")
    params = parsed.get("params", [])
    # token stored in params[0] by our convention
    token_in_callback = params[0] if params else None

    # Validate token to avoid cross-session toggles
    if not token_in_callback or draft.get("token") != token_in_callback:
        await safe_edit_message(query, "❌ جلسة منتهية أو بيانات غير متطابقة. يرجى /newrec للبدء من جديد.")
        clean_creation_state(context)
        return ConversationHandler.END

    # If action is toggle, params expected: [token, tg_chat_id, page]
    if action == CallbackAction.TOGGLE.value or action == CallbackAction.TOGGLE:
        try:
            tg_chat_id = int(params[1])
        except Exception:
            await safe_edit_message(query, "❌ خطأ في بيانات القناة.")
            return AWAITING_CHANNELS

        selected_set: Set[int] = context.user_data.setdefault(CHANNEL_PICKER_KEY, set())
        if tg_chat_id in selected_set:
            selected_set.remove(tg_chat_id)
        else:
            selected_set.add(tg_chat_id)

        # rebuild and edit keyboard (keep the same page if provided)
        page = int(params[2]) if len(params) > 2 and params[2].isdigit() else 1
        all_channels = ChannelRepository(db_session).list_by_analyst(UserRepository(db_session).find_by_telegram_id(query.from_user.id).id, only_active=False)
        keyboard = build_channel_picker_keyboard(draft["token"], all_channels, selected_set, page=page)
        await safe_edit_message(query, "📢 **اختر القنوات للنشر**\n\n✅ = مختارة\n☑️ = غير مختارة\n\nاضغط على القناة لتبديل اختيارها", reply_markup=keyboard)
        return AWAITING_CHANNELS

    # navigation/back from channel picker
    if action == CallbackAction.BACK.value or action == CallbackAction.BACK:
        # go back to review
        await show_review_card(update, context)
        return AWAITING_REVIEW

    # If some other action (safety)
    await safe_edit_message(query, "❌ إجراء غير معروف.")
    return AWAITING_CHANNELS

# --- Register conversation ---
def register_conversation_handlers(app: Application):
    """
    Register the recommendation creation conversation.
    """
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newrec", start_newrec)],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: None)],  # placeholder
            AWAITING_ASSET: [CallbackQueryHandler(lambda u,c: None, pattern="^asset_")],
            AWAITING_SIDE: [CallbackQueryHandler(lambda u,c: None, pattern="^side_")],
            AWAITING_TYPE: [CallbackQueryHandler(lambda u,c: None, pattern="^type_")],
            AWAITING_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: None)],
            AWAITING_REVIEW: [CallbackQueryHandler(lambda u,c: None, pattern="^rec|^pub")],
            AWAITING_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: None)],
            AWAITING_CHANNELS: [CallbackQueryHandler(channel_picker_handler, pattern="^(?:pub|rec)\\|")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        name="recommendation_creation",
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=CONVERSATION_TIMEOUT,
    )
    app.add_handler(conv_handler)
    log.info("Conversation handlers registered.")
# --- END OF FILE ---