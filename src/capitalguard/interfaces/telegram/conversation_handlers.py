#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from typing import Dict, Any, List

from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.formatting.telegram_templates import format_signal
from .keyboards import confirm_recommendation_keyboard

# مراحل المحادثة
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS = range(5)

def _format_recap(data: Dict[str, Any]) -> str:
    targets_str = ", ".join(f"{t:g}" for t in data.get("targets", []))
    return (
        "📝 *مراجعة التوصية*\n\n"
        f"🔹 *الأصل:* `{data.get('asset', 'N/A')}`\n"
        f"🔸 *الاتجاه:* `{data.get('side', 'N/A')}`\n"
        f"📈 *سعر الدخول:* `{data.get('entry', 'N/A')}`\n"
        f"📉 *وقف الخسارة:* `{data.get('stop_loss', 'N/A')}`\n"
        f"🎯 *الأهداف:* `{targets_str}`\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )

async def start_new_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ✅ تنظيف أي حالة قديمة محفوظة عبر PicklePersistence
    context.user_data.clear()
    context.user_data["recommendation"] = {}
    await update.message.reply_text(
        "لنبدأ بإنشاء توصية جديدة.\nما هو *رمز الأصل*؟ (مثال: BTCUSDT)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASSET

async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["recommendation"]["asset"] = (update.message.text or "").strip().upper()
    await update.message.reply_text(
        "ممتاز. الآن ما هو *الاتجاه*؟ أرسل `LONG` أو `SHORT`.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return SIDE

async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    side = (update.message.text or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        await update.message.reply_text("اتجاه غير صالح. الرجاء إدخال LONG أو SHORT.")
        return SIDE
    context.user_data["recommendation"]["side"] = side
    await update.message.reply_text("رائع. ما هو *سعر الدخول*؟", parse_mode=ParseMode.MARKDOWN)
    return ENTRY

async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        entry_val = float((update.message.text or "").strip())
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return ENTRY
    context.user_data["recommendation"]["entry"] = entry_val
    await update.message.reply_text("تمام. ما هو *سعر وقف الخسارة*؟", parse_mode=ParseMode.MARKDOWN)
    return STOP_LOSS

async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        sl_val = float((update.message.text or "").strip())
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return STOP_LOSS
    context.user_data["recommendation"]["stop_loss"] = sl_val
    await update.message.reply_text(
        "أخيرًا، أرسل *الأهداف* مفصولة بمسافة أو فاصلة (مثال: `68000 70000` أو `68000,70000`).",
        parse_mode=ParseMode.MARKDOWN,
    )
    return TARGETS

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        text = (update.message.text or "").replace(",", " ").strip()
        parts = [p for p in text.split() if p]
        targets: List[float] = [float(p) for p in parts]
        if not targets:
            raise ValueError("No targets")
    except (ValueError, TypeError):
        await update.message.reply_text("الأهداف غير صالحة. الرجاء إدخال قائمة أرقام صحيحة.")
        return TARGETS

    context.user_data["recommendation"]["targets"] = targets

    # تخزين نسخة مؤقتة في bot_data لاستخدامها عند الضغط على الأزرار
    user_data_key = str(uuid.uuid4())
    context.bot_data[user_data_key] = dict(context.user_data["recommendation"])

    recap_text = _format_recap(context.user_data["recommendation"])
    await update.message.reply_text(
        recap_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_recommendation_keyboard(user_data_key),
    )
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- أزرار النشر/الإلغاء ---
async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    user_data_key = parts[2] if len(parts) >= 3 else None
    rec_data = context.bot_data.get(user_data_key) if user_data_key else None

    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة أو حدث خطأ.")
        return

    # ✅ الخدمة تؤخذ من المفتاح الخاص بالمحادثات
    trade_service = context.application.bot_data.get("trade_service_conv")
    if not isinstance(trade_service, TradeService):
        await query.edit_message_text("❌ خطأ داخلي: خدمة التداول غير مهيأة.")
        logging.error("TradeService not found in bot_data for conversation.")
        return

    try:
        new_rec = trade_service.create(
            asset=rec_data["asset"],
            side=rec_data["side"],
            entry=rec_data["entry"],
            stop_loss=rec_data["stop_loss"],
            targets=rec_data["targets"],
            user_id=str(query.from_user.id),
        )
        await query.edit_message_text(f"✅ تم إنشاء التوصية #{new_rec.id} ونشرها بنجاح.")
    except Exception as e:
        logging.exception("Failed to publish recommendation")
        await query.edit_message_text(f"❌ فشل في إنشاء أو نشر التوصية: {e}")
    finally:
        if user_data_key and user_data_key in context.bot_data:
            del context.bot_data[user_data_key]
        context.user_data.clear()

async def cancel_publication(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    user_data_key = parts[2] if len(parts) >= 3 else None
    if user_data_key and user_data_key in context.bot_data:
        del context.bot_data[user_data_key]
    await query.edit_message_text("تم إلغاء النشر.")

def get_recommendation_conversation_handler(allowed_filter) -> ConversationHandler:
    """
    نقيّد الدخول بالمستخدمين المصرّح لهم + دردشة خاصة (اختياريًا حسب حاجتك).
    """
    return ConversationHandler(
        entry_points=[CommandHandler(
            "newrec",
            start_new_recommendation,
            filters=filters.ChatType.PRIVATE & allowed_filter,
        )],
        states={
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        persistent=True,
        name="new_recommendation_conversation",
    )
#--- END OF FILE ---