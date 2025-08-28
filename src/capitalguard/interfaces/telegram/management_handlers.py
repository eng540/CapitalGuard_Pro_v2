# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from typing import Optional
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard
from .auth import ALLOWED_FILTER  # ✅ استيراد الفلتر من الملف الجديد

AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

def _svc(context: ContextTypes.DEFAULT_TYPE, name: str):
    svc = context.application.bot_data.get(name)
    if not svc:
        raise RuntimeError(f"Service '{name}' not initialized in bot_data")
    return svc

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return

    for it in items:
        entry_val = getattr(it.entry, "value", it.entry)
        sl_val    = getattr(it.stop_loss, "value", it.stop_loss)
        targets   = getattr(it.targets, "values", it.targets)
        tps = ", ".join(map(str, targets))
        text = (
            f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value})\n"
            f"Entry: <code>{entry_val}</code> | SL: <code>{sl_val}</code>\n"
            f"TPs: <code>{tps}</code>"
        )
        await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(it.id))

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.edit_message_text("تنسيق غير صحيح.")
        return
    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("تعذّر قراءة رقم التوصية.")
        return
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    await query.edit_message_text(
        f"🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية <b>#{rec_id}</b>.",
        parse_mode=ParseMode.HTML,
    )

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return
    txt = (update.message.text or "").strip()
    try:
        exit_price = float(txt)
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
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await query.edit_message_text("تنسيق تأكيد غير صحيح.")
        return
    try:
        rec_id = int(parts[2])
        exit_price = float(parts[3])
    except ValueError:
        await query.edit_message_text("⚠️ بيانات غير صالحة.")
        return
    try:
        trade_service: TradeService = _svc(context, "trade_service")
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    rec_id: Optional[int] = None
    if len(parts) == 3:
        try:
            rec_id = int(parts[2])
        except ValueError:
            rec_id = None
    if rec_id is not None and context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    await query.edit_message_text("تم التراجع عن الإغلاق.")

def register_management_handlers(app: Application, services: dict):
    app.add_handler(CommandHandler(
        "open",
        lambda u, c: open_cmd(u, c, trade_service=services["trade_service"]),
        filters=ALLOWED_FILTER,
    ))
    app.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:"))
    app.add_handler(CallbackQueryHandler(confirm_close, pattern=r"^rec:confirm_close:"))
    app.add_handler(CallbackQueryHandler(cancel_close, pattern=r"^rec:cancel_close:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price))
# --- END OF FILE ---