# src/capitalguard/interfaces/telegram/conversation_handlers.py (v35.6 - Production Ready & Final)
"""
معالجات المحادثات النهائية والمستقرة لبيئة الإنتاج.
✅ إصلاح شامل لمشكلة تجميد لوحة اختيار القنوات.
✅ تطبيق نظام مهلات قوي وإدارة جلسات آمنة عبر التوكن.
✅ إعادة هيكلة المعالجات للاعتماد الكامل على CallbackBuilder.
✅ تحسين معالجة الأخطاء لضمان تجربة مستخدم سلسة.
"""

import logging
import uuid
import time
from decimal import Decimal, InvalidOperation

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest
from telegram.constants import ParseMode

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard, CallbackBuilder, CallbackNamespace, CallbackAction
)
from .auth import require_active_user, require_analyst_user
from .parsers import parse_rec_command, parse_editor_command, parse_number, parse_targets_list
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

# --- State Management Keys ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"
LAST_ACTIVITY_KEY = "last_creation_activity"

# --- Timeout Configuration ---
CONVERSATION_TIMEOUT = 1800  # 30 دقيقة

# --- Helper Functions ---

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة إنشاء التوصية بشكل كامل."""
    for key in [DRAFT_KEY, CHANNEL_PICKER_KEY, LAST_ACTIVITY_KEY]:
        context.user_data.pop(key, None)

def update_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير."""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

async def safe_edit_message(query, text=None, reply_markup=None, parse_mode=ParseMode.HTML):
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

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """معالجة انتهاء مدة المحادثة."""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    if time.time() - last_activity > CONVERSATION_TIMEOUT:
        clean_creation_state(context)
        message = "⏰ انتهت مدة الجلسة. يرجى البدء من جديد باستخدام /newrec"
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await safe_edit_message(update.callback_query, text=message)
        elif update.message:
            await update.message.reply_text(message)
        return True
    return False

# --- Entry Points ---

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة."""
    clean_creation_state(context)
    update_activity(context)
    await update.message.reply_html("🚀 <b>منشئ التوصيات</b>\n\nاختر طريقة الإدخال:", reply_markup=main_creation_keyboard())
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """بدء الإدخال النصي السريع."""
    clean_creation_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    update_activity(context)
    
    prompt = "⚡️ <b>وضع الأمر السريع</b>\nأدخل توصيتك في سطر واحد:\n<code>الأصل الاتجاه الدخول الوقف الهدف1 الهدف2...</code>" if command == 'rec' else "📋 <b>وضع المحرر النصي</b>\nالصق توصيتك بتنسيق <code>مفتاح: قيمة</code>"
    await update.message.reply_html(prompt)
    return AWAIT_TEXT_INPUT

# --- State Handlers ---

@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالجة اختيار طريقة الإدخال."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
    draft = context.user_data[DRAFT_KEY]
    if "back" in query.data:
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(draft.get('market', 'Futures')))
    else:
        market = query.data.split("_")[1]
        draft['market'] = market
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(market))
    return AWAITING_SIDE

async def type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار نوع الطلب."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type
    
    prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>وقف الخسارة الأهداف...</code>" if order_type == 'MARKET' else "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>"
    await safe_edit_message(query, text=f"✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}")
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الأسعار."""
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
    draft = context.user_data[DRAFT_KEY]
    tokens = (update.message.text or "").strip().split()
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        if draft["order_type"] == 'MARKET':
            if len(tokens) < 2: raise ValueError("تنسيق السوق: وقف الخسارة ثم الأهداف...")
            stop_loss, targets = parse_number(tokens[0]), parse_targets_list(tokens[1:])
            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), True)
            if not live_price_float: raise ValueError("تعذر جلب سعر السوق الحالي.")
            live_price = Decimal(str(live_price_float))
            trade_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            draft.update({"entry": live_price, "stop_loss": stop_loss, "targets": targets})
        else:
            if len(tokens) < 3: raise ValueError("تنسيق LIMIT/STOP: سعر الدخول، وقف الخسارة، ثم الأهداف...")
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            trade_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
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
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة مراجعة التوصية."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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
            selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, {ch.telegram_channel_id for ch in ChannelRepository(db_session).list_by_analyst(UserRepository(db_session).find_by_telegram_id(query.from_user.id).id, only_active=True)})
            draft['target_channel_ids'] = selected_ids
            trade_service = get_service(context, "trade_service", TradeService)
            rec, report = await trade_service.create_and_publish_recommendation_async(str(query.from_user.id), db_session, **draft)
            msg = f"✅ تم النشر في {len(report.get('success', []))} قناة." if report.get("success") else "⚠️ تم الحفظ ولكن فشل النشر."
            await safe_edit_message(query, text=msg)
            clean_creation_state(context)
            return ConversationHandler.END
            
        elif action == "choose_channels":
            user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
            all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
            selected_ids = context.user_data.setdefault(CHANNEL_PICKER_KEY, {ch.telegram_channel_id for ch in all_channels if ch.is_active})
            keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids)
            await safe_edit_message(query, text="📢 **اختر القنوات للنشر**", reply_markup=keyboard)
            return AWAITING_CHANNELS
            
        elif action == "add_notes":
            await safe_edit_message(query, text="📝 **أضف ملاحظاتك**")
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

async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الملاحظات."""
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
    draft = context.user_data[DRAFT_KEY]
    draft['notes'] = (update.message.text or '').strip()
    await show_review_card(update, context)
    return AWAITING_REVIEW

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة اختيار القنوات - الإصدار النهائي المصحح."""
    query = update.callback_query
    await query.answer()
    if await handle_timeout(update, context): return ConversationHandler.END
    update_activity(context)
    
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

        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())

        if action == CallbackAction.BACK.value:
            await show_review_card(update, context)
            return AWAITING_REVIEW

        elif action == CallbackAction.CONFIRM.value:
            if not selected_ids:
                await query.answer("❌ لم يتم اختيار أي قنوات", show_alert=True)
                return AWAITING_CHANNELS
            
            draft['target_channel_ids'] = selected_ids
            trade_service = get_service(context, "trade_service", TradeService)
            rec, report = await trade_service.create_and_publish_recommendation_async(str(query.from_user.id), db_session, **draft)
            msg = f"✅ تم النشر في {len(report.get('success', []))} قناة." if report.get("success") else "❌ فشل النشر."
            await safe_edit_message(query, text=msg)
            clean_creation_state(context)
            return ConversationHandler.END

        else: # Handles TOGGLE and NAV
            page = 1
            if action == CallbackAction.TOGGLE.value:
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

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإلغاء."""
    clean_creation_state(context)
    await update.message.reply_text("❌ تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    """تسجيل معالجات المحادثة."""
    conv_handler = ConversationHandler(
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
            AWAITING_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_handler)],
            AWAITING_CHANNELS: [CallbackQueryHandler(channel_picker_handler, pattern=f"^{CallbackNamespace.PUBLICATION.value}:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        name="recommendation_creation",
        per_user=True,
        per_chat=True,
        conversation_timeout=CONVERSATION_TIMEOUT,
    )
    app.add_handler(conv_handler)