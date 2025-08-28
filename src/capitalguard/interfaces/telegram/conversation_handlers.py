#--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
import uuid
import logging
from telegram import Update, ReplyKeyboardRemove
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

# States for the conversation
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS = range(5)

def _format_recap(data: dict) -> str:
    """Formats a summary of the recommendation for confirmation."""
    targets_str = ", ".join(map(str, data.get("targets", [])))
    return (
        f"📝 *مراجعة التوصية*\n\n"
        f"🔹 *الأصل:* `{data.get('asset', 'N/A')}`\n"
        f"🔸 *الاتجاه:* `{data.get('side', 'N/A')}`\n"
        f"📈 *سعر الدخول:* `{data.get('entry', 'N/A')}`\n"
        f"📉 *وقف الخسارة:* `{data.get('stop_loss', 'N/A')}`\n"
        f"🎯 *الأهداف:* `{targets_str}`\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )

async def start_new_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation to create a new recommendation."""
    context.user_data['recommendation'] = {}
    await update.message.reply_text("لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)")
    return ASSET

async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the asset and asks for the side."""
    context.user_data['recommendation']['asset'] = update.message.text.upper().strip()
    await update.message.reply_text("ممتاز. الآن، ما هو الاتجاه؟ (LONG أو SHORT)")
    return SIDE

async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the side and asks for the entry price."""
    side = update.message.text.upper().strip()
    if side not in ["LONG", "SHORT"]:
        await update.message.reply_text("اتجاه غير صالح. الرجاء إدخال LONG أو SHORT.")
        return SIDE
    context.user_data['recommendation']['side'] = side
    await update.message.reply_text("رائع. ما هو سعر الدخول؟")
    return ENTRY

async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the entry price and asks for the stop loss."""
    try:
        context.user_data['recommendation']['entry'] = float(update.message.text.strip())
        await update.message.reply_text("تمام. ما هو سعر وقف الخسارة؟")
        return STOP_LOSS
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return ENTRY

async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the stop loss and asks for targets."""
    try:
        context.user_data['recommendation']['stop_loss'] = float(update.message.text.strip())
        await update.message.reply_text("أخيرًا، أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000).")
        return TARGETS
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return STOP_LOSS

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores targets, shows a recap, and asks for confirmation."""
    try:
        targets_str = update.message.text.strip().replace(',', ' ').split()
        targets = [float(t) for t in targets_str if t]
        context.user_data['recommendation']['targets'] = targets
        
        user_data_key = str(uuid.uuid4())
        context.bot_data[user_data_key] = context.user_data.pop('recommendation', {})
        
        recap_text = _format_recap(context.bot_data[user_data_key])
        await update.message.reply_markdown_v2(
            recap_text.replace('.', r'\.'), # Escape dots for MarkdownV2
            reply_markup=confirm_recommendation_keyboard(user_data_key)
        )
        return ConversationHandler.END
    except (ValueError, TypeError):
        await update.message.reply_text("الأهداف غير صالحة. الرجاء إدخال قائمة أرقام صحيحة.")
        return TARGETS

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data_key = query.data.split(':')[2]
    rec_data = context.bot_data.get(user_data_key)
    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة.")
        return

    trade_service = context.application.bot_data.get("trade_service")
    if not isinstance(trade_service, TradeService):
        await query.edit_message_text("❌ خطأ داخلي: خدمة التداول غير مهيأة.")
        return

    try:
        new_rec = trade_service.create(
            asset=rec_data['asset'],
            side=rec_data['side'],
            entry=rec_data['entry'],
            stop_loss=rec_data['stop_loss'],
            targets=rec_data['targets'],
            user_id=str(query.from_user.id)
        )
        
        signal_text = format_signal(
            rec_id=new_rec.id, symbol=new_rec.asset.value, side=new_rec.side.value,
            entry=new_rec.entry.value, sl=new_rec.stop_loss.value, targets=new_rec.targets.values
        )

        await context.bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=signal_text, parse_mode="HTML")
        await query.edit_message_text(f"✅ تم نشر التوصية #{new_rec.id} في القناة بنجاح!")

    except Exception as e:
        logging.exception("Failed to publish recommendation")
        await query.edit_message_text(f"❌ فشل في النشر: {e}")
    finally:
        if user_data_key in context.bot_data:
            del context.bot_data[user_data_key]

async def cancel_publication(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_data_key = query.data.split(':')[2]
    if user_data_key in context.bot_data:
        del context.bot_data[user_data_key]
    await query.edit_message_text("تم إلغاء النشر.")


# ✅ الدالة المفقودة التي تسببت في الخطأ
def get_recommendation_conversation_handler() -> ConversationHandler:
    """
    Builds the ConversationHandler for creating recommendations.
    """
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", start_new_recommendation, filters=filters.ChatType.PRIVATE & ALLOWED_FILTER)],
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