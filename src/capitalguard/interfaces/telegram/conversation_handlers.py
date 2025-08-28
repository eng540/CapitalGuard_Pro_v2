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
from .keyboards import confirm_recommendation_keyboard

# ======================
# Conversation States
# ======================
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS = range(5)


# ======================
# Helpers
# ======================
def _format_recap(data: Dict[str, Any]) -> str:
    targets = data.get("targets", [])
    # تنسيق الأهداف بدون أقواس وبفواصل مناسبة
    targets_str = ", ".join(f"{t:g}" for t in targets) if targets else "N/A"
    return (
        "📝 *مراجعة التوصية*\n\n"
        f"🔹 *الأصل:* `{data.get('asset', 'N/A')}`\n"
        f"🔸 *الاتجاه:* `{data.get('side', 'N/A')}`\n"
        f"📈 *سعر الدخول:* `{data.get('entry', 'N/A')}`\n"
        f"📉 *وقف الخسارة:* `{data.get('stop_loss', 'N/A')}`\n"
        f"🎯 *الأهداف:* `{targets_str}`\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )


def _svc(context: ContextTypes.DEFAULT_TYPE, name: str):
    """الوصول الآمن إلى الخدمات من bot_data داخل المحادثات/Callbacks."""
    svc = context.application.bot_data.get(name)
    if not svc:
        raise RuntimeError(f"Service '{name}' not available in bot_data")
    return svc


# ======================
# Conversation Flow
# ======================
async def start_new_recommendation(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    # تنظيف أي حالة قديمة محفوظة
    context.user_data.clear()
    context.user_data["recommendation"] = {}
    await update.message.reply_text(
        "لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASSET


async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["recommendation"]["asset"] = (update.message.text or "").upper().strip()
    await update.message.reply_text("ممتاز. الآن، ما هو الاتجاه؟ (LONG أو SHORT)")
    return SIDE


async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    side = (update.message.text or "").upper().strip()
    if side not in {"LONG", "SHORT"}:
        await update.message.reply_text("اتجاه غير صالح. الرجاء إدخال LONG أو SHORT.")
        return SIDE
    context.user_data["recommendation"]["side"] = side
    await update.message.reply_text("رائع. ما هو سعر الدخول؟")
    return ENTRY


async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["recommendation"]["entry"] = float((update.message.text or "").strip())
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return ENTRY
    await update.message.reply_text("تمام. ما هو سعر وقف الخسارة؟")
    return STOP_LOSS


async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["recommendation"]["stop_loss"] = float((update.message.text or "").strip())
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return STOP_LOSS
    await update.message.reply_text("أخيرًا، أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000).")
    return TARGETS


async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        targets = [float(t) for t in (update.message.text or "").replace(",", " ").split() if t]
        if not targets:
            raise ValueError
        context.user_data["recommendation"]["targets"] = targets
    except (ValueError, TypeError):
        await update.message.reply_text("الأهداف غير صالحة. الرجاء إدخال قائمة أرقام صحيحة.")
        return TARGETS

    # نخزّن نسخة مؤقتة للزرّين باستخدام مفتاح فريد
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


# ======================
# Publish / Cancel Callbacks
# ======================
async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    user_data_key = parts[2] if len(parts) >= 3 else None
    rec_data = context.bot_data.get(user_data_key) if user_data_key else None

    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة.")
        return

    try:
        trade_service = _svc(context, "trade_service")
        if not isinstance(trade_service, TradeService):
            raise RuntimeError("TradeService missing or invalid type")

        new_rec = trade_service.create(
            asset=rec_data["asset"],
            side=rec_data["side"],
            entry=rec_data["entry"],
            stop_loss=rec_data["stop_loss"],
            targets=rec_data["targets"],
            user_id=str(query.from_user.id),
        )
        # لا نرسل للقناة هنا — TradeService/TelegramNotifier يتكفّل بذلك داخليًا
        await query.edit_message_text(f"✅ تم إنشاء التوصية #{new_rec.id} ونشرها بنجاح!")
    except Exception as e:
        logging.exception("Failed to publish recommendation")
        await query.edit_message_text(f"❌ فشل في النشر: {e}")
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


# ======================
# Registration Helpers
# ======================
def get_recommendation_conversation_handler(allowed_filter) -> ConversationHandler:
    """
    يُرجع ConversationHandler لتدفق /newrec.
    يجب تسجيل الـ CallbackQueryHandlers (publish/cancel) أيضًا — انظر register_conversation_handlers أدناه.
    """
    return ConversationHandler(
        entry_points=[
            CommandHandler("newrec", start_new_recommendation, filters=filters.ChatType.PRIVATE & allowed_filter)
        ],
        states={
            ASSET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            ENTRY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        persistent=True,
        name="new_recommendation_conversation",
    )


def register_conversation_handlers(app, allowed_filter=None):
    """
    مساعد لتسجيل محادثة /newrec وأزرار النشر/الإلغاء على نفس Application.
    استعمله إن رغبت بالتسجيل من هذا الملف مباشرة.
    """
    conv = get_recommendation_conversation_handler(allowed_filter or filters.ALL)
    app.add_handler(conv)

    # تسجيل أزرار التأكيد/الإلغاء الخاصة بمراجعة التوصية
    app.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    app.add_handler(CallbackQueryHandler(cancel_publication, pattern=r"^rec:cancel:"))
#--- END OF FILE ---