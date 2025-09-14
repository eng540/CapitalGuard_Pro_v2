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

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

# --- State Definitions ---
(SELECT_METHOD, AWAIT_TEXT_INPUT, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(8)
CONVERSATION_DATA_KEY = "new_rec_draft"
REV_TOKENS_MAP = "review_tokens_map"
REV_TOKENS_REVERSE = "review_tokens_rev"

# --- Helper Functions ---
def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    review_key = context.user_data.pop('current_review_key', None)
    if review_key: context.bot_data.pop(review_key, None)
    review_token = context.user_data.pop('current_review_token', None)
    if review_token: context.user_data.pop(f"pubsel:{review_token}", None)
    context.user_data.pop('original_query_message', None)
    context.user_data.pop('input_mode', None)

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

# --- Entry Point Functions ---
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
    command = update.message.text.split()[0].lower().replace('/', '')
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
    if order_type == 'MARKET':
        prompt = "4️⃣ الآن، أرسل الأسعار بالتنسيق:\n`STOP TARGETS...`\nمثال: `114000 116000@30 119000@70`"
    else:
        prompt = "4️⃣ الآن، أرسل الأسعار بالتنسيق:\n`ENTRY STOP TARGETS...`\nمثال: `115000 114000 116000 119000`"
    await query.message.edit_text(f"✅ Order Type: {order_type}\n\n{prompt}")
    return I_PRICES

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
        order_type = draft.get('order_type')
        parts = (update.message.text or "").strip().replace(',', ' ').split()
        if order_type == 'MARKET':
            if len(parts) < 2: raise ValueError("At least Stop Loss and one Target are required.")
            draft["entry"] = 0
            draft["stop_loss"] = parse_number(parts[0])
            draft["targets"] = parse_targets_list(parts[1:])
        else:
            if len(parts) < 3: raise ValueError("Entry, Stop, and at least one Target are required.")
            draft["entry"] = parse_number(parts[0])
            draft["stop_loss"] = parse_number(parts[1])
            draft["targets"] = parse_targets_list(parts[2:])
        if not draft["targets"]: raise ValueError("No valid targets were parsed.")
        context.user_data[CONVERSATION_DATA_KEY] = draft
        await show_review_card(update, context)
        return ConversationHandler.END
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ تنسيق أسعار غير صالح: {e}. حاول مرة أخرى.")
        return I_PRICES

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
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received_interactive)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        name="recommendation_creation",
        persistent=False,
    )
    app.add_handler(conv_handler)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE ---