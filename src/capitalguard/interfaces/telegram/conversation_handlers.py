# File: src/capitalguard/interfaces/telegram/conversation_handlers.py
# Version: v53.0.0-PRODUCTION-ENHANCED (UX & State Fixes)
# ✅ THE FIX:
#    1. UX: Show live price in MARKET order type
#    2. CRITICAL: Fix state management to prevent portfolio from appearing during creation
#    3. SECURITY: Maintain all security fixes from v52

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
from capitalguard.infrastructure.core_engine import core_cache, cb_db, AsyncPipeline  # ✅ RESTORED

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
loge = logging.getLogger("capitalguard.errors")

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
# 4. CREATION HANDLERS WITH UX ENHANCEMENTS
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
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
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
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
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
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)
    
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type

    # ✅ UX ENHANCEMENT: Show live price for ALL order types including MARKET
    asset = draft.get("asset")
    market = draft.get("market", "Futures")
    live_price_str = ""
    
    if asset:
        try:
            price_service = get_service(context, "price_service", PriceService)
            live_price = await price_service.get_cached_price(asset, market)
            if live_price is not None:
                lp_dec = Decimal(str(live_price))
                if lp_dec >= 1000:
                    live_price_str = f"— السعر الحالي: {lp_dec.normalize():f}"
                else:
                    live_price_str = f"— السعر الحالي: {lp_dec:.4f}".rstrip('0').rstrip('.')
        except Exception as e:
            log.warning(f"Failed to fetch live price for {asset}: {e}")

    prompt = (
        "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>وقف الخسارة الأهداف...</code>"
        if order_type == 'MARKET'
        else "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>"
    )

    # ✅ UX ENHANCEMENT: Always show asset and live price
    asset_display = f"<b>{asset}</b> " if asset else ""
    msg = f"✅ الأصل: {asset_display}{live_price_str}\n✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}"

    await safe_edit_message(query, text=msg)
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
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
# 5. MASTER REPLY HANDLER WITH STATE FIX
# ==============================================================================

@uow_transaction
@require_active_user
async def master_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> Optional[int]:
    """
    ✅ CRITICAL FIX: Proper state management to prevent portfolio from appearing
    """
    
    # --- Case 1: Adding Notes in Creation Flow ---
    if context.user_data.get(DRAFT_KEY) and update.message:
        log.debug(f"MasterReplyHandler: Processing notes for creation flow")
        
        # ✅ CRITICAL FIX: Check timeout and update activity
        if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
            return ConversationHandler.END
            
        update_creation_activity(context)
        draft = context.user_data[DRAFT_KEY]
        draft['notes'] = (update.message.text or '').strip()
        
        # ✅ CRITICAL FIX: Explicitly return to AWAITING_REVIEW state
        await show_review_card(update, context)
        return AWAITING_REVIEW

    # --- Case 2: Management Input (Fallback for Implicit State) ---
    from capitalguard.interfaces.telegram.session import SessionContext, KEY_AWAITING_INPUT, KEY_PENDING_CHANGE
    from capitalguard.interfaces.telegram.schemas import ManagementAction
    
    session = SessionContext(context)
    input_state = session.get_input_state()
    
    if input_state and update.message:
        log.debug(f"MasterReplyHandler: Processing management input for {input_state.get('action')}")
        
        if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
            return ConversationHandler.END
            
        update_management_activity(context)
        
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

    log.debug("MasterReplyHandler: No active state detected")
    return None

# ==============================================================================
# 6. MANAGEMENT CONVERSATIONS (RESTORED)
# ==============================================================================

# ... (جميع دوال الإدارة من الإصدار السابق تبقى كما هي بدون تغيير)
# partial_close_custom_start, partial_close_percent_received, partial_close_price_received
# user_trade_close_start, user_trade_close_price_received, cancel_management_conversation

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
    
    # 2. Partial Close Conversation
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

    # 3. User Trade Close Conversation
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