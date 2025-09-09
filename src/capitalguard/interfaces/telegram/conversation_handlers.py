# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
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

# DB access for channels listing
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"


# =========================
# Review Card
# =========================
async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False) -> int:
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if not message:
        log.warning("No message object available to render review card.")
        return ConversationHandler.END

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

    keyboard = review_final_keyboard(review_key)
    try:
        if is_edit and hasattr(message, 'edit_text'):
            await message.edit_text(
                text=review_text,
                reply_markup=keyboard,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        else:
            await message.reply_html(
                text=review_text,
                reply_markup=keyboard,
                disable_web_page_preview=True
            )
    except Exception as e:
        log.warning(f"Edit failed, sending new message. Error: {e}")
        await message.reply_html(
            text=review_text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
    return I_REVIEW


# =========================
# Publish / Cancel
# =========================
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("جارٍ الحفظ ثم النشر...")
    review_key = query.data.split(":")[2]
    draft = context.bot_data.get(review_key)
    if not draft:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec.")
        return ConversationHandler.END

    trade_service = get_service(context, "trade_service")
    try:
        live_price = get_service(context, "price_service").get_cached_price(
            draft["asset"], draft.get("market", "Futures")
        )
        entry_val = draft["entry"]
        entry_price = entry_val[0] if isinstance(entry_val, list) else entry_val
        if isinstance(entry_val, list):
            draft.setdefault("notes", "")
            draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"

        rec = trade_service.create_and_publish_recommendation(
            asset=draft["asset"],
            side=draft["side"],
            market=draft.get("market", "Futures"),
            entry=entry_price,
            stop_loss=draft["stop_loss"],
            targets=draft["targets"],
            notes=draft.get("notes"),
            user_id=str(query.from_user.id),
            order_type=draft['order_type'],
            live_price=live_price,
            publish=True,
        )
        await query.edit_message_text(f"✅ تم الحفظ، ومحاولة النشر انطلقت للتوصية #{rec.id}.")
    except Exception as e:
        log.exception("Failed to save/publish recommendation.")
        await query.edit_message_text(f"❌ فشل الحفظ/النشر: {e}")
    finally:
        context.bot_data.pop(review_key, None)
        context.user_data.pop('current_review_key', None)
    return ConversationHandler.END


async def cancel_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    review_key = query.data.split(":")[2]
    context.bot_data.pop(review_key, None)
    context.user_data.pop('current_review_key', None)
    await query.edit_message_text("تم إلغاء العملية.")
    return ConversationHandler.END


async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in (CONVERSATION_DATA_KEY, 'current_review_key', 'last_interactive_message_id'):
        context.user_data.pop(key, None)
    await update.message.reply_text("تم الإلغاء.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# =========================
# قناة: اختيار قنوات محددة للنشر
# =========================
def _load_user_active_channels(user_tg_id: int) -> List[Dict[str, Any]]:
    with SessionLocal() as s:
        urepo = UserRepository(s)
        crepo = ChannelRepository(s)
        user = urepo.find_or_create(user_tg_id)
        chans = crepo.list_by_user(user.id, only_active=True)
        return [
            {
                "id": ch.id,
                "telegram_channel_id": int(ch.telegram_channel_id),
                "username": ch.username,
                "title": getattr(ch, "title", None),
            }
            for ch in chans
        ]


async def choose_channels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    review_key = query.data.split(":")[2]
    context.user_data['current_review_key'] = review_key

    channels = _load_user_active_channels(query.from_user.id)
    if not channels:
        await query.edit_message_text(
            "ℹ️ لا توجد قنوات مرتبطة بحسابك.\n"
            "استخدم: /link_channel ثم أعد المحاولة."
        )
        return ConversationHandler.END

    sel_key = f"pubsel:{review_key}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    if not isinstance(selected, set):
        selected = set()
        context.user_data[sel_key] = selected

    kb = build_channel_picker_keyboard(review_key, channels, selected, page=1)
    await query.edit_message_text(
        "📢 اختر القنوات التي تريد النشر إليها ثم اضغط «🚀 نشر المحدد».",
        reply_markup=kb
    )


async def channel_picker_nav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, review_key, page_s = query.data.split(":")
    page = int(page_s)

    channels = _load_user_active_channels(query.from_user.id)
    sel_key = f"pubsel:{review_key}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    kb = build_channel_picker_keyboard(review_key, channels, selected, page=page)
    await query.edit_message_reply_markup(reply_markup=kb)


async def channel_picker_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, _, review_key, tg_id_s, page_s = query.data.split(":")
    tg_id = int(tg_id_s)
    page = int(page_s)

    sel_key = f"pubsel:{review_key}"
    selected: Set[int] = context.user_data.get(sel_key, set())
    if tg_id in selected:
        selected.remove(tg_id)
    else:
        selected.add(tg_id)
    context.user_data[sel_key] = selected

    channels = _load_user_active_channels(query.from_user.id)
    kb = build_channel_picker_keyboard(review_key, channels, selected, page=page)
    await query.edit_message_reply_markup(reply_markup=kb)


async def channel_picker_back_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_review_card(update, context, is_edit=True)


async def channel_picker_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جارٍ النشر للقنوات المحددة...")
    review_key = query.data.split(":")[2]

    draft = context.bot_data.get(review_key)
    sel_key = f"pubsel:{review_key}"
    selected: Set[int] = context.user_data.get(sel_key, set())

    if not draft:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec.")
        return ConversationHandler.END
    if not selected:
        await query.edit_message_text("⚠️ لم تختر أي قناة. اختر قناة واحدة على الأقل.")
        return

    trade_service = get_service(context, "trade_service")
    try:
        live_price = get_service(context, "price_service").get_cached_price(
            draft["asset"], draft.get("market", "Futures")
        )
        entry_val = draft["entry"]
        entry_price = entry_val[0] if isinstance(entry_val, list) else entry_val
        if isinstance(entry_val, list):
            draft.setdefault("notes", "")
            draft["notes"] += f"\nEntry Zone: {entry_val[0]}-{entry_val[-1]}"

        rec = trade_service.create_recommendation(
            asset=draft["asset"],
            side=draft["side"],
            market=draft.get("market", "Futures"),
            entry=entry_price,
            stop_loss=draft["stop_loss"],
            targets=draft["targets"],
            notes=draft.get("notes"),
            user_id=str(query.from_user.id),
            order_type=draft["order_type"],
            live_price=live_price,
        )

        trade_service.publish_existing(
            rec_id=rec.id,
            user_id=str(query.from_user.id),
            target_channel_ids=list(selected),
        )

        await query.edit_message_text(f"✅ تم الحفظ، وتمت محاولة النشر للقنوات المختارة للتوصية #{rec.id}.")
    except Exception as e:
        log.exception("Failed to save/publish to selected channels.")
        await query.edit_message_text(f"❌ فشل النشر للقنوات المحددة: {e}")
    finally:
        context.bot_data.pop(review_key, None)
        context.user_data.pop('current_review_key', None)
        context.user_data.pop(sel_key, None)
    return ConversationHandler.END


# =========================
# Method Selection / Quick / Editor
# =========================
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

    if choice == "interactive":
        return await start_interactive_builder(update, context)
    elif choice == "quick":
        await query.message.edit_text(
            "⚡️ وضع الأمر السريع.\n\nأرسل /rec الآن.",
            reply_markup=change_method_keyboard()
        )
        return QUICK_COMMAND
    elif choice == "editor":
        await query.message.edit_text(
            "📋 وضع المحرّر النصي.\n\nألصق التوصية.",
            reply_markup=change_method_keyboard()
        )
        return TEXT_EDITOR
    return ConversationHandler.END


async def quick_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_quick_command(update.message.text)
    if not data:
        await update.message.reply_text("❌ صيغة غير صحيحة. حاول مجدداً.")
        return QUICK_COMMAND
    context.user_data[CONVERSATION_DATA_KEY] = data
    return await show_review_card(update, context)


async def text_editor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = parse_text_editor(update.message.text)
    if not data:
        await update.message.reply_text("❌ تعذّر تحليل النص. تأكد من الحقول المطلوبة.")
        return TEXT_EDITOR
    if 'order_type' not in data or not data['order_type']:
        data['order_type'] = 'LIMIT'
    context.user_data[CONVERSATION_DATA_KEY] = data
    return await show_review_card(update, context)


# =========================
# Interactive Builder
# =========================
async def start_interactive_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.callback_query.message
    context.user_data[CONVERSATION_DATA_KEY] = {}
    trade_service = get_service(context, "trade_service")
    user_id = str(update.effective_user.id)
    recent_assets = trade_service.get_recent_assets_for_user(user_id, limit=5)

    sent_message = await message.reply_text(
        "🚀 Interactive Builder\n\n1️⃣ اختر أصلاً حديثاً أو اكتب الرمز مباشرة:",
        reply_markup=asset_choice_keyboard(recent_assets)
    )
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
    await query.message.edit_text(
        f"✅ Asset: {asset.upper()}\n\n2️⃣ اختر الاتجاه:",
        reply_markup=side_market_keyboard(market)
    )
    return I_SIDE_MARKET


async def asset_chosen_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    last_message_id = context.user_data.pop('last_interactive_message_id', None)
    if last_message_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_message_id)
        except Exception:
            pass

    raw = (update.message.text or "").strip()
    if raw.lower() in {"new", "جديد"}:
        sent = await update.message.reply_text("⚠️ هذا زر إضافة. من فضلك اكتب رمزًا حقيقيًا مثل: BTCUSDT")
        context.user_data['last_interactive_message_id'] = sent.message_id
        return I_ASSET_CHOICE

    asset = raw.upper()
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['asset'] = asset
    market = context.user_data.get('preferred_market', 'Futures')
    draft['market'] = market

    sent_message = await update.message.reply_text(
        f"✅ Asset: {asset}\n\n2️⃣ اختر الاتجاه:",
        reply_markup=side_market_keyboard(market)
    )
    context.user_data['last_interactive_message_id'] = sent_message.message_id
    return I_SIDE_MARKET


async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    side = query.data.split('_')[1]
    context.user_data[CONVERSATION_DATA_KEY]['side'] = side
    asset = context.user_data[CONVERSATION_DATA_KEY]['asset']
    await query.message.edit_text(
        f"✅ Asset: {asset} ({side})\n\n3️⃣ اختر نوع أمر الدخول:",
        reply_markup=order_type_keyboard()
    )
    return I_ORDER_TYPE


async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    order_type = query.data.split('_')[1]
    draft = context.user_data[CONVERSATION_DATA_KEY]
    draft['order_type'] = order_type
    if order_type == 'MARKET':
        await query.message.edit_text("✅ Order Type: Market\n\n4️⃣ أرسل: `STOP TARGETS...`")
    else:
        await query.message.edit_text(f"✅ Order Type: {order_type}\n\n4️⃣ أرسل: `ENTRY STOP TARGETS...`")
    return I_PRICES


def _parse_price_string(price_str: str) -> float:
    s = price_str.strip().lower()
    if 'k' in s:
        return float(s.replace('k', '')) * 1000
    return float(s)


async def prices_received_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        draft = context.user_data[CONVERSATION_DATA_KEY]
        order_type = draft.get('order_type')
        parts = update.message.text.strip().replace(',', ' ').split()

        if order_type == 'MARKET':
            if len(parts) < 2:
                raise ValueError("At least Stop Loss and one Target are required.")
            draft["entry"] = 0
            draft["stop_loss"] = _parse_price_string(parts[0])
            draft["targets"] = [_parse_price_string(t) for t in parts[1:]]
        else:
            if len(parts) < 3:
                raise ValueError("Entry, Stop, and at least one Target are required.")
            draft["entry"] = _parse_price_string(parts[0])
            draft["stop_loss"] = _parse_price_string(parts[1])
            draft["targets"] = [_parse_price_string(t) for t in parts[2:]