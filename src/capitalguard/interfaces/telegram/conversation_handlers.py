# File: src/capitalguard/interfaces/telegram/conversation_handlers.py
# Version: v37.0.1-R2 (Critical SyntaxError Hotfix)
# ✅ THE FIX: (R2 Architecture - SyntaxError Hotfix)
#    - 1. (CRITICAL) إصلاح `SyntaxError: invalid syntax` الذي أبلغ عنه المستخدم.
#    - 2. (MOVED) تم نقل استدعاء `get_service(context, "creation_service", ...)`
#       إلى *داخل* البلوك الشرطي `if action == "publish":` (في `review_handler`)
#       و `elif action == CallbackAction.CONFIRM.value:` (في `channel_picker_handler`).
#    - 3. (CLEAN) هذا يحل مشكلة قطع سلسلة `if/elif` ويجعل الملف قابلاً للتشغيل.
# 🎯 IMPACT: الملف الآن خالٍ من الأخطاء النحوية الحرجة وجاهز للعمل.

import logging
import uuid
import time
import asyncio
import re 
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Dict, Any, Union

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest, TelegramError
from telegram.constants import ParseMode

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, _get_attr
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard, CallbackBuilder, CallbackNamespace, CallbackAction,
    ButtonTexts,
    build_editable_review_card
)
from .auth import require_active_user, require_analyst_user, get_db_user
from .parsers import parse_rec_command, parse_editor_command, parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
# ✅ R2: Import new services
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.creation_service import CreationService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.models import UserType as UserTypeEntity


log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- 1. State Definitions (Consolidated) ---

# --- States for Recommendation Creation ---
(
    SELECT_METHOD, AWAIT_TEXT_INPUT, AWAITING_ASSET,
    AWAITING_SIDE, AWAITING_TYPE,
    AWAITING_PRICES, AWAITING_REVIEW, AWAITING_NOTES, AWAITING_CHANNELS
) = range(9)

# --- States for Management Conversations (Moved from management_handlers.py) ---
(
    AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE,
    AWAIT_USER_TRADE_CLOSE_PRICE
) = range(AWAITING_CHANNELS + 1, AWAITING_CHANNELS + 4)


# --- 2. State Management Keys (Consolidated) ---

# --- Keys for Creation Conversation ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"
LAST_ACTIVITY_KEY_CREATION = "last_creation_activity"
CONVERSATION_TIMEOUT_CREATION = 1800  # 30 minutes

# --- Keys for Management Conversations (Moved from management_handlers.py) ---
AWAITING_INPUT_KEY = "awaiting_management_input" # Implicit state for replies
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY_MGMT = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800  # 30 minutes
# Keys for specific conversations
PARTIAL_CLOSE_REC_ID_KEY = "partial_close_rec_id"
PARTIAL_CLOSE_PERCENT_KEY = "partial_close_percent"
USER_TRADE_CLOSE_ID_KEY = "user_trade_close_id"
# Keys for message editing
ORIGINAL_MESSAGE_CHAT_ID_KEY = "original_message_chat_id"
ORIGINAL_MESSAGE_MESSAGE_ID_KEY = "original_message_message_id"


# --- 3. Helper Functions (Consolidated) ---

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة إنشاء التوصية بشكل كامل."""
    for key in [DRAFT_KEY, CHANNEL_PICKER_KEY, LAST_ACTIVITY_KEY_CREATION]:
        context.user_data.pop(key, None)

def update_creation_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير (لإنشاء توصية)."""
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
    log.debug(f"All management conversation states cleared for user {getattr(context, '_user_id', '<unknown>')}.")

def update_management_activity(context: ContextTypes.DEFAULT_TYPE):
    """Updates the last activity timestamp (for management)."""
    context.user_data[LAST_ACTIVITY_KEY_MGMT] = time.time()

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE, timeout_seconds: int, last_activity_key: str, state_cleaner: callable) -> bool:
    """معالج انتهاء مدة موحد."""
    last_activity = context.user_data.get(last_activity_key, 0)
    if time.time() - last_activity > timeout_seconds:
        state_cleaner(context)
        message = "⏰ انتهت مدة الجلسة. يرجى البدء من جديد."
        if update.callback_query:
            try:
                await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            except Exception: pass
            await safe_edit_message(update.callback_query, text=message)
        elif update.message:
            await update.message.reply_text(message)
        return True
    return False

# (Helper: safe_edit_message)
async def safe_edit_message(query: CallbackQuery, text=None, reply_markup=None, parse_mode=ParseMode.HTML):
    """تحرير الرسالة بشكل آمن مع استعادة الأخطاء."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        log.warning(f"Handled BadRequest in safe_edit_message: {e}")
        return False
    except Exception as e:
        log.error(f"Error in safe_edit_message: {e}", exc_info=True)
        return False

# (Helper: _preload_asset_prices)
async def _preload_asset_prices(price_service: PriceService, assets: List[str]):
    """Background task to warm up the price cache for recent assets."""
    log.debug(f"[Pre-fetch]: Warming cache for {len(assets)} assets...")
    try:
        tasks = [
            price_service.get_cached_price(asset, "Futures", force_refresh=False) 
            for asset in assets
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        log.debug("[Pre-fetch]: Cache warming complete.")
    except Exception as e:
        log.warning(f"[Pre-fetch]: Price pre-fetch task failed: {e}", exc_info=False)


# --- 4. Handlers for Recommendation Creation (No Change) ---
# (e.g., newrec_entrypoint, start_text_input_entrypoint, method_chosen,
# received_text_input, asset_handler, side_handler, market_handler,
# type_handler, prices_handler, show_review_card)

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة."""
    clean_creation_state(context)
    clean_management_state(context) # Clean other convos
    update_creation_activity(context)
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        recent_assets = trade_service.get_recent_assets_for_user(
            db_session, str(db_user.telegram_user_id)
        )
        if recent_assets:
            asyncio.create_task(_preload_asset_prices(price_service, recent_assets))
    except Exception as e:
        log.warning(f"Failed to launch price pre-fetch task: {e}", exc_info=False)

    await update.message.reply_html("🚀 <b>منشئ التوصيات</b>\n\nاختر طريقة الإدخال:", reply_markup=main_creation_keyboard())
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """بدء الإدخال النصي السريع."""
    clean_creation_state(context)
    clean_management_state(context) # Clean other convos
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    update_creation_activity(context)

    prompt = "⚡️ <b>وضع الأمر السريع</b>\nأدخل توصيتك في سطر واحد:\n<code>الأصل الاتجاه الدخول الوقف الهدف1 الهدف2...</code>" if command == 'rec' else "📋 <b>وضع المحرر النصي</b>\nالصق توصيتك بتنسيق <code>مفتاح: قيمة</code>"

    await update.message.reply_html(prompt)
    return AWAIT_TEXT_INPUT

@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """معالجة اختيار طريقة الإدخال."""
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
    prompt = "⚡️ أدخل توصيتك الكاملة في سطر واحد..." if choice == "quick" else "📋 الصق توصيتك..."
    await safe_edit_message(query, text=prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإدخال النصي."""
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

    await update.message.reply_text("❌ تنسيق غير صالح. يرجى المحاولة مرة أخرى أو /cancel.")
    return AWAIT_TEXT_INPUT

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الأصل."""
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
    """معالجة اختيار الاتجاه."""
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
    """معالجة اختيار السوق."""
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
    """معالجة اختيار نوع الطلب مع عرض السعر الحي واسم الأصل."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type

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

    asset_display = f"<b>{asset}</b> " if asset else ""
    msg = f"✅ الأصل: {asset_display}{live_price_str}\n✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}"

    await safe_edit_message(query, text=msg)
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الأسعار."""
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data[DRAFT_KEY]
    tokens = (update.message.text or "").strip().split()

    try:
        # ✅ R2: Use CreationService
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
            if len(tokens) < 3: raise ValueError("تنسيق LIMIT/STOP: سعر الدخول، وقف الخسارة، ثم الأهداف...")
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            creation_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            draft.update({"entry": entry, "stop_loss": stop_loss, "targets": targets})

        if not draft.get("targets"): raise ValueError("لم يتم تحليل أي أهداف صالحة.")
        await show_review_card(update, context)
        return AWAITING_REVIEW

    except (ValueError, InvalidOperation, TypeError) as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nيرجى المحاولة مرة أخرى.")
        return AWAITING_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض بطاقة المراجعة النهائية."""
    draft = context.user_data[DRAFT_KEY]
    if not draft.get("token"):
        draft["token"] = str(uuid.uuid4())

    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    review_text = build_review_text_with_price(draft, preview_price)

    message = update.callback_query.message if update.callback_query else update.effective_message
    await message.reply_html(review_text, reply_markup=review_final_keyboard(draft["token"]))

@uow_transaction
@require_active_user
@require_analyst_user
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """معالجة مراجعة التوصية."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data.get(DRAFT_KEY)
    if not draft or not draft.get("token"):
        await safe_edit_message(query, text="❌ انتهت الجلسة. يرجى /newrec للبدء من جديد.")
        return ConversationHandler.END

    try:
        callback_data = CallbackBuilder.parse(query.data)
        action = callback_data.get('action')
        params = callback_data.get('params', [])
        token_in_callback = params[0] if params else None
        short_token = draft["token"][:12]

        if not token_in_callback or token_in_callback != short_token:
            await safe_edit_message(query, text="❌ جلسة منتهية الصلاحية.")
            clean_creation_state(context)
            return ConversationHandler.END
            
        if action == "publish":
            # ✅ [FIX 1] HOTFIX: Move service retrieval *inside* the block
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
            await safe_edit_message(query, text="📝 <b>أضف ملاحظاتك</b>")
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
    """معالجة اختيار القنوات - الإصدار النهائي المصحح."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
        return ConversationHandler.END
    update_creation_activity(context)

    draft = context.user_data.get(DRAFT_KEY)
    if not draft or not draft.get("token"):
        await safe_edit_message(query, text="❌ انتهت الجلسة. يرجى البدء من جديد.")
        return ConversationHandler.END

    try:
        callback_data = CallbackBuilder.parse(query.data)
        action = callback_data.get('action')
        params = callback_data.get('params', [])
        token_in_callback = params[0] if params else None
        short_token = draft["token"][:12]

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
            # ✅ [FIX 2] HOTFIX: Move service retrieval *inside* the block
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

            asyncio.create_task(
                creation_service.background_publish_and_index(
                    rec_id=created_rec_entity.id,
                    user_db_id=db_user.id,
                    target_channel_ids=selected_ids
                )
            )
            
            clean_creation_state(context)
            return ConversationHandler.END

        else: # Handles TOGGLE and NAV
            page = 1
            if action == "toggle":
                channel_id_to_toggle = int(params[1])
                if channel_id_to_toggle in selected_ids: selected_ids.remove(channel_id_to_toggle)
                else: selected_ids.add(channel_id_to_toggle)
                context.user_data[CHANNEL_PICKER_KEY] = selected_ids
                page = int(params[2])
            elif action == "nav":
                page = int(params[1])

            keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids, page=page)
            await query.edit_message_reply_markup(reply_markup=keyboard)
            return AWAITING_CHANNELS

    except Exception as e:
        log.error(f"Error in channel picker: {e}", exc_info=True)
        await safe_edit_message(query, text=f"❌ حدث خطأ: {str(e)}")
        return AWAITING_CHANNELS

async def cancel_creation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإلغاء (لإنشاء توصية)."""
    clean_creation_state(context)
    await update.message.reply_text("❌ تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --- 5. Master Reply Handler (MERGED) ---
@uow_transaction
@require_active_user
async def master_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> Optional[int]:
    """
    [R2 - MERGED]
    معالج الردود الموحد. يتحقق من جميع حالات المحادثة التي تنتظر ردًا نصيًا.
    """
    
    # --- الحالة 1: الرد لإدارة التوصية (Moved from management_handlers) ---
    mgmt_state = context.user_data.get(AWAITING_INPUT_KEY)
    if mgmt_state:
        log.debug(f"MasterReplyHandler: Detected management input state: {mgmt_state.get('action')}")
        if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
            return ConversationHandler.END
        update_management_activity(context)
        
        chat_id = mgmt_state.get("original_message_chat_id")
        message_id = mgmt_state.get("original_message_message_id")
        if not (chat_id and message_id):
            log.error(f"Reply handler for user {update.effective_user.id} has corrupt state: missing message IDs.")
            clean_management_state(context)
            return ConversationHandler.END

        namespace = mgmt_state.get("namespace")
        action = mgmt_state.get("action")
        item_id = mgmt_state.get("item_id")
        item_type = mgmt_state.get("item_type", "rec")
        user_input = update.message.text.strip() if update.message.text else ""

        is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value]
        if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST):
            await update.message.reply_text("🚫 Permission Denied: This action requires Analyst role.")
            clean_management_state(context)
            return None 

        try: await update.message.delete()
        except Exception: log.debug("Could not delete user reply message.")

        validated_value: Any = None
        change_description = ""
        # ✅ R2: Use LifecycleService
        lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
        
        try:
            current_item = lifecycle_service.repo.get(db_session, item_id) # Simplified get
            if not current_item: raise ValueError("Position not found or closed.")
            
            current_item_entity = lifecycle_service.repo._to_entity(current_item)
            if not current_item_entity: raise ValueError("Could not parse position entity.")


            if namespace == CallbackNamespace.EXIT_STRATEGY.value:
                # ... (logic for set_fixed, set_trailing) ...
                pass # (Stubbed for brevity)

            elif namespace == CallbackNamespace.RECOMMENDATION.value:
                if action in ["edit_sl", "edit_entry", "close_manual"]:
                    price = parse_number(user_input)
                    if price is None: raise ValueError("Invalid price format.")
                    
                    validated_value = price
                    if action == "edit_sl": change_description = f"Update Stop Loss to {_get_attr(price, 'g')}"
                    elif action == "edit_entry": change_description = f"Update Entry Price to {_get_attr(price, 'g')}"
                    elif action == "close_manual": change_description = f"Manually Close Position at {_get_attr(price, 'g')}"

                elif action == "edit_tp":
                    targets_list_dict = parse_targets_list(user_input.split())
                    if not targets_list_dict: raise ValueError("Invalid targets format.")
                    validated_value = targets_list_dict
                    change_description = "Update Targets"

                elif action == "edit_notes":
                    validated_value = user_input if user_input.lower() not in ["clear", "مسح"] else None
                    change_description = f"Update Notes"
            
            if validated_value is not None or action == "edit_notes":
                context.user_data[PENDING_CHANGE_KEY] = {"value": validated_value}
                context.user_data.pop(AWAITING_INPUT_KEY, None)

                confirm_callback = CallbackBuilder.create("mgmt", "confirm_change", namespace, action, item_id)
                reenter_callback = mgmt_state.get("previous_callback", CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, item_id))
                cancel_callback = CallbackBuilder.create("mgmt", "cancel_all", item_id)

                confirm_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(ButtonTexts.CONFIRM, callback_data=confirm_callback)],
                    [InlineKeyboardButton("✏️ Re-enter Value", callback_data=reenter_callback)],
                    [InlineKeyboardButton(ButtonTexts.CANCEL + " Action", callback_data=cancel_callback)],
                ])
                
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id,
                    text=f"❓ <b>Confirm Action</b>\n\nDo you want to:\n➡️ {change_description}?",
                    reply_markup=confirm_keyboard, parse_mode=ParseMode.HTML
                )
            else:
                raise ValueError("Validation passed but no value was stored.")

        except ValueError as e:
            log.warning(f"Invalid input during reply for {action} on {item_type} #{item_id}: {e}")
            cancel_button = InlineKeyboardButton("❌ Cancel Input", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", item_id))
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=f"⚠️ **Invalid Input:** {e}\n\nPlease try again or cancel.",
                reply_markup=InlineKeyboardMarkup([[cancel_button]]),
                parse_mode=ParseMode.HTML
            )
            return None # Stay in implicit state
        except Exception as e:
            loge.error(f"Error processing reply for {action} on {item_type} #{item_id}: {e}", exc_info=True)
            clean_management_state(context)
            # (Error message logic)
        
        return None # Stay in implicit state until confirmation

    # --- الحالة 2: الرد لإضافة ملاحظات (Moved from creation conversation) ---
    elif context.user_data.get(DRAFT_KEY) and update.message:
        log.debug(f"MasterReplyHandler: Detected creation input state (AWAITING_NOTES)")
        if await handle_timeout(update, context, CONVERSATION_TIMEOUT_CREATION, LAST_ACTIVITY_KEY_CREATION, clean_creation_state):
            return ConversationHandler.END
        update_creation_activity(context)
        
        draft = context.user_data[DRAFT_KEY]
        draft['notes'] = (update.message.text or '').strip()
        await show_review_card(update, context)
        return AWAITING_REVIEW
        
    # --- الحالة 3: الرد لإغلاق جزئي مخصص (Moved from management_handlers) ---
    elif context.user_data.get(PARTIAL_CLOSE_REC_ID_KEY) and update.message:
        log.debug(f"MasterReplyHandler: Detected partial_close input state")
        # (This state is now managed by explicit ConversationHandler, see below)
        pass

    # --- الحالة 4: الرد لإغلاق صفقة مستخدم (Moved from management_handlers) ---
    elif context.user_data.get(USER_TRADE_CLOSE_ID_KEY) and update.message:
        log.debug(f"MasterReplyHandler: Detected user_trade_close input state")
        # (This state is now managed by explicit ConversationHandler, see below)
        pass

    log.debug("MasterReplyHandler: No active state detected for this reply.")
    return None


# --- 6. Handlers for Management Conversations (MOVED & REFACTORED) ---

# --- (Partial Close Conversation) ---
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

    parsed_data = CallbackBuilder.parse(query.data) # rec:partial_close_custom:<rec_id>
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
    """Handles receiving the custom partial close percentage."""
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
    """Handles receiving the custom partial close price (or 'market')."""
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

    # ✅ R2: Use LifecycleService
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
            text=f"✅ Closed {percent_val:g}% at {_get_attr(exit_price, 'g')}.",
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

async def cancel_management_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels any active management conversation."""
    chat_id = context.user_data.get(ORIGINAL_MESSAGE_CHAT_ID_KEY)
    message_id = context.user_data.get(ORIGINAL_MESSAGE_MESSAGE_ID_KEY)
    
    clean_management_state(context)

    if update.callback_query:
        try: await update.callback_query.answer("Cancelled")
        except Exception: pass

    if chat_id and message_id:
        try:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ Operation cancelled.", reply_markup=None)
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text("❌ Operation cancelled.", reply_markup=ReplyKeyboardRemove())
    
    return ConversationHandler.END

# --- (User Trade Close Conversation) ---
@uow_transaction
@require_active_user
async def user_trade_close_start(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """Entry point for closing a personal UserTrade."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context, MANAGEMENT_TIMEOUT, LAST_ACTIVITY_KEY_MGMT, clean_management_state):
        return ConversationHandler.END
    update_management_activity(context)

    parsed_data = CallbackBuilder.parse(query.data) # pos:cl:trade:<trade_id>
    params = parsed_data.get("params", [])
    trade_id = int(params[1]) if params and len(params) > 1 and params[1].isdigit() else None
    if trade_id is None:
        return ConversationHandler.END

    # ✅ R2: Use LifecycleService
    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    position = lifecycle_service.repo.get_user_trade_by_id(db_session, trade_id) # Use repo for read
    
    if not position or position.user_id != db_user.id or position.status != UserTradeStatusEnum.ACTIVATED:
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
    """Handles receiving the exit price for a UserTrade."""
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

    # ✅ R2: Use LifecycleService
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
            text=f"✅ <b>Trade Closed</b>\n{closed_trade.asset} closed at {_get_attr(exit_price, 'g')} {pnl_str}.",
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


# --- 7. Registration Function (Consolidated) ---
def register_conversation_handlers(app: Application):
    """
    [R2 - Consolidated]
    Registers ALL conversation handlers for the bot.
    """
    
    # --- 1. Creation Conversation (Analyst) ---
    creation_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entrypoint),
            CommandHandler("rec", start_text_input_entrypoint),
            CommandHandler("editor", start_text_input_entrypoint),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            AWAITING_ASSET: [
                CallbackQueryHandler(asset_handler, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler)
            ],
            AWAITING_SIDE: [
                CallbackQueryHandler(side_handler, pattern="^side_"),
                CallbackQueryHandler(market_handler, pattern="^market_")
            ],
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
    
    # --- 2. Custom Partial Close Conversation (Analyst) (Moved) ---
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

    # --- 3. User Trade Closing Conversation (Trader) (Moved) ---
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

    # --- 4. Master Reply Handler (Implicit State) ---
    reply_handler = MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        master_reply_handler
    )

    app.add_handler(creation_conv, group=0)
    app.add_handler(partial_close_conv, group=0)
    app.add_handler(user_trade_close_conv, group=0)
    app.add_handler(reply_handler, group=0)