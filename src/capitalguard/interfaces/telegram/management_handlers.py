#--- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from typing import Any, List, Optional
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
)

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard

AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"  # user_data key: int rec_id

def _ts(context: ContextTypes.DEFAULT_TYPE) -> TradeService:
    svc = context.application.bot_data.get("trade_service")
    if not isinstance(svc, TradeService):
        raise RuntimeError("TradeService not initialized in bot_data")
    return svc

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        trade_service = _ts(context)
    except RuntimeError:
        await update.message.reply_text("⚠️ خدمة التداول غير متاحة.")
        return

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

    parts = (query.data or "").split(":")  # pattern: rec:close:<id>
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

    # pattern: rec:confirm_close:<rec_id>:<exit_price>
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        await query.edit_message_text("تنسيق تأكيد غير صحيح.")
        return

    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("معرّف التوصية غير صالح.")
        return

    try:
        exit_price = float(parts[3])
    except ValueError:
        await query.edit_message_text("سعر غير صالح في التأكيد.")
        return

    try:
        trade_service = _ts(context)
    except RuntimeError:
        await query.edit_message_text("⚠️ خدمة التداول غير متاحة.")
        return

    try:
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return

    # تنظيف الانتظار إن وُجد
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # rec:cancel_close:<rec_id>
    rec_id: Optional[int] = None
    if len(parts) == 3:
        try:
            rec_id = int(parts[2])
        except ValueError:
            rec_id = None

    if rec_id is not None and context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

    await query.edit_message_text("تم التراجع عن الإغلاق.")
#--- END OF FILE ---