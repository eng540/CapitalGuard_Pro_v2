# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from capitalguard.application.services.trade_service import TradeService
from capitalguard.config import settings
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard

AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"  # user_data key: int rec_id

def _get_trade_service(context: ContextTypes.DEFAULT_TYPE) -> TradeService:
    return context.application.bot_data["trade_service"]

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service = _get_trade_service(context)
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return

    for it in items:
        text = (
            f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value})\n"
            f"Entry: <code>{it.entry.value}</code> | SL: <code>{it.stop_loss.value}</code>\n"
            f"TPs: <code>{', '.join(map(lambda x: str(x), it.targets.values))}</code>"
        )
        await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(it.id))

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # pattern: rec:close:<rec_id>
    if len(parts) != 3:
        await query.edit_message_text("تنسيق غير صحيح.")
        return

    rec_id = int(parts[2])
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    await query.edit_message_text(
        f"🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية <b>#{rec_id}</b>.",
        parse_mode=ParseMode.HTML,
    )

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return

    try:
        exit_price = float((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text("⚠️ سعر غير صالح. الرجاء إدخال رقم صحيح.")
        return

    rec_id = int(context.user_data[AWAITING_CLOSE_PRICE_KEY])
    await update.message.reply_html(
        f"هل تريد تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <code>{exit_price}</code>؟",
        reply_markup=confirm_close_keyboard(rec_id, exit_price),
    )

async def confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # pattern: rec:confirm_close:<rec_id>:<exit_price>
    if len(parts) != 4:
        await query.edit_message_text("تنسيق تأكيد غير صحيح.")
        return

    rec_id = int(parts[2])
    try:
        exit_price = float(parts[3])
    except ValueError:
        await query.edit_message_text("سعر غير صالح في التأكيد.")
        return

    try:
        trade_service = _get_trade_service(context)
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
    finally:
        if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # pattern: rec:cancel_close:<rec_id>
    if len(parts) != 3:
        await query.edit_message_text("تم الإلغاء.")
        return

    rec_id = int(parts[2])
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

    await query.edit_message_text("تم التراجع عن الإغلاق.")

def register_management_handlers(application: Application):
    application.add_handler(CommandHandler("open", open_cmd))
    application.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_close,   pattern=r"^rec:confirm_close:\d+:[0-9.]+$"))
    application.add_handler(CallbackQueryHandler(cancel_close,    pattern=r"^rec:cancel_close:\d+$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price))
# --- END OF FILE ---