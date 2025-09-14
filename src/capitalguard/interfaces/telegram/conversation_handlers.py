# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---
import logging
import uuid
import types
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
    market_choice_keyboard, order_type_keyboard, build_channel_picker_keyboard,
    main_creation_keyboard
)
from .parsers import parse_quick_command, parse_text_editor, parse_targets_list, parse_number
from .auth import ALLOWED_USER_FILTER
from .management_handlers import show_review_card

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

# --- State Definitions (Simplified) ---
(SELECT_METHOD, AWAIT_TEXT_INPUT, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE) = range(5)
CONVERSATION_DATA_KEY = "new_rec_draft"

def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    context.user_data.pop('input_mode', None)

# --- Entry Points ---
async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text(
        "🚀 إنشاء توصية جديدة.\n\nاختر طريقتك المفضلة للإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    trade_service: TradeService = get_service(context, "trade_service")
    user_id = str(update.effective_user.id)
    recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)
    await update.message.reply_text(
        "🚀 Interactive Builder\n\n1️⃣ اختر أصلاً أو اكتب الرمز:",
        reply_markup=asset_choice_keyboard(recent_assets)
    )
    return I_ASSET

async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data['input_mode'] = command
    
    if command == 'rec':
        await update.message.reply_text("⚡️ أرسل الآن توصيتك الكاملة في رسالة واحدة تبدأ بـ /rec")
    elif command == 'editor':
        await update.message.reply_text("📋 ألصق توصيتك الآن بشكل حقول.")
        
    return AWAIT_TEXT_INPUT

# --- State Handlers ---
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    if choice == "interactive":
        trade_service: TradeService = get_service(context, "trade_service")
        user_id = str(update.effective_user.id)
        recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)
        await query.message.edit_text("🚀 Interactive Builder\n\n1️⃣ اختر أصلاً أو اكتب الرمز:", reply_markup=asset_choice_keyboard(recent_assets))
        return I_ASSET
    elif choice == "quick":
        context.user_data['input_mode'] = 'rec'
        await query.message.edit_text("⚡️ أرسل الآن توصيتك الكاملة في رسالة واحدة تبدأ بـ /rec")
        return AWAIT_TEXT_INPUT
    elif choice == "editor":
        context.user_data['input_mode'] = 'editor'
        await query.message.edit_text("📋 ألصق توصيتك الآن بشكل حقول.")
        return AWAIT_TEXT_INPUT
    return ConversationHandler.END

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    mode = context.user_data.get('input_mode')
    text = update.message.text
    data = None
    if mode == 'rec':
        data = parse_quick_command(text)
        if not data:
            await update.message.reply_text("❌ صيغة غير صحيحة. حاول مرة أخرى.")
            return AWAIT_TEXT_INPUT
    elif mode == 'editor':
        data = parse_text_editor(text)
        if not data:
            await update.message.reply_text("❌ تعذّر تحليل النص. حاول مرة أخرى.")
            return AWAIT_TEXT_INPUT
    else:
        await update.message.reply_text("حدث خطأ غير متوقع. تم إلغاء المحادثة.")
        return ConversationHandler.END
    context.user_data[CONVERSATION_DATA_KEY] = data
    await show_review_card(update, context)
    return ConversationHandler.END

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    asset = ""
    message_obj = update.message
    if update.callback_query:
        await update.callback_query.answer()
        asset = update.callback_query.data.split('_', 1)[1]
        message_obj = update.callback_query.message
        if asset.lower() == "new":
            await message_obj.edit_text("✍️ أرسل رمز الأصل الآن (مثال: BTCUSDT).")
            return I_ASSET
    else:
        asset = (update.message.text or "").strip().upper()

    market_data_service = get_service(context, "market_data_service")
    if not market_data_service.is_valid_symbol(asset, "Futures"):
        await message_obj.reply_text(f"❌ الرمز '{asset}' غير صالح. حاول مرة أخرى.")
        return I_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await message_obj.reply_text(f"✅ Asset: {asset}\n\n2️⃣ اختر الاتجاه:", reply_markup=side_market_keyboard(draft['market']))
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['side'] = side
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await query.message.edit_text(f"✅ Asset: {draft.get('asset','N/A')} ({side})\n\n3️⃣ اختر نوع أمر الدخول:", reply_markup=order_type_keyboard())
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['order_type'] = order_type
    context.user_data[CONVERSATION_DATA_KEY] = draft
    
    prompt = ""
    if order_type == 'MARKET':
        prompt = "4️⃣ الآن، قم **بالرد على هذه الرسالة** ↩️ بالأسعار بالتنسيق التالي:\n`STOP TARGETS...`"
    else:
        prompt = "4️⃣ الآن، قم **بالرد على هذه الرسالة** ↩️ بالأسعار بالتنسيق التالي:\n`ENTRY STOP TARGETS...`"
        
    await query.message.edit_text(f"✅ Order Type: {order_type}\n\n{prompt}")
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    await update.message.reply_text("تم إلغاء المحادثة الحالية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_menu_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("new", start_interactive_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("rec", start_text_input_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("editor", start_text_input_entrypoint, filters=ALLOWED_USER_FILTER),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen)
            ],
            I_SIDE_MARKET: [CallbackQueryHandler(side_chosen, pattern="^side_")],
            I_ORDER_TYPE: [CallbackQueryHandler(order_type_chosen, pattern="^type_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        name="recommendation_creation",
        persistent=False,
    )
    app.add_handler(conv_handler)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---