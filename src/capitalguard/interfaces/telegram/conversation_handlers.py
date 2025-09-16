# --- START OF FINAL, RE-ARCHITECTED FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import logging
import uuid
import types
from typing import List, Dict, Any
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
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
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
    return I_REVIEW

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
        prompt = (
            "4️⃣ أرسل الأسعار بصيغة سطر واحد:\n"
            "<code>STOP  TARGETS...</code>\n"
            "مثال:\n"
            "<code>58000   60000@30 62000@50</code>\n\n"
            "• STOP = وقف الخسارة\n"
            "• TARGETS = أهداف بشكل <code>سعر@نسبة</code>، آخر هدف يُغلق 100% تلقائيًا إذا لم تُحدد نسب."
        )
    else:
        prompt = (
            "4️⃣ أرسل الأسعار بصيغة سطر واحد:\n"
            "<code>ENTRY  STOP  TARGETS...</code>\n"
            "مثال:\n"
            "<code>59000  58000  60000@30 62000@50</code>\n\n"
            "• ENTRY = سعر الدخول\n"
            "• STOP = وقف الخسارة\n"
            "• TARGETS = أهداف بشكل <code>سعر@نسبة</code>، آخر هدف يُغلق 100% تلقائيًا إذا لم تُحدد نسب."
        )
    await query.message.edit_text(f"✅ Order Type: {order_type}\n\n{prompt}", parse_mode="HTML")
    return I_PRICES

async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    if not draft or 'order_type' not in draft:
        await update.message.reply_text("❌ مفقود نوع الأمر. ابدأ من جديد بـ /newrec.")
        return ConversationHandler.END

    order_type = draft['order_type'].upper()
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ لم أستطع قراءة الأسعار، أعد الإرسال وفق المثال المعروض.")
        return I_PRICES

    tokens = text.replace(",", " ").split()
    try:
        if order_type == 'MARKET':
            if len(tokens) < 2:
                raise ValueError("صيغة MARKET تتطلب: STOP ثم TARGETS...")
            stop_val = parse_number(tokens[0])
            targets = parse_targets_list(tokens[1:])
            draft['stop_loss'] = stop_val
            draft['targets'] = targets
            draft['entry'] = draft.get('entry') or stop_val
        else:
            if len(tokens) < 3:
                raise ValueError("صيغة LIMIT/STOP_MARKET تتطلب: ENTRY STOP ثم TARGETS...")
            entry_val = parse_number(tokens[0])
            stop_val = parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            draft['entry'] = entry_val
            draft['stop_loss'] = stop_val
            draft['targets'] = targets

    except ValueError as e:
        await update.message.reply_text(f"❌ خطأ في التنسيق: {e}\n\nأعد الإرسال وفق المثال المعروض.")
        return I_PRICES

    context.user_data[CONVERSATION_DATA_KEY] = draft
    return await show_review_card(update, context)

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if not message: return ConversationHandler.END
    review_key = context.user_data.get('current_review_key')
    data = context.bot_data.get(review_key) if review_key else context.user_data.get(CONVERSATION_DATA_KEY, {})
    if not data or not data.get("asset"):
        await message.reply_text("حدث خطأ، ابدأ من جديد بواسطة /newrec.")
        _clean_conversation_state(context)
        return ConversationHandler.END
    price_service = get_service(context, "price_service")
    preview_price = await price_service.get_cached_price(data["asset"], data.get("market", "Futures"))
    review_text = build_review_text_with_price(data, preview_price)
    if not review_key:
        review_key = str(uuid.uuid4())
        context.user_data['current_review_key'] = review_key
        context.bot_data[review_key] = data.copy()
    review_token = _get_or_make_token_for_review(context, review_key)
    context.user_data['current_review_token'] = review_token
    keyboard = review_final_keyboard(review_token)
    try:
        if is_edit and hasattr(message, 'edit_text'):
            await message.edit_text(text=review_text, reply_markup=keyboard, parse_mode='HTML', disable_web_page_preview=True)
        else:
            await message.reply_html(text=review_text, reply_markup=keyboard, disable_web_page_preview=True)
    except Exception as e:
        log.warning(f"Edit failed, sending new message. Error: {e}")
        await message.reply_html(text=review_text, reply_markup=keyboard, disable_web_page_preview=True)
    return I_REVIEW

async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    token = query.data.split(':')[2]
    review_key = _resolve_review_key_from_token(context, token)
    if not review_key or review_key not in context.bot_data:
        await query.message.edit_text("❌ انتهت صلاحية البطاقة."); return ConversationHandler.END
    context.user_data['current_review_key'] = review_key
    context.user_data['current_review_token'] = token
    context.user_data['original_query_message'] = query.message
    await query.message.edit_text(f"{query.message.text}\n\n✍️ أرسل ملاحظاتك الآن.", parse_mode='HTML', disable_web_page_preview=True)
    return I_NOTES

async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    notes = update.message.text.strip()
    review_key = context.user_data.get('current_review_key')
    original_message = context.user_data.pop('original_query_message', None)
    if review_key and review_key in context.bot_data and original_message:
        draft = context.bot_data[review_key]
        draft['notes'] = notes if notes.lower() not in ['skip', 'none'] else None
        try: await update.message.delete()
        except Exception: pass
        dummy_update = Update(update.update_id, callback_query=types.SimpleNamespace(message=original_message, data=''))
        return await show_review_card(dummy_update, context, is_edit=True)
    await update.message.reply_text("حدث خلل. ابدأ من جديد بـ /newrec.")
    return ConversationHandler.END

async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("جارٍ الحفظ والنشر...")
    token = query.data.split(":")[2]
    review_key = _resolve_review_key_from_token(context, token)
    draft = context.bot_data.get(review_key) if review_key else None
    if not draft:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec.")
        return ConversationHandler.END
        
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")

    with SessionLocal() as session:
        try:
            live_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
            
            saved_rec, report = trade_service.create_and_publish_recommendation(
                session=session,
                asset=draft["asset"], side=draft["side"], market=draft.get("market", "Futures"),
                entry=draft["entry"], stop_loss=draft["stop_loss"], targets=draft["targets"],
                notes=draft.get("notes"), user_id=str(update.effective_user.id),
                order_type=draft.get('order_type', 'LIMIT'), live_price=live_price
            )
            
            session.commit()

            if report.get("success"):
                success_count = len(report["success"])
                await query.edit_message_text(f"✅ تم الحفظ بنجاح ونشر التوصية #{saved_rec.id} إلى {success_count} قناة.")
            else:
                fail_reason = "غير معروف"
                if report.get("failed"):
                    fail_reason = report["failed"][0].get("reason", "فشل في الاتصال بـ API")
                await query.edit_message_text(
                    f"⚠️ تم حفظ التوصية #{saved_rec.id}، ولكن فشل النشر.\n"
                    f"<b>السبب:</b> {fail_reason}\n\n"
                    "<i>يرجى التحقق من أن البوت مسؤول في القناة ولديه صلاحية النشر.</i>",
                    parse_mode='HTML'
                )
        except Exception as e:
            session.rollback()
            log.exception("Handler failed to save/publish recommendation.")
            await query.edit_message_text(f"❌ فشل الحفظ/النشر: {e}")
        finally:
            _clean_conversation_state(context)
            
    return ConversationHandler.END

async def cancel_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _clean_conversation_state(context)
    await query.edit_message_text("تم إلغاء العملية.")
    return ConversationHandler.END

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    await update.message.reply_text("تم إلغاء المحادثة الحالية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def unexpected_input_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get(CONVERSATION_DATA_KEY) is not None or context.user_data.get('current_review_key'):
        user_message = "أمر أو زر غير متوقع."
        if update.message:
            await update.message.reply_text(f"⚠️ {user_message} تم إنهاء عملية إنشاء التوصية الحالية.")
        elif update.callback_query:
            await update.callback_query.answer("إجراء غير صالح.", show_alert=True)
            try: await update.callback_query.edit_message_text("تم إنهاء المحادثة.")
            except Exception: pass
    _clean_conversation_state(context)
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
            I_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)],
            I_REVIEW: [
                CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            MessageHandler(filters.COMMAND, unexpected_input_fallback),
            CallbackQueryHandler(unexpected_input_fallback),
        ],
        name="recommendation_creation",
        persistent=False,
    )
    app.add_handler(conv_handler)
# --- END OF FINAL, RE-ARCHITECTED FILE ---