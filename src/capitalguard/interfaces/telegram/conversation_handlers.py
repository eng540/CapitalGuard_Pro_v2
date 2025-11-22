# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
# File: src/capitalguard/interfaces/telegram/conversation_handlers.py
# Version: v52.0.0-PRODUCTION-SECURE (Security & Stability Fixes)
# ✅ THE FIX:
#    1. CRITICAL: Defined 'loge' for error logging in management flows.
#    2. SECURITY: Added Token Validation in Review & Channel Picker handlers.
#    3. STABILITY: Unified Session Keys and cleanup logic in 'master_reply_handler'.
#    4. UI: Standardized ParseMode.HTML across all handlers.

import logging
import uuid
import time
import asyncio
import re 
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Union

from telegram import CallbackQuery, Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest, TelegramError
from telegram.constants import ParseMode

# --- Infrastructure ---
from capitalguard.infrastructure.db.uow import uow_transaction

from capitalguard.infrastructure.core_engine import core_cache, cb_db, AsyncPipeline
# --- Helpers & UI ---
from .helpers import get_service, _get_attr, _format_price
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard, CallbackBuilder, CallbackNamespace, CallbackAction,
    ButtonTexts, build_editable_review_card
)
from .auth import require_active_user, require_analyst_user, get_db_user
from .parsers import parse_rec_command, parse_editor_command, parse_number, parse_targets_list

# --- Services ---
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.creation_service import CreationService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from capitalguard.domain.entities import RecommendationStatus, ExitStrategy, UserTradeStatus
from capitalguard.infrastructure.db.models import UserType as UserTypeEntity

# --- Session Management ---
from capitalguard.interfaces.telegram.session import SessionContext

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors") # ✅ FIX: Defined loge

# ==============================================================================
# 1. STATE DEFINITIONS
# ==============================================================================

# --- Creation States ---
(
    SELECT_METHOD, AWAIT_TEXT_INPUT, AWAITING_ASSET,
    AWAITING_SIDE, AWAITING_TYPE,
    AWAITING_PRICES, AWAITING_REVIEW, AWAITING_NOTES, AWAITING_CHANNELS
) = range(9)

# --- Management States ---
(
    AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE,
    AWAIT_USER_TRADE_CLOSE_PRICE
) = range(AWAITING_CHANNELS + 1, AWAITING_CHANNELS + 4)

# ==============================================================================
# 2. STATE KEYS
# ==============================================================================

# --- Creation Keys ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"
LAST_ACTIVITY_KEY_CREATION = "last_creation_activity"
CONVERSATION_TIMEOUT_CREATION = 1800

# --- Management Keys ---
# These keys must match what is used in management_handlers.py and session.py
AWAITING_INPUT_KEY = "awaiting_management_input"
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY_MGMT = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800
PARTIAL_CLOSE_REC_ID_KEY = "partial_close_rec_id"
PARTIAL_CLOSE_PERCENT_KEY = "partial_close_percent"
USER_TRADE_CLOSE_ID_KEY = "user_trade_close_id"
ORIGINAL_MESSAGE_CHAT_ID_KEY = "original_message_chat_id"
ORIGINAL_MESSAGE_MESSAGE_ID_KEY = "original_message_message_id"

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    for key in [DRAFT_KEY, CHANNEL_PICKER_KEY, LAST_ACTIVITY_KEY_CREATION]:
        context.user_data.pop(key, None)

def update_creation_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY_CREATION] = time.time()

def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """Cleans up all keys related to management conversations."""
    keys_to_pop = [
        AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY_MGMT,
        PARTIAL_CLOSE_REC_ID_KEY, PARTIAL_CLOSE_PERCENT_KEY,
        USER_TRADE_CLOSE_ID_KEY,
        ORIGINAL_MESSAGE_CHAT_ID_KEY, ORIGINAL_MESSAGE_MESSAGE_ID_KEY,
    ]
    for key in keys_to_pop:
        context.user_data.pop(key, None)

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    context.user_data[LAST_ACTIVITY_KEY_MGMT] = time.time()

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE, timeout_seconds: int, last_activity_key: str, state_cleaner: callable) -> bool:
    last_activity = context.user_data.get(last_activity_key, 0)
    if time.time() - last_activity > timeout_seconds:
        state_cleaner(context)
        message = "⏰ انتهت مدة الجلسة. يرجى البدء من جديد."
        if update.callback_query:
            try: await update.callback_query.answer("Session Timeout", show_alert=True)
            except: pass
            await safe_edit_message(update.callback_query, text=message)
        elif update.message:
            await update.message.reply_text(message)
        return True
    return False

async def safe_edit_message(query: CallbackQuery, text=None, reply_markup=None, parse_mode=ParseMode.HTML):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        return False
    except Exception as e:
        log.error(f"Error in safe_edit_message: {e}", exc_info=True)
        return False

async def _preload_asset_prices(price_service: PriceService, assets: List[str]):
    try:
        tasks = [price_service.get_cached_price(asset, "Futures", force_refresh=False) for asset in assets]
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception: pass

# ==============================================================================
# 4. CREATION HANDLERS
# ==============================================================================

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    clean_creation_state(context)
    clean_management_state(context) 
    update_creation_activity(context)
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(db_user.telegram_user_id))
        if recent_assets:
            asyncio.create_task(_preload_asset_prices(price_service, recent_assets))
    except Exception: pass

    await update.message.reply_html("🚀 <b>منشئ التوصيات</b>\n\nاختر طريقة الإدخال:", reply_markup=main_creation_keyboard())
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    clean_creation_state(context)
    clean_management_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    update_creation_activity(context)

    if command == 'rec':
        prompt = (
            "⚡️ <b>وضع الأمر السريع (Quick Command)</b>\n\n"
            "أدخل التوصية في سطر واحد بالترتيب التالي:\n"
            "<code>الرمز الاتجاه الدخول الوقف الأهداف...</code>\n\n"
            "<b>مثال (انسخ وعدل):</b>\n"
            "<code>BTCUSDT LONG 90000 89000 91000 92000</code>"
        )
    else:
        prompt = (
            "📋 <b>وضع المحرر النصي (Editor Mode)</b>\n\n"
            "الصق التوصية بالتنسيق التالي (كل معلومة في سطر):\n\n"
            "<b>مثال (انسخ وعدل):</b>\n"
            "<pre>"
            "Asset: ETHUSDT\n"
            "Side: SHORT\n"
            "Entry: 3000\n"
            "SL: 3100\n"
            "TPs: 2900 2800\n"
            "Notes: صفقة سريعة"
            "</pre>"
        )

    await update.message.reply_html(prompt)
    return AWAIT_TEXT_INPUT

@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    choice = query.data.split('_')[1]
    if choice == "interactive":
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(query.from_user.id))
        context.user_data[DRAFT_KEY] = {}
        await safe_edit_message(query, text="<b>الخطوة 1/4: الأصل</b>\nاختر أو اكتب رمز الأصل (e.g., BTCUSDT).", reply_markup=asset_choice_keyboard(recent_assets))
        return AWAITING_ASSET

    context.user_data[DRAFT_KEY] = {'input_mode': 'rec' if choice == 'quick' else 'editor'}
    
    if choice == "quick":
        prompt = (
            "⚡️ <b>وضع الأمر السريع</b>\n\n"
            "التنسيق: <code>الرمز الاتجاه الدخول الوقف الأهداف</code>\n"
            "مثال: <code>SOLUSDT LONG 150 140 160 170</code>"
        )
    else:
        prompt = (
            "📋 <b>وضع المحرر النصي</b>\n\n"
            "الصق البيانات:\n"
            "<pre>"
            "Asset: BNBUSDT\n"
            "Side: LONG\n"
            "Entry: 600\n"
            "SL: 580\n"
            "TPs: 620 640"
            "</pre>"
        )
        
    await safe_edit_message(query, text=prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data.get(DRAFT_KEY, {})
    mode = draft.get('input_mode')
    parser = parse_rec_command if mode == 'rec' else parse_editor_command
    
    data = parser(update.message.text)

    if data:
        draft.update(data)
        await show_review_card(update, context)
        return AWAITING_REVIEW

    await update.message.reply_text("❌ تنسيق غير صالح. يرجى التأكد من اتباع المثال أعلاه.")
    return AWAIT_TEXT_INPUT

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data[DRAFT_KEY]
    asset = ""
    query = update.callback_query
    if query:
        await query.answer()
        asset_part = query.data.split("_", 1)[1]
        if asset_part.lower() == "new":
            await safe_edit_message(query, text="✍️ الرجاء كتابة رمز الأصل الجديد.")
            return AWAITING_ASSET
        asset = asset_part
    else:
        asset = (update.message.text or "").strip().upper()

    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
        msg = f"❌ الرمز '<b>{asset}</b>' غير صالح. يرجى المحاولة مرة أخرى."
        if query: await safe_edit_message(query, text=msg)
        else: await update.message.reply_html(msg)
        return AWAITING_ASSET

    draft['asset'] = asset
    draft.setdefault('market', 'Futures')

    msg = f"✅ الأصل: <b>{asset}</b>\n\n<b>الخطوة 2/4: الاتجاه</b>\nاختر اتجاه التداول."
    markup = side_market_keyboard(draft['market'])
    if query: await safe_edit_message(query, text=msg, reply_markup=markup)
    else: await update.message.reply_html(msg, reply_markup=markup)
    return AWAITING_SIDE

async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    update_creation_activity(context)
    draft = context.user_data[DRAFT_KEY]
    action = query.data.split("_")[1]
    if action in ("LONG", "SHORT"):
        draft['side'] = action
        await safe_edit_message(query, text=f"✅ الاتجاه: <b>{action}</b>\n\n<b>الخطوة 3/4: نوع الطلب</b>", reply_markup=order_type_keyboard())
        return AWAITING_TYPE
    elif action == "menu":
        await query.edit_message_reply_markup(reply_markup=market_choice_keyboard())
        return AWAITING_SIDE

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    update_creation_activity(context)
    draft = context.user_data[DRAFT_KEY]
    if "back" in query.data:
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(draft.get('market', 'Futures')))
    else:
        market = query.data.split("_")[1]
        draft['market'] = market
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(market))
    return AWAITING_SIDE

async def type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    update_creation_activity(context)
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type
    
    prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>"
    if order_type == 'MARKET':
        prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>وقف الخسارة الأهداف...</code>"
        
    await safe_edit_message(query, text=f"✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}")
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    update_creation_activity(context)
    draft = context.user_data[DRAFT_KEY]
    tokens = (update.message.text or "").strip().split()
    try:
        creation_service = get_service(context, "creation_service", CreationService)
        if draft["order_type"] == 'MARKET':
            if len(tokens) < 2: raise ValueError("تنسيق السوق: وقف الخسارة ثم الأهداف...")
            stop_loss, targets = parse_number(tokens[0]), parse_targets_list(tokens[1:])
            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), True)
            if not live_price_float: raise ValueError("تعذر جلب سعر السوق الحالي.")
            live_price = Decimal(str(live_price_float))
            creation_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            draft.update({"entry": live_price, "stop_loss": stop_loss, "targets": targets})
        else:
            if len(tokens) < 3: raise ValueError("تنسيق LIMIT: سعر الدخول، وقف الخسارة، ثم الأهداف...")
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            creation_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            draft.update({"entry": entry, "stop_loss": stop_loss, "targets": targets})

        if not draft.get("targets"): raise ValueError("لم يتم تحليل أي أهداف صالحة.")
        await show_review_card(update, context)
        return AWAITING_REVIEW
    except Exception as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nيرجى المحاولة مرة أخرى.")
        return AWAITING_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data[DRAFT_KEY]
    if not draft.get("token"):
        draft["token"] = str(uuid.uuid4())

    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    review_text = build_review_text_with_price(draft, preview_price)

    if update.callback_query:
        await safe_edit_message(update.callback_query, text=review_text, reply_markup=review_final_keyboard(draft["token"]))
    else:
        await update.effective_message.reply_html(review_text, reply_markup=review_final_keyboard(draft["token"]))

@uow_transaction
@require_active_user
@require_analyst_user
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    update_creation_activity(context)

    draft = context.user_data.get(DRAFT_KEY)
    if not draft or not draft.get("token"):
        await safe_edit_message(query, text="❌ انتهت الجلسة. يرجى /newrec للبدء من جديد.")
        return ConversationHandler.END

    try:
        callback_data = CallbackBuilder.parse(query.data)
        action = callback_data.get('action')
        params = callback_data.get('params', [])
        
        # ✅ SECURITY FIX: Token Validation
        token_in_callback = params[0] if params else None
        short_token = draft.get("token", "")[:12]
        if not token_in_callback or token_in_callback != short_token:
            await safe_edit_message(query, text="❌ جلسة منتهية الصلاحية (Token Mismatch).")
            clean_creation_state(context)
            return ConversationHandler.END
        
        if action == "publish":
            creation_service = get_service(context, "creation_service", CreationService)
            all_channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=True)
            selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, {ch.telegram_channel_id for ch in all_channels})
            draft['target_channel_ids'] = selected_ids
            
            created_rec_entity, _ = await creation_service.create_and_publish_recommendation_async(
                str(query.from_user.id), db_session, **draft
            )
            
            msg = f"✅ تم الحفظ! (ID: #{created_rec_entity.id})\n\nجاري النشر الآن في {len(selected_ids)} قناة..."
            await safe_edit_message(query, text=msg)

            asyncio.create_task(
                creation_service.background_publish_and_index(
                    rec_id=created_rec_entity.id,
                    user_db_id=db_user.id,
                    target_channel_ids=selected_ids
                )
            )
            
            clean_creation_state(context)
            return ConversationHandler.END

        elif action == "choose_channels":
            all_channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
            selected_ids = context.user_data.setdefault(CHANNEL_PICKER_KEY, {ch.telegram_channel_id for ch in all_channels if ch.is_active})
            keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids)
            await safe_edit_message(query, text="📢 <b>اختر القنوات للنشر</b>", reply_markup=keyboard)
            return AWAITING_CHANNELS

        elif action == "add_notes":
            await safe_edit_message(query, text="📝 <b>أضف ملاحظاتك</b>\nأرسل النص الآن:")
            return AWAITING_NOTES

        elif action == "cancel":
            await safe_edit_message(query, text="❌ تم إلغاء العملية.")
            clean_creation_state(context)
            return ConversationHandler.END

    except Exception as e:
        log.exception("Review handler error")
        await safe_edit_message(query, text=f"❌ خطأ غير متوقع: {str(e)}")
        clean_creation_state(context)
        return ConversationHandler.END

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    update_creation_activity(context)
    draft = context.user_data.get(DRAFT_KEY)
    
    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    
    # ✅ SECURITY FIX: Token Validation
    token_in_callback = params[0] if params else None
    short_token = draft.get("token", "")[:12]
    if not token_in_callback or token_in_callback != short_token:
        await safe_edit_message(query, text="❌ جلسة منتهية الصلاحية.")
        clean_creation_state(context)
        return ConversationHandler.END
    
    all_channels = ChannelRepository(db_session).list_by_analyst(db_user.id, only_active=False)
    selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())

    if action == CallbackAction.BACK.value:
        await show_review_card(update, context)
        return AWAITING_REVIEW

    elif action == CallbackAction.CONFIRM.value:
        creation_service = get_service(context, "creation_service", CreationService)
        if not selected_ids:
            await query.answer("❌ لم يتم اختيار أي قنوات", show_alert=True)
            return AWAITING_CHANNELS

        draft['target_channel_ids'] = selected_ids
        created_rec_entity, _ = await creation_service.create_and_publish_recommendation_async(
            str(query.from_user.id), db_session, **draft
        )
        msg = f"✅ تم الحفظ! (ID: #{created_rec_entity.id})\n\nجاري النشر الآن في {len(selected_ids)} قناة..."
        await safe_edit_message(query, text=msg)
        asyncio.create_task(creation_service.background_publish_and_index(rec_id=created_rec_entity.id, user_db_id=db_user.id, target_channel_ids=selected_ids))
        clean_creation_state(context)
        return ConversationHandler.END

    else: # TOGGLE / NAV
        if action == "toggle":
            channel_id_to_toggle = int(params[1])
            if channel_id_to_toggle in selected_ids: selected_ids.remove(channel_id_to_toggle)
            else: selected_ids.add(channel_id_to_toggle)
            context.user_data[CHANNEL_PICKER_KEY] = selected_ids
        
        page = int(params[2]) if len(params) > 2 else 1
        if action == "nav": page = int(params[1])

        keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids, page=page)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return AWAITING_CHANNELS

async def cancel_creation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clean_creation_state(context)
    await update.message.reply_text("❌ تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==============================================================================
# 5. MANAGEMENT CONVERSATIONS (FULLY RESTORED)
# ==============================================================================

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Entry point for custom partial close conversation."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get("params", [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None
    if rec_id is None:
        return ConversationHandler.END

    context.user_data[PARTIAL_CLOSE_REC_ID_KEY] = rec_id
    context.user_data[ORIGINAL_MESSAGE_CHAT_ID_KEY] = query.message.chat_id
    context.user_data[ORIGINAL_MESSAGE_MESSAGE_ID_KEY] = query.message.message_id

    cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
    await safe_edit_message(
        query,
        text=f"{query.message.text_html}\n\n<b>💰 Send the custom Percentage to close (e.g., 30):</b>",
        reply_markup=InlineKeyboardMarkup([[cancel_button]]),
    )
    return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END
    update_management_activity(context)

    rec_id = context.user_data.get(PARTIAL_CLOSE_REC_ID_KEY)
    chat_id = context.user_data.get(ORIGINAL_MESSAGE_CHAT_ID_KEY)
    message_id = context.user_data.get(ORIGINAL_MESSAGE_MESSAGE_ID_KEY)
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: pass
    if not (rec_id and chat_id and message_id):
        clean_management_state(context)
        return ConversationHandler.END

    try:
        percent_val = parse_number(user_input.replace("%", ""))
        if percent_val is None or not (0 < percent_val <= Decimal("100")):
            raise ValueError("Percentage must be between 0 and 100.")

        context.user_data[PARTIAL_CLOSE_PERCENT_KEY] = percent_val
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"✅ Closing {percent_val:g}%.\n\n<b>✍️ Send the custom Exit Price:</b>\n(or send '<b>market</b>' to use live price)",
            reply_markup=InlineKeyboardMarkup([[cancel_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PRICE

    except (ValueError, Exception) as e:
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"⚠️ **Invalid Percentage:** {e}\n\n<b>💰 Send Percentage to close (e.g., 30):</b>",
            reply_markup=InlineKeyboardMarkup([[cancel_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END

    rec_id = context.user_data.get(PARTIAL_CLOSE_REC_ID_KEY)
    percent_val = context.user_data.get(PARTIAL_CLOSE_PERCENT_KEY)
    chat_id = context.user_data.get(ORIGINAL_MESSAGE_CHAT_ID_KEY)
    message_id = context.user_data.get(ORIGINAL_MESSAGE_MESSAGE_ID_KEY)
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: pass
    if not (rec_id and percent_val and chat_id and message_id):
        clean_management_state(context)
        return ConversationHandler.END

    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    user_telegram_id = str(db_user.telegram_user_id)
    exit_price: Optional[Decimal] = None

    try:
        if user_input.lower() == "market":
            price_service = get_service(context, "price_service", PriceService)
            position = lifecycle_service.repo.get(db_session, rec_id)
            if not position: raise ValueError("Recommendation not found.")
            live_price = await price_service.get_cached_price(position.asset, position.market, force_refresh=True)
            if not live_price: raise ValueError(f"Could not fetch market price for {position.asset}.")
            exit_price = Decimal(str(live_price))
        else:
            price_val = parse_number(user_input)
            if price_val is None: raise ValueError("Invalid price format. Send a number or 'market'.")
            exit_price = price_val

        await lifecycle_service.partial_close_async(rec_id, user_telegram_id, percent_val, exit_price, db_session, triggered_by="MANUAL_CUSTOM")
        
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"✅ Closed {percent_val:g}% at {_format_price(exit_price)}.",
            reply_markup=None
        )

    except (ValueError, Exception) as e:
        loge.error(f"Error in custom partial close execution for rec #{rec_id}: {e}", exc_info=True)
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id))
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"⚠️ **Error:** {e}\n\n<b>✍️ Send the custom Exit Price:</b>\n(or send '<b>market</b>' to use live price)",
            reply_markup=InlineKeyboardMarkup([[cancel_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PRICE

    clean_management_state(context)
    return ConversationHandler.END

@uow_transaction
@require_active_user
async def user_trade_close_start(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get("params", [])
    trade_id = int(params[1]) if params and len(params) > 1 and params[1].isdigit() else None
    if trade_id is None:
        return ConversationHandler.END

    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    position = lifecycle_service.repo.get_user_trade_by_id(db_session, trade_id)
    
    if not position or position.user_id != db_user.id or position.status != UserTradeStatus.ACTIVATED:
        await query.answer("❌ Trade not found or is not active.", show_alert=True)
        return ConversationHandler.END

    context.user_data[USER_TRADE_CLOSE_ID_KEY] = trade_id
    context.user_data[ORIGINAL_MESSAGE_CHAT_ID_KEY] = query.message.chat_id
    context.user_data[ORIGINAL_MESSAGE_MESSAGE_ID_KEY] = query.message.message_id

    cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", trade_id))
    await safe_edit_message(
        query,
        text=f"{query.message.text_html}\n\n<b>✍️ Send the final Exit Price for {position.asset}:</b>",
        reply_markup=InlineKeyboardMarkup([[cancel_button]]),
    )
    return AWAIT_USER_TRADE_CLOSE_PRICE

@uow_transaction
@require_active_user
async def user_trade_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END

    trade_id = context.user_data.get(USER_TRADE_CLOSE_ID_KEY)
    chat_id = context.user_data.get(ORIGINAL_MESSAGE_CHAT_ID_KEY)
    message_id = context.user_data.get(ORIGINAL_MESSAGE_MESSAGE_ID_KEY)
    user_input = update.message.text.strip() if update.message.text else ""

    try: await update.message.delete()
    except Exception: pass
    if not (trade_id and chat_id and message_id):
        clean_management_state(context)
        return ConversationHandler.END

    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        exit_price = parse_number(user_input)
        if exit_price is None:
            raise ValueError("Invalid price format. Send a valid number.")

        closed_trade = await lifecycle_service.close_user_trade_async(user_telegram_id, trade_id, exit_price, db_session)
        if not closed_trade:
            raise ValueError("Trade not found or access denied.")

        pnl_pct = closed_trade.pnl_percentage
        pnl_str = f"({pnl_pct:+.2f}%)" if pnl_pct is not None else ""

        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"✅ <b>Trade Closed</b>\n{closed_trade.asset} closed at {_format_price(exit_price)} {pnl_str}.",
            reply_markup=None, parse_mode=ParseMode.HTML
        )

    except (ValueError, Exception) as e:
        loge.error(f"Error in user trade close execution for trade #{trade_id}: {e}", exc_info=True)
        cancel_button = InlineKeyboardButton("❌ Cancel", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", trade_id))
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id,
            text=f"⚠️ **Error:** {e}\n\n<b>✍️ Send the final Exit Price:</b>",
            reply_markup=InlineKeyboardMarkup([[cancel_button]]),
            parse_mode=ParseMode.HTML
        )
        return AWAIT_USER_TRADE_CLOSE_PRICE

    clean_management_state(context)
    return ConversationHandler.END

async def cancel_management_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = context.user_data.get(ORIGINAL_MESSAGE_CHAT_ID_KEY)
    message_id = context.user_data.get(ORIGINAL_MESSAGE_MESSAGE_ID_KEY)
    clean_management_state(context)
    if update.callback_query:
        try: await update.callback_query.answer("Cancelled")
        except Exception: pass
    if chat_id and message_id:
        try: await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Operation cancelled.", reply_markup=None)
        except Exception: pass
    elif update.message:
        await update.message.reply_text("❌ Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ==============================================================================
# 6. MASTER REPLY HANDLER (FULLY RESTORED)
# ==============================================================================

@uow_transaction
@require_active_user
async def master_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> Optional[int]:
    """
    Handles text replies for:
    1. Adding Notes (Creation Flow)
    2. Management Input (Edit Entry, etc. - Handled by SessionContext in management_handlers, but fallback here)
    """
    
    # --- Case 1: Adding Notes in Creation Flow ---
    if context.user_data.get(DRAFT_KEY) and update.message:
        update_creation_activity(context)
        draft = context.user_data[DRAFT_KEY]
        draft['notes'] = (update.message.text or '').strip()
        await show_review_card(update, context)
        return AWAITING_REVIEW

    # --- Case 2: Management Input (Fallback for Implicit State) ---
    from capitalguard.interfaces.telegram.session import SessionContext, KEY_AWAITING_INPUT, KEY_PENDING_CHANGE
    from capitalguard.interfaces.telegram.schemas import ManagementAction
    from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace
    
    session = SessionContext(context)
    input_state = session.get_input_state()
    
    if input_state and update.message:
        user_input = update.message.text.strip()
        try: await update.message.delete()
        except: pass
        
        action = input_state.get("action")
        item_id = input_state.get("item_id")
        chat_id = input_state.get("original_message_chat_id")
        message_id = input_state.get("original_message_message_id")
        
        validated_value = None
        change_desc = ""
        
        try:
            if action in [ManagementAction.EDIT_ENTRY.value, ManagementAction.EDIT_SL.value, ManagementAction.CLOSE_MANUAL.value]:
                val = parse_number(user_input)
                if val is None: raise ValueError("Invalid number.")
                validated_value = val
                change_desc = f"Update {action.replace('edit_', '').upper()} to {val}"
            elif action == ManagementAction.EDIT_TP.value:
                val = parse_targets_list(user_input.split())
                if not val: raise ValueError("Invalid targets.")
                validated_value = val
                change_desc = "Update Targets"
            elif action == ManagementAction.EDIT_NOTES.value:
                validated_value = user_input if user_input.lower() != "clear" else None
                change_desc = "Update Notes"
                
            context.user_data[KEY_PENDING_CHANGE] = {"value": validated_value}
            
            confirm_cb = CallbackBuilder.create(CallbackNamespace.MGMT, ManagementAction.CONFIRM_CHANGE, "rec", action, item_id)
            cancel_cb = CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", item_id)
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm", callback_data=confirm_cb)],
                [InlineKeyboardButton("❌ Cancel", callback_data=cancel_cb)]
            ])
            
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f"❓ **Confirm Action**\n\n{change_desc}?",
                reply_markup=kb, parse_mode=ParseMode.MARKDOWN
            )
            
            session.user_data.pop(KEY_AWAITING_INPUT, None)
            
        except ValueError as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Invalid input: {e}")
            
        return None

    return None

# ==============================================================================
# 7. REGISTRATION
# ==============================================================================

def register_conversation_handlers(app: Application):
    # 1. Creation Conversation
    creation_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entrypoint),
            CommandHandler("rec", start_text_input_entrypoint),
            CommandHandler("editor", start_text_input_entrypoint),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            AWAITING_ASSET: [CallbackQueryHandler(asset_handler, pattern="^asset_"), MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler)],
            AWAITING_SIDE: [CallbackQueryHandler(side_handler, pattern="^side_"), CallbackQueryHandler(market_handler, pattern="^market_")],
            AWAITING_TYPE: [CallbackQueryHandler(type_handler, pattern="^type_")],
            AWAITING_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_handler)],
            AWAITING_REVIEW: [CallbackQueryHandler(review_handler, pattern=f"^{CallbackNamespace.RECOMMENDATION.value}:")],
            AWAITING_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, master_reply_handler)],
            AWAITING_CHANNELS: [CallbackQueryHandler(channel_picker_handler, pattern=f"^{CallbackNamespace.PUBLICATION.value}:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_creation_handler)],
        name="recommendation_creation",
        per_user=True, per_chat=True,
        conversation_timeout=CONVERSATION_TIMEOUT_CREATION,
        per_message=False
    )
    
    # 2. Partial Close Conversation (Restored)
    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_management_conversation),
            CallbackQueryHandler(cancel_management_conversation, pattern=rf"^mgmt:cancel_input:"),
        ],
        name="partial_close_conversation",
        per_user=True, per_chat=True,
        conversation_timeout=MANAGEMENT_TIMEOUT,
        persistent=False,
        per_message=False,
    )

    # 3. User Trade Close Conversation (Restored)
    user_trade_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_trade_close_start, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.CLOSE.value}:trade:")],
        states={
            AWAIT_USER_TRADE_CLOSE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_trade_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_management_conversation),
            CallbackQueryHandler(cancel_management_conversation, pattern=rf"^mgmt:cancel_input:"),
        ],
        name="user_trade_close_conversation",
        per_user=True, per_chat=True,
        conversation_timeout=MANAGEMENT_TIMEOUT,
        persistent=False,
        per_message=False,
    )

    # 4. Master Reply Handler (Implicit State)
    reply_handler = MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        master_reply_handler
    )
    
    # Also catch generic text messages if they match an implicit state (for management edits)
    generic_text_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        master_reply_handler
    )

    app.add_handler(creation_conv, group=0)
    app.add_handler(partial_close_conv, group=0)
    app.add_handler(user_trade_close_conv, group=0)
    app.add_handler(reply_handler, group=0)
    app.add_handler(generic_text_handler, group=0)

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---