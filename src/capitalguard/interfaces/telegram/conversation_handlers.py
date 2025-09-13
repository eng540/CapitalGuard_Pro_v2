# --- START OF FULL, RE-ARCHITECTED, AND FINAL FILE ---
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
(SELECT_METHOD, QUICK_COMMAND, TEXT_EDITOR, I_ASSET, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(9)
CONVERSATION_DATA_KEY = "new_rec_draft"
REV_TOKENS_MAP = "review_tokens_map"
REV_TOKENS_REVERSE = "review_tokens_rev"

# --- Helper Functions ---
def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """A centralized function to clean up all conversation-related data."""
    review_key = context.user_data.pop('current_review_key', None)
    if review_key:
        context.bot_data.pop(review_key, None)
    
    review_token = context.user_data.pop('current_review_token', None)
    if review_token:
        context.user_data.pop(f"pubsel:{review_token}", None)

    for key in (CONVERSATION_DATA_KEY, 'last_interactive_message_id', 'original_query_message'):
        context.user_data.pop(key, None)

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

# --- Entry Point Functions for Conversation Handler ---

async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for /newrec. Always shows the method selection menu.
    """
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text(
        "🚀 إنشاء توصية جديدة.\n\nاختر طريقتك المفضلة للإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for /new. Starts the interactive builder directly.
    """
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

async def start_quick_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for /rec. Starts the quick command mode directly.
    """
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text(
        "⚡️ وضع الأمر السريع.\n\n"
        "أرسل توصيتك الآن برسالة واحدة تبدأ بـ /rec\n"
        "مثال: /rec BTCUSDT LONG 65000 64000 66k"
    )
    return QUICK_COMMAND

async def start_editor_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Entry point for /editor. Starts the text editor mode directly.
    """
    _clean_conversation_state(context)
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text(
        "📋 وضع المحرّر النصي.\n\n"
        "ألصق توصيتك الآن بشكل حقول."
    )
    return TEXT_EDITOR

# --- State Handlers ---

async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handles the button press after the /newrec menu.
    """
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]

    if choice == "interactive":
        trade_service: TradeService = get_service(context, "trade_service")
        user_id = str(update.effective_user.id)
        recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)
        await query.message.edit_text(
            "🚀 Interactive Builder\n\n1️⃣ اختر أصلاً أو اكتب الرمز:",
            reply_markup=asset_choice_keyboard(recent_assets)
        )
        return I_ASSET
    elif choice == "quick":
        await query.message.edit_text(
            "⚡️ وضع الأمر السريع.\n\n"
            "أرسل توصيتك الآن برسالة واحدة تبدأ بـ /rec\n"
            "مثال: /rec BTCUSDT LONG 65000 64000 66k"
        )
        return QUICK_COMMAND
    elif choice == "editor":
        await query.message.edit_text(
            "📋 وضع المحرّر النصي.\n\n"
            "ألصق توصيتك الآن بشكل حقول."
        )
        return TEXT_EDITOR
    return ConversationHandler.END

async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_quick_command(update.message.text)
    if not data:
        await update.message.reply_text("❌ صيغة غير صحيحة. تأكد من أن الأمر يبدأ بـ /rec ويحتوي على جميع الحقول.")
        return QUICK_COMMAND
    context.user_data[CONVERSATION_DATA_KEY] = data
    return await show_review_card(update, context)

async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_text_editor(update.message.text)
    if not data:
        await update.message.reply_text("❌ تعذّر تحليل النص. تأكد من وجود الحقول الأساسية (Asset, Side, Entry, Stop, Targets).")
        return TEXT_EDITOR
    if 'order_type' not in data or not data['order_type']:
        data['order_type'] = 'LIMIT'
    context.user_data[CONVERSATION_DATA_KEY] = data
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
    preview_price = price_service.get_preview_price(data["asset"], data.get("market", "Futures"))
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

async def asset_chosen_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    asset = query.data.split('_', 1)[1]
    
    if asset.lower() == "new":
        await query.message.edit_text("✍️ أرسل رمز الأصل الآن (مثال: BTCUSDT).")
        return I_ASSET
        
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['asset'] = asset.upper()
    market = context.user_data.get('preferred_market', 'Futures')
    draft['market'] = market
    context.user_data[CONVERSATION_DATA_KEY] = draft
    
    await query.message.edit_text(f"✅ Asset: {asset.upper()}\n\n2️⃣ اختر الاتجاه:", reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def asset_chosen_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_asset = (update.message.text or "").strip().upper()
    if not raw_asset:
        await update.message.reply_text("⚠️ إدخال غير صالح. الرجاء إرسال رمز.")
        return I_ASSET

    market_data_service: MarketDataService = get_service(context, "market_data_service")
    preferred_market = context.user_data.get('preferred_market', 'Futures')
    
    if not market_data_service.is_valid_symbol(raw_asset, preferred_market):
        error_msg = (
            f"❌ الرمز '{raw_asset}' غير صالح أو غير متوفر في السوق الافتراضي ({preferred_market}).\n\n"
            "تأكد من أن الرمز صحيح وحاول مرة أخرى."
        )
        await update.message.reply_text(error_msg)
        return I_ASSET

    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['asset'] = raw_asset
    draft['market'] = preferred_market
    context.user_data[CONVERSATION_DATA_KEY] = draft
    
    await update.message.reply_text(
        f"✅ Asset: {raw_asset}\n\n2️⃣ اختر الاتجاه:",
        reply_markup=side_market_keyboard(preferred_market)
    )
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['side'] = side
    context.user_data[CONVERSATION_DATA_KEY] = draft
    asset = draft.get('asset', 'N/A')
    await query.message.edit_text(f"✅ Asset: {asset} ({side})\n\n3️⃣ اختر نوع أمر الدخول:", reply_markup=order_type_keyboard())
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    draft['order_type'] = order_type
    context.user_data[CONVERSATION_DATA_KEY] = draft
    if order_type == 'MARKET':
        await query.message.edit_text("✅ Order Type: Market\n\n4️⃣ أرسل: `STOP TARGETS...`")
    else:
        await query.message.edit_text(f"✅ Order Type: {order_type}\n\n4️⃣ أرسل: `ENTRY STOP TARGETS...`")
    return I_PRICES

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
        order_type = draft.get('order_type')
        parts = update.message.text.strip().replace(',', ' ').split()
        
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
            
        if not draft["targets"]:
            raise ValueError("No valid targets were parsed.")

        context.user_data[CONVERSATION_DATA_KEY] = draft
        return await show_review_card(update, context)
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ تنسيق أسعار غير صالح: {e}. حاول مرة أخرى.")
        return I_PRICES

async def change_market_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_reply_markup(reply_markup=market_choice_keyboard())
    return I_SIDE_MARKET

async def market_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    draft = context.user_data.get(CONVERSATION_DATA_KEY, {})
    market = draft.get('market', 'Futures')
    if choice != "market_back":
        market = choice.split('_')[1]
        context.user_data['preferred_market'] = market
    draft['market'] = market
    context.user_data[CONVERSATION_DATA_KEY] = draft
    await query.message.edit_reply_markup(reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

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
    await query.message.edit_text(f"{query.message.text}\n\n✍️ أرسل ملاحظاتك الآن.")
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
    
    trade_service = get_service(context, "trade_service")
    try:
        live_price = get_service(context, "price_service").get_cached_price(draft["asset"], draft.get("market", "Futures"))
        entry_val = draft["entry"]
        entry_price = entry_val[0] if isinstance(entry_val, list) else entry_val
        if isinstance(entry_val, list):
            draft.setdefault("notes", "")
            draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"
        
        saved_rec = trade_service.create_recommendation(
            asset=draft["asset"], side=draft["side"], market=draft.get("market", "Futures"),
            entry=entry_price, stop_loss=draft["stop_loss"], targets=draft["targets"],
            notes=draft.get("notes"), user_id=str(update.effective_user.id),
            order_type=draft.get('order_type', 'LIMIT'), live_price=live_price
        )
        
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
            await update.callback_query.answer("إجراء غير صالح. تم إنهاء المحادثة الحالية.", show_alert=True)
            try:
                await update.callback_query.edit_message_text("تم إنهاء المحادثة.")
            except Exception:
                pass
    
    _clean_conversation_state(context)
    return ConversationHandler.END

async def choose_channels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    token = query.data.split(":")[2]
    review_key = _resolve_review_key_from_token(context, token)
    if not review_key or review_key not in context.bot_data:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec."); return
    context.user_data['current_review_key'] = review_key
    context.user_data['current_review_token'] = token
    channels = _load_user_active_channels(query.from_user.id)
    if not channels:
        await query.edit_message_text("ℹ️ لا توجد قنوات مرتبطة بحسابك.\nاستخدم: /link_channel ثم أعد المحاولة."); return
    sel_key = f"pubsel:{token}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    if not isinstance(selected, set): selected = set(); context.user_data[sel_key] = selected
    kb = build_channel_picker_keyboard(token, channels, selected, page=1)
    await query.edit_message_text("📢 اختر القنوات التي تريد النشر إليها ثم اضغط «🚀 نشر المحدد».", reply_markup=kb)

async def channel_picker_nav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, token, page_s = query.data.split(":")
    page = int(page_s)
    channels = _load_user_active_channels(query.from_user.id)
    sel_key = f"pubsel:{token}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    kb = build_channel_picker_keyboard(token, channels, selected, page=page)
    await query.edit_message_reply_markup(reply_markup=kb)

async def channel_picker_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, token, tg_id_s, page_s = query.data.split(":")
    tg_id, page = int(tg_id_s), int(page_s)
    sel_key = f"pubsel:{token}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    if tg_id in selected: selected.remove(tg_id)
    else: selected.add(tg_id)
    context.user_data[sel_key] = selected
    channels = _load_user_active_channels(query.from_user.id)
    kb = build_channel_picker_keyboard(token, channels, selected, page=page)
    await query.edit_message_reply_markup(reply_markup=kb)

async def channel_picker_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_review_card(update, context, is_edit=True)

async def channel_picker_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جارٍ النشر للقنوات المحددة...")
    token = query.data.split(":")[2]
    review_key = _resolve_review_key_from_token(context, token)
    draft = context.bot_data.get(review_key) if review_key else None
    sel_key = f"pubsel:{token}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    if not draft: await query.edit_message_text("❌ انتهت صلاحية البطاقة."); return ConversationHandler.END
    if not selected: await query.edit_message_text("⚠️ لم تختر أي قناة."); return
    trade_service = get_service(context, "trade_service")
    try:
        live_price = get_service(context, "price_service").get_cached_price(draft["asset"], draft.get("market", "Futures"))
        entry_val = draft["entry"]
        entry_price = entry_val[0] if isinstance(entry_val, list) else entry_val
        if isinstance(entry_val, list): draft.setdefault("notes", "") ; draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"
        rec = trade_service.create_recommendation(
            asset=draft["asset"], side=draft["side"], market=draft.get("market", "Futures"),
            entry=entry_price, stop_loss=draft["stop_loss"], targets=draft["targets"],
            notes=draft.get("notes"), user_id=str(query.from_user.id),
            order_type=draft.get('order_type', 'LIMIT'), live_price=live_price,
        )
        trade_service.publish_recommendation(rec_id=rec.id, user_id=str(query.from_user.id), channel_ids=list(selected))
        await query.edit_message_text(f"✅ تم الحفظ والنشر للقنوات المختارة للتوصية #{rec.id}.")
    except Exception as e:
        log.exception("Failed to save/publish to selected channels.")
        await query.edit_message_text(f"❌ فشل النشر: {e}")
    finally:
        _clean_conversation_state(context)
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_menu_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("new", start_interactive_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("rec", start_quick_entrypoint, filters=ALLOWED_USER_FILTER),
            CommandHandler("editor", start_editor_entrypoint, filters=ALLOWED_USER_FILTER),
        ],
        states={
            SELECT_METHOD: [
                CallbackQueryHandler(method_chosen, pattern="^method_")
            ],
            QUICK_COMMAND: [
                MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)
            ],
            TEXT_EDITOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)
            ],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen_button, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen_text)
            ],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"),
                CallbackQueryHandler(market_chosen, pattern="^market_")
            ],
            I_ORDER_TYPE: [
                CallbackQueryHandler(order_type_chosen, pattern="^type_")
            ],
            I_PRICES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received_interactive)
            ],
            I_REVIEW: [
                CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(choose_channels_handler, pattern=r"^rec:choose_channels:"),
                CallbackQueryHandler(channel_picker_nav_handler, pattern=r"^pubsel:nav:"),
                CallbackQueryHandler(channel_picker_toggle_handler, pattern=r"^pubsel:toggle:"),
                CallbackQueryHandler(channel_picker_confirm_handler, pattern=r"^pubsel:confirm:"),
                CallbackQueryHandler(channel_picker_back_handler, pattern=r"^pubsel:back:"),
                CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            MessageHandler(filters.COMMAND, unexpected_input_fallback),
            CallbackQueryHandler(unexpected_input_fallback),
        ],
        name="new_recommendation_conversation",
        persistent=False,
        per_user=True,
        per_chat=False,
        per_message=False,
    )
    app.add_handler(creation_conv_handler)
# --- END OF FULL, RE-ARCHITECTED, AND FINAL FILE ---