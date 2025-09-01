# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from .helpers import get_service
from .keyboards import confirm_recommendation_keyboard
from .ui_texts import build_review_text, build_review_text_with_price
from capitalguard.application.services.price_service import PriceService

# --- Conversation States ---
# نستخدم أرقامًا لتمثيل مراحل المحادثة
(ASSET, SIDE, MARKET, ENTRY, STOP_LOSS, TARGETS, NOTES, REVIEW) = range(8)
# مفتاح فريد لتخزين بيانات المحادثة المؤقتة
CONVERSATION_DATA_KEY = "new_rec_draft"

# --- Conversation Steps ---

async def newrec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يبدأ محادثة إنشاء توصية جديدة."""
    context.user_data[CONVERSATION_DATA_KEY] = {}
    await update.message.reply_text("لنبدأ توصية جديدة. من فضلك أرسل رمز الأصل (مثال: BTCUSDT).")
    return ASSET

async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن الأصل وينتقِل للسؤال عن الاتجاه."""
    asset = update.message.text.strip().upper()
    context.user_data[CONVERSATION_DATA_KEY]["asset"] = asset
    await update.message.reply_text("ممتاز. الآن أرسل الاتجاه: LONG أو SHORT.")
    return SIDE

async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن الاتجاه وينتقِل للسؤال عن السوق."""
    side = update.message.text.strip().upper()
    if side not in ["LONG", "SHORT"]:
        await update.message.reply_text("غير صالح. الرجاء إرسال LONG أو SHORT.")
        return SIDE
    context.user_data[CONVERSATION_DATA_KEY]["side"] = side
    await update.message.reply_text("تمام. الآن أرسل نوع السوق (مثال: Futures أو Spot).")
    return MARKET

async def received_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن السوق وينتقِل للسؤال عن سعر الدخول."""
    context.user_data[CONVERSATION_DATA_KEY]["market"] = update.message.text.strip()
    await update.message.reply_text("جيد. الآن أرسل سعر الدخول.")
    return ENTRY

async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن سعر الدخول وينتقِل لوقف الخسارة."""
    try:
        context.user_data[CONVERSATION_DATA_KEY]["entry"] = float(update.message.text.strip())
        await update.message.reply_text("أرسل الآن سعر وقف الخسارة (Stop Loss).")
        return STOP_LOSS
    except ValueError:
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return ENTRY

async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن وقف الخسارة وينتقِل للأهداف."""
    try:
        context.user_data[CONVERSATION_DATA_KEY]["stop_loss"] = float(update.message.text.strip())
        await update.message.reply_text("رائع. الآن أرسل الأهداف (يمكنك إرسال عدة أهداف مفصولة بمسافة أو فاصلة).")
        return TARGETS
    except ValueError:
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return STOP_LOSS

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن الأهداف وينتقِل للملاحظات."""
    targets_str = update.message.text.strip()
    try:
        targets = [float(t) for t in targets_str.replace(",", " ").split()]
        if not targets: raise ValueError("No targets provided")
        context.user_data[CONVERSATION_DATA_KEY]["targets"] = targets
        await update.message.reply_text("أخيرًا، أرسل أي ملاحظات. أو أرسل 'لا يوجد' لتخطي.")
        return NOTES
    except ValueError:
        await update.message.reply_text("أهداف غير صالحة. الرجاء إرسال أرقام مفصولة بمسافة.")
        return TARGETS

async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يخزن الملاحظات ويعرض بطاقة المراجعة النهائية."""
    notes = update.message.text.strip()
    context.user_data[CONVERSATION_DATA_KEY]["notes"] = notes if notes.lower() not in ["-", "لا يوجد", "none"] else None
    
    draft = context.user_data[CONVERSATION_DATA_KEY]
    price_service: PriceService = get_service(context, "price_service")
    preview_price = price_service.get_preview_price(draft["asset"], draft["market"])
    
    review_text = build_review_text_with_price(draft, preview_price)
    
    # ننشئ مفتاحًا فريدًا لهذه المراجعة لتجنب التضارب
    review_key = str(uuid.uuid4())
    context.bot_data[review_key] = draft.copy()
    
    await update.message.reply_html(
        review_text,
        reply_markup=confirm_recommendation_keyboard(review_key)
    )
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    return ConversationHandler.END

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يتم استدعاؤها عند الضغط على زر 'نشر'. تنشر التوصية في القناة."""
    query = update.callback_query
    await query.answer("جاري النشر...")
    
    review_key = query.data.split(":")[2]
    draft = context.bot_data.get(review_key)
    
    if not draft:
        await query.edit_message_text("خطأ: لم يتم العثور على بيانات المراجعة. ربما انتهت صلاحيتها.")
        return

    trade_service = get_service(context, "trade_service")
    try:
        rec = trade_service.create_and_publish_recommendation(
            asset=draft["asset"],
            side=draft["side"],
            market=draft["market"],
            entry=draft["entry"],
            stop_loss=draft["stop_loss"],
            targets=draft["targets"],
            notes=draft["notes"],
            user_id=str(query.from_user.id)
        )
        await query.edit_message_text(f"✅ تم نشر التوصية بنجاح! #{rec.id}")
    except Exception as e:
        logging.exception("Failed to publish recommendation from conversation.")
        await query.edit_message_text(f"❌ فشل النشر: {e}")
    finally:
        context.bot_data.pop(review_key, None)

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يلغي المحادثة الحالية."""
    context.user_data.pop(CONVERSATION_DATA_KEY, None)
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def cancel_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يلغي النشر بعد المراجعة."""
    query = update.callback_query
    await query.answer()
    review_key = query.data.split(":")[2]
    context.bot_data.pop(review_key, None)
    await query.edit_message_text("تم إلغاء النشر.")

# --- The Handler Itself ---

def get_recommendation_conversation_handler(allowed_filter: filters.BaseFilter) -> ConversationHandler:
    """
    ينشئ ويعيد كائن ConversationHandler الكامل.
    """
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_cmd, filters=allowed_filter)],
        states={
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            MARKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_market)],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=False
    )

def register_conversation_handlers(app: Application):
    from .auth import ALLOWED_FILTER
    app.add_handler(get_recommendation_conversation_handler(ALLOWED_FILTER))
    app.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    app.add_handler(CallbackQueryHandler(cancel_publish, pattern=r"^rec:cancel:"))
# --- END OF FILE ---