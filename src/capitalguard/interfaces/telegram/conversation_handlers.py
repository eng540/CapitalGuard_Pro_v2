# --- START OF FINAL, CORRECTED AND ROBUST FILE (V5): src/capitalguard/interfaces/telegram/conversation_handlers.py ---
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

(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"
REV_TOKENS_MAP = "review_tokens_map"
REV_TOKENS_REVERSE = "review_tokens_rev"

def _clean_conversation_state(context: ContextTypes.DEFAULT_TYPE):
    """A centralized function to clean up all conversation-related data."""
    review_key = context.user_data.pop('current_review_key', None)
    if review_key:
        context.bot_data.pop(review_key, None)
    
    review_token = context.user_data.pop('current_review_token', None)
    if review_token:
        sel_key = f"pubsel:{review_token}"
        context.user_data.pop(sel_key, None)

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

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if not message: return ConversationHandler.END
    review_key = context.user_data.get('current_review_key')
    data = context.bot_data.get(review_key) if review_key else context.user_data.get(CONVERSATION_DATA_KEY, {})
    if not data or not data.get("asset"):
        await message.reply_text("حدث خطأ، ابدأ من جديد بواسطة /newrec.")
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
            order_type=draft['order_type'], live_price=live_price,
        )
        
        _, report = trade_service.publish_recommendation(rec_id=saved_rec.id, user_id=str(update.effective_user.id))
        
        if report["success"]:
            await query.edit_message_text(f"✅ تم الحفظ والنشر بنجاح للتوصية #{saved_rec.id}.")
        else:
            await query.edit_message_text(f"⚠️ تم حفظ التوصية #{saved_rec.id}، ولكن فشل النشر (قد لا تكون هناك قنوات مرتبطة).")
    except Exception as e:
        log.exception("Failed to save/publish recommendation.")
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
    """Handles any input that is not expected in the current conversation state."""
    # Check if there's an active conversation to cancel
    if context.user_data.get(CONVERSATION_DATA_KEY) or context.user_data.get('current_review_key'):
        if update.message:
            await update.message.reply_text(
                "⚠️ تم استلام مدخل غير متوقع. تم إنهاء عملية إنشاء التوصية الحالية.\n"
                "يمكنك الآن استخدام الأوامر الأخرى بشكل طبيعي."
            )
        elif update.callback_query:
            await update.callback_query.answer("إجراء غير صالح. تم إنهاء المحادثة الحالية.", show_alert=True)
            # Try to edit the message to remove the old keyboard
            try:
                await update.callback_query.edit_message_text("تم إنهاء المحادثة.")
            except Exception:
                pass
    
    _clean_conversation_state(context)
    # Let the update be processed by other handlers
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
            order_type=draft["order_type"], live_price=live_price,
        )
        trade_service.publish_recommendation(rec_id=rec.id, user_id=str(query.from_user.id), channel_ids=list(selected))
        await query.edit_message_text(f"✅ تم الحفظ والنشر للقنوات المختارة للتوصية #{rec.id}.")
    except Exception as e:
        log.exception("Failed to save/publish to selected channels.")
        await query.edit_message_text(f"❌ فشل النشر: {e}")
    finally:
        _clean_conversation_state(context)
    return ConversationHandler.END

async def change_method_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("⚙️ اختر طريقتك المفضلة:", reply_markup=main_creation_keyboard())
    return CHOOSE_METHOD

async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    context.user_data[USER_PREFERENCE_KEY] = choice
    if choice == "interactive": return await start_interactive_builder(update, context)
    elif choice == "quick":
        await query.message.edit_text("⚡️ وضع الأمر السريع.\n\nأرسل /rec الآن.", reply_markup=change_method_keyboard())
        return QUICK_COMMAND
    elif choice == "editor":
        await query.message.edit_text("📋 وضع المحرّر النصي.\n\nألصق التوصية.", reply_markup=change_method_keyboard())
        return TEXT_EDITOR
    return ConversationHandler.END

async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_quick_command(update.message.text)
    if not data: await update.message.reply_text("❌ صيغة غير صحيحة."); return QUICK_COMMAND
    context.user_data[CONVERSATION_DATA_KEY] = data
    return await show_review_card(update, context)

async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_text_editor(update.message.text)
    if not data: await update.message.reply_text("❌ تعذّر تحليل النص."); return TEXT_EDITOR
    if 'order_type' not in data or not data['order_type']: data['order_type'] = 'LIMIT'
    context.user_data[CONVERSATION_DATA_KEY] = data
    return await show_review_card(update, context)

async def start_interactive_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clean_conversation_state(context)
    message = update.message or update.callback_query.message
    context.user_data[CONVERSATION_DATA_KEY] = {}
    trade_service = get_service(context, "trade_service")
    user_id = str(update.effective_user.id)
    recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)
    sent_message = await message.reply_text("🚀 Interactive Builder\n\n1️⃣ اختر أصلاً أو اكتب الرمز:", reply_markup=asset_choice_keyboard(recent_assets))
    context.user_data['last_interactive_message_id'] = sent_message.message_id
    return I_ASSET_CHOICE

async def asset_chosen_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    asset = query.data.split('_', 1)[1]
    if asset.lower() == "new":
        await query.message.edit_text("✍️ أرسل رمز الأصل الآن (مثال: BTCUSDT).")
        return I_ASSET_CHOICE
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['asset'] = asset.upper()
    market = context.user_data.get('preferred_market', 'Futures')
    draft['market'] = market
    await query.message.edit_text(f"✅ Asset: {asset.upper()}\n\n2️⃣ اختر الاتجاه:", reply_markup=side_market_keyboard(market))
    return I_SIDE_MARKET

async def asset_chosen_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    last_message_id = context.user_data.pop('last_interactive_message_id', None)
    if last_message_id:
        try: await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_message_id)
        except Exception: pass
    raw = (update.message.text or "").strip()
    if raw.lower() in {"new", "جديد"}:
        sent = await update.message.reply_text("⚠️ هذا زر إضافة. اكتب رمزًا حقيقيًا.")
        context.user_data['last_interactive_message_id'] = sent.message_id
        return I_ASSET_CHOICE
    asset = raw.upper()
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['asset'] = asset
    market = context.user_data.get('preferred_market', 'Futures')
    draft['market'] = market
    sent_message = await update.message.reply_text(f"✅ Asset: {asset}\n\n2️⃣ اختر الاتجاه:", reply_markup=side_market_keyboard(market))
    context.user_data['last_interactive_message_id'] = sent_message.message_id
    return I_SIDE_MARKET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    context.user_data[CONVERSATION_DATA_KEY]['side'] = side
    asset = context.user_data[CONVERSATION_DATA_KEY]['asset']
    await query.message.edit_text(f"✅ Asset: {asset} ({side})\n\n3️⃣ اختر نوع أمر الدخول:", reply_markup=order_type_keyboard())
    return I_ORDER_TYPE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['order_type'] = order_type
    if order_type == 'MARKET': await query.message.edit_text("✅ Order Type: Market\n\n4️⃣ أرسل: `STOP TARGETS...`")
    else: await query.message.edit_text(f"✅ Order Type: {order_type}\n\n4️⃣ أرسل: `ENTRY STOP TARGETS...`")
    return I_PRICES

def _parse_price_string(price_str: str) -> float:
    s = price_str.strip().lower()
    if 'k' in s: return float(s.replace('k', '')) * 1000
    return float(s)

async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        draft = context.user_data[CONVERSATION_DATA_KEY]
        order_type = draft.get('order_type')
        parts = update.message.text.strip().replace(',', ' ').split()
        if order_type == 'MARKET':
            if len(parts) < 2: raise ValueError("At least Stop Loss and one Target are required.")
            draft["entry"], draft["stop_loss"] = 0, _parse_price_string(parts[0])
            draft["targets"] = [_parse_price_string(t) for t in parts[1:]]
        else:
            if len(parts) < 3: raise ValueError("Entry, Stop, and at least one Target are required.")
            draft["entry"], draft["stop_loss"] = _parse_price_string(parts[0]), _parse_price_string(parts[1])
            draft["targets"] = [_parse_price_string(t) for t in parts[2:]]
        return await show_review_card(update, context)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ تنسيق أسعار غير صالح. حاول مرة أخرى.")
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
    market = context.user_data[CONVERSATION_DATA_KEY].get('market', 'Futures')
    if choice != "market_back":
        market = choice.split('_')[1]
        context.user_data['preferred_market'] = market
    context.user_data[CONVERSATION_DATA_KEY]['market'] = market
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

def register_conversation_handlers(app: Application):
    change_method_cb = CallbackQueryHandler(change_method_handler, pattern="^change_method$")
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_USER_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER),
        ],
        states={
            CHOOSE_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_"), change_method_cb, MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen_text)],
            QUICK_COMMAND: [change_method_cb, MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)],
            TEXT_EDITOR: [change_method_cb, MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)],
            I_ASSET_CHOICE: [change_method_cb, CallbackQueryHandler(asset_chosen_button, pattern="^asset_"), MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen_text)],
            I_SIDE_MARKET: [change_method_cb, CallbackQueryHandler(side_chosen, pattern="^side_"), CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"), CallbackQueryHandler(market_chosen, pattern="^market_")],
            I_ORDER_TYPE: [change_method_cb, CallbackQueryHandler(order_type_chosen, pattern="^type_")],
            I_PRICES: [change_method_cb, MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received_interactive)],
            I_REVIEW: [
                change_method_cb, CallbackQueryHandler(add_notes_handler, pattern=r"^rec:add_notes:"),
                CallbackQueryHandler(publish_handler, pattern=r"^rec:publish:"),
                CallbackQueryHandler(choose_channels_handler, pattern=r"^rec:choose_channels:"),
                CallbackQueryHandler(channel_picker_nav_handler, pattern=r"^pubsel:nav:"),
                CallbackQueryHandler(channel_picker_toggle_handler, pattern=r"^pubsel:toggle:"),
                CallbackQueryHandler(channel_picker_confirm_handler, pattern=r"^pubsel:confirm:"),
                CallbackQueryHandler(channel_picker_back_handler, pattern=r"^pubsel:back:"),
                CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [change_method_cb, MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)],
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
# --- END OF FINAL, CORRECTED AND ROBUST FILE (V5) ---