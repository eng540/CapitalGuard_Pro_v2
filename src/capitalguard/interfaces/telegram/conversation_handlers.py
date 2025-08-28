# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
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

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.formatting.telegram_templates import format_signal
from .keyboards import confirm_recommendation_keyboard

# ✅ واردات الحقن الاحتياطي
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

# Conversation states
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS = range(5)


def _get_trade_service(context: ContextTypes.DEFAULT_TYPE) -> TradeService | None:
    """
    محاولة جلب الخدمة من bot_data.
    قد تعود None إذا لم تكن محقونة بعد.
    """
    try:
        svc = context.application.bot_data.get("trade_service") or context.bot_data.get("trade_service")
        if isinstance(svc, TradeService):
            return svc
    except Exception:
        pass
    return None


def _ensure_trade_service(context: ContextTypes.DEFAULT_TYPE) -> TradeService:
    """
    ✅ حقن احتياطي (self-healing) في حال الخدمة غير موجودة.
    ينشئ TradeService ويحقنه داخل bot_data ثم يعيده.
    """
    svc = _get_trade_service(context)
    if svc:
        return svc

    logging.warning("[Conversation] trade_service not found in bot_data — performing lazy injection.")
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    svc = TradeService(repo, notifier)

    # نحقنه في application.bot_data ليبقى متاحًا لباقي المعالجات
    context.application.bot_data["trade_service"] = svc
    return svc


def _format_recap(data: Dict[str, Any]) -> str:
    """Formats a summary of the recommendation for confirmation (Markdown)."""
    targets_str = ", ".join(map(lambda x: f"{x:g}", data.get("targets", [])))
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
    """Start the interactive flow."""
    context.user_data["recommendation"] = {}
    await update.message.reply_text(
        "لنبدأ بإنشاء توصية جديدة.\nما هو *رمز الأصل*؟ (مثال: BTCUSDT)",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASSET


async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture asset and ask for side."""
    context.user_data["recommendation"]["asset"] = (update.message.text or "").strip().upper()
    await update.message.reply_text(
        "ممتاز. الآن ما هو *الاتجاه*؟ أرسل `LONG` أو `SHORT`.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return SIDE


async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture side and ask for entry price."""
    side = (update.message.text or "").strip().upper()
    if side not in {"LONG", "SHORT"}:
        await update.message.reply_text("اتجاه غير صالح. الرجاء إدخال LONG أو SHORT.")
        return SIDE
    context.user_data["recommendation"]["side"] = side
    await update.message.reply_text("رائع. ما هو *سعر الدخول*؟", parse_mode=ParseMode.MARKDOWN)
    return ENTRY


async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture entry, ask for stop loss."""
    try:
        entry_val = float((update.message.text or "").strip())
        context.user_data["recommendation"]["entry"] = entry_val
        await update.message.reply_text("تمام. ما هو *سعر وقف الخسارة*؟", parse_mode=ParseMode.MARKDOWN)
        return STOP_LOSS
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return ENTRY


async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture stop loss, ask for targets."""
    try:
        sl_val = float((update.message.text or "").strip())
        context.user_data["recommendation"]["stop_loss"] = sl_val
        await update.message.reply_text(
            "أخيرًا، أرسل *الأهداف* مفصولة بمسافة أو فاصلة (مثال: `68000 70000` أو `68000,70000`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return TARGETS
    except (ValueError, TypeError):
        await update.message.reply_text("سعر غير صالح. الرجاء إدخال رقم.")
        return STOP_LOSS


async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture targets, show recap with confirm/cancel buttons."""
    try:
        text = (update.message.text or "").replace(",", " ").strip()
        parts = [p for p in text.split() if p]
        targets: List[float] = [float(p) for p in parts]
        if not targets:
            raise ValueError("No targets")

        context.user_data["recommendation"]["targets"] = targets

        # Store a copy in bot_data keyed by unique id, to be used by callback buttons
        user_data_key = str(uuid.uuid4())
        context.bot_data[user_data_key] = dict(context.user_data["recommendation"])

        recap_text = _format_recap(context.user_data["recommendation"])
        await update.message.reply_text(
            recap_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=confirm_recommendation_keyboard(user_data_key),
        )
        # End the conversation; next steps will be via inline buttons
        return ConversationHandler.END

    except (ValueError, TypeError):
        await update.message.reply_text("الأهداف غير صالحة. الرجاء إدخال قائمة أرقام صحيحة.")
        return TARGETS


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the flow."""
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --- Inline button callbacks (used by webhook_handlers registration) ---

async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for 'Publish' button."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    # pattern: rec:publish:<uuid>
    user_data_key = parts[2] if len(parts) >= 3 else None
    rec_data = context.bot_data.get(user_data_key) if user_data_key else None

    if not rec_data:
        await query.edit_message_text("انتهت صلاحية هذه الجلسة أو حدث خطأ.")
        return

    # ✅ نضمن توفر الخدمة حتى لو لم تُحقن من main.py
    trade_service = _ensure_trade_service(context)

    try:
        new_rec = trade_service.create(
            asset=rec_data["asset"],
            side=rec_data["side"],
            entry=rec_data["entry"],
            stop_loss=rec_data["stop_loss"],
            targets=rec_data["targets"],
            user_id=str(query.from_user.id),
        )

        # Format the signal as HTML for channel
        signal_text = format_signal(
            rec_id=new_rec.id,
            symbol=new_rec.asset.value,
            side=new_rec.side.value,
            entry=new_rec.entry.value,
            sl=new_rec.stop_loss.value,
            targets=new_rec.targets.values,
        )

        channel_id = settings.TELEGRAM_CHAT_ID
        if channel_id:
            await context.bot.send_message(chat_id=channel_id, text=signal_text, parse_mode=ParseMode.HTML)
            await query.edit_message_text(f"✅ تم نشر التوصية #{new_rec.id} في القناة بنجاح!")
        else:
            await query.edit_message_text(f"✅ تم إنشاء التوصية #{new_rec.id}، لكن لم يتم تحديد قناة للنشر.")

    except Exception as e:
        logging.exception("Failed to create or publish recommendation")
        await query.edit_message_text(f"❌ فشل في إنشاء أو نشر التوصية: {e}")

    finally:
        # Cleanup
        if user_data_key and user_data_key in context.bot_data:
            del context.bot_data[user_data_key]
        context.user_data.clear()


async def cancel_publication(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for the 'Cancel' button."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    # pattern: rec:cancel:<uuid>
    user_data_key = parts[2] if len(parts) >= 3 else None
    if user_data_key and user_data_key in context.bot_data:
        del context.bot_data[user_data_key]

    await query.edit_message_text("تم إلغاء النشر.")


def get_recommendation_conversation_handler() -> ConversationHandler:
    """Build the ConversationHandler for creating recommendations."""
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", start_new_recommendation, filters=filters.ChatType.PRIVATE)],
        states={
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        # الاستمرارية مفعّلة في main.py عبر PicklePersistence
        persistent=True,
        name="new_recommendation_conversation",
    )
# --- END OF FILE ---