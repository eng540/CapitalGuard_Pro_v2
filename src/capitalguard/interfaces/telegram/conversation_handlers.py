# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import logging
import uuid
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    review_final_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard
)
from .commands import (
    main_creation_keyboard, change_method_keyboard,
    newrec_entry_point, settings_cmd
)
from .parsers import parse_quick_command, parse_text_editor
from .auth import ALLOWED_USER_FILTER

log = logging.getLogger(__name__)

# --- State Definitions & Keys ---
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"
CONVERSATION_DATA_KEY = "new_rec_draft"


# --- Review Card ---
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
        # إن وُجدت رسالة أصلية قابلة للتعديل، نحاول تعديلها؛ وإلا نرسل رسالة جديدة.
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


# --- Publish / Cancel ---
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    زر «💾 حفظ + نشر»:
    - يحفظ التوصية للمستخدم الحالي.
    - يحاول النشر فقط إلى قنوات المستخدم المرتبطة والفعّالة (بدون أي قناة افتراضية).
    - في حال عدم وجود قنوات، تُرسل رسالة خاصة للمستخدم من طبقة الخدمة.
    """
    query = update.callback_query
    await query.answer("جارٍ الحفظ ثم النشر...")
    review_key = query.data.split(":")[2]
    draft = context.bot_data.get(review_key)
    if not draft:
        await query.edit_message_text("❌ انتهت صلاحية البطاقة. أعد البدء بـ /newrec.")
        return ConversationHandler.END

    trade_service = get_service(context, "trade_service")
    try:
        # احصل على السعر الحي في حال ORDER_TYPE=MARKET (الخدمة ستتحقق وتفشل إن غاب)
        live_price = get_service(context, "price_service").get_cached_price(
            draft["asset"], draft.get("market", "Futures")
        )

        # دعم مناطق الدخول: نخزنها كملاحظة فقط، ونعتمد أول قيمة كـ Entry
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
            user_id=str(query.from_user.id),   # ✅ مقيّد بالمستخدم
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


# --- Method Selection / Quick / Editor ---
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


# --- Interactive Builder ---
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
    """التعامل مع أزرار اختيار الأصل، بما فيها زر الإضافة asset_new."""
    query = update.callback_query
    await query.answer()
    asset = query.data.split('_', 1)[1]  # مثال: BTCUSDT أو new

    # ✅ أي قيمة 'new' تعني "أدخل الرمز يدويًا" وليست أصلًا حقيقيًا
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
    """التعامل مع إدخال الرمز كتابةً، مع حماية من NEW/جديد."""
    last_message_id = context.user_data.pop('last_interactive_message_id', None)
    if last_message_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=last_message_id)
        except Exception:
            pass

    raw = (update.message.text or "").strip()

    # ✅ حماية: لا نسمح باعتبار NEW/جديد كرمز
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
    review_key = query.data.split(':')[2]
    context.user_data['current_review_key'] = review_key
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
        try:
            await update.message.delete()
        except Exception:
            pass
        # أعد عرض البطاقة على نفس الرسالة الأصلية
        dummy_update = Update(
            update.update_id,
            callback_query=type('obj', (object,), {'message': original_message, 'data': ''})
        )
        return await show_review_card(dummy_update, context, is_edit=True)
    await update.message.reply_text("حدث خلل. ابدأ من جديد بـ /newrec.")
    return ConversationHandler.END


# --- Registration Function ---
def register_conversation_handlers(app: Application):
    creation_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entry_point, filters=ALLOWED_USER_FILTER),
            CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER),
        ],
        states={
            CHOOSE_METHOD: [
                CallbackQueryHandler(method_chosen, pattern="^method_"),
                CallbackQueryHandler(change_method_handler, pattern="^change_method$"),
                # السماح ببدء المنشئ فوراً إذا كتب المستخدم الأصل مباشرة بعد /newrec
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen_text),
            ],
            QUICK_COMMAND: [
                MessageHandler(filters.COMMAND & filters.Regex(r'^\/rec'), quick_command_handler)
            ],
            TEXT_EDITOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, text_editor_handler)
            ],
            I_ASSET_CHOICE: [
                CallbackQueryHandler(asset_chosen_button, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen_text),
            ],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(change_market_menu, pattern="^change_market_menu$"),
                CallbackQueryHandler(market_chosen, pattern="^market_"),
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
                CallbackQueryHandler(cancel_publish_handler, pattern=r"^rec:cancel:")
            ],
            I_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv_handler)],
        per_message=False,
        allow_reentry=True,
    )
    app.add_handler(creation_conv_handler)
# --- END OF FILE ---