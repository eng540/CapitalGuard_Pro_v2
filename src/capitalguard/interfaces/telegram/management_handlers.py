# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import Optional, List
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, MessageHandler, filters

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard

# مفاتيح حالات الإدخال لكل مستخدم (context.user_data)
AWAITING_CLOSE_PRICE = "await_close_price_for"    # int rec_id
AWAITING_NEW_SL      = "await_new_sl_for"         # int rec_id
AWAITING_NEW_TPS     = "await_new_tps_for"        # int rec_id

# -------- أوامر عرض/إدارة --------
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    try:
        items = trade_service.list_open()
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر جلب التوصيات: {e}")
        return

    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return

    for it in items:
        asset = getattr(getattr(it, "asset", None), "value", getattr(it, "asset", "?"))
        side  = getattr(getattr(it, "side", None), "value", getattr(it, "side", "?"))
        entry = getattr(getattr(it, "entry", None), "value", getattr(it, "entry", "?"))
        sl    = getattr(getattr(it, "stop_loss", None), "value", getattr(it, "stop_loss", "?"))
        targets = getattr(getattr(it, "targets", None), "values", getattr(it, "targets", [])) or []
        tps = " • ".join(map(lambda x: f"{x:g}", targets)) if isinstance(targets, (list, tuple)) else str(targets)

        text = (
            f"🟢 #{getattr(it, 'id', '?')} — {asset} {('📈' if side=='LONG' else '📉')}\n"
            f"• الحالة: {getattr(it, 'status', 'OPEN')}\n"
            f"• الدخول: {entry}\n"
            f"• وقف الخسارة: {sl}\n"
            f"• الأهداف: {tps}"
        )
        await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(getattr(it, "id", 0)))

async def list_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    try:
        items = trade_service.list_open()
        await update.message.reply_text(f"📦 مفتوحة الآن: {len(items)}")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر الجلب: {e}")

# -------- تدفقات الإغلاق/التعديل --------
async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # rec:close:<id>
    if len(parts) != 3:
        await query.edit_message_text("تنسيق غير صحيح.")
        return
    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.edit_message_text("تعذّر قراءة رقم التوصية.")
        return

    context.user_data[AWAITING_CLOSE_PRICE] = rec_id
    await query.edit_message_text(
        f"🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية <b>#{rec_id}</b>.",
        parse_mode=ParseMode.HTML,
    )

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE_PRICE not in context.user_data:
        return
    try:
        rec_id = int(context.user_data[AWAITING_CLOSE_PRICE])
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE, None)
        await update.message.reply_text("انتهت الجلسة. استخدم /open مجددًا.")
        return
    try:
        exit_price = float((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text("⚠️ سعر غير صالح. الرجاء إدخال رقم.")
        return

    await update.message.reply_html(
        f"هل تريد تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <code>{exit_price}</code>؟",
        reply_markup=confirm_close_keyboard(rec_id, exit_price),
    )

async def confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")  # rec:confirm_close:<rec_id>:<exit_price>
    if len(parts) != 4:
        await query.edit_message_text("تنسيق تأكيد غير صحيح.")
        return
    try:
        rec_id = int(parts[2])
        exit_price = float(parts[3])
    except ValueError:
        await query.edit_message_text("⚠️ بيانات التأكيد غير صالحة.")
        return

    trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
    try:
        rec = trade.close(rec_id, exit_price)
        await query.edit_message_text(
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return
    finally:
        if context.user_data.get(AWAITING_CLOSE_PRICE) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # rec:cancel_close:<rec_id>
    try:
        rec_id = int((query.data or "").split(":")[2])
    except Exception:
        rec_id = None
    if context.user_data.get(AWAITING_CLOSE_PRICE) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE, None)
    await query.edit_message_text("تم التراجع عن الإغلاق.")

# -------- تعديل SL --------
async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        rec_id = int((query.data or "").split(":")[2])  # rec:amend_sl:<id>
    except Exception:
        await query.edit_message_text("تنسيق غير صحيح.")
        return
    context.user_data[AWAITING_NEW_SL] = rec_id
    await query.edit_message_text(
        f"🛡️ أرسل قيمة <b>SL</b> الجديدة للتوصية <b>#{rec_id}</b>.",
        parse_mode=ParseMode.HTML,
    )

async def received_new_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_NEW_SL not in context.user_data:
        return
    try:
        rec_id = int(context.user_data[AWAITING_NEW_SL])
    except Exception:
        context.user_data.pop(AWAITING_NEW_SL, None)
        return
    try:
        new_sl = float((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text("⚠️ قيمة SL غير صالحة. أدخل رقمًا.")
        return

    trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
    try:
        rec = trade.update_stop_loss(rec_id, new_sl)
        await update.message.reply_html(
            f"✅ تم تحديث SL للتوصية <b>#{rec.id}</b> إلى <code>{new_sl}</code>."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر التحديث: {e}")
    finally:
        context.user_data.pop(AWAITING_NEW_SL, None)

# -------- تعديل الأهداف --------
async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        rec_id = int((query.data or "").split(":")[2])  # rec:amend_tp:<id>
    except Exception:
        await query.edit_message_text("تنسيق غير صحيح.")
        return
    context.user_data[AWAITING_NEW_TPS] = rec_id
    await query.edit_message_text(
        f"🎯 أرسل قائمة الأهداف الجديدة للتوصية <b>#{rec_id}</b> مفصولة بمسافة أو فاصلة.",
        parse_mode=ParseMode.HTML,
    )

async def received_new_tps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_NEW_TPS not in context.user_data:
        return
    try:
        rec_id = int(context.user_data[AWAITING_NEW_TPS])
    except Exception:
        context.user_data.pop(AWAITING_NEW_TPS, None)
        return
    try:
        targets: List[float] = [float(t) for t in (update.message.text or "").replace(",", " ").split() if t]
        if not targets:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ قائمة أهداف غير صالحة. أدخل أرقامًا مفصولة بمسافة/فاصلة.")
        return

    trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
    try:
        rec = trade.update_targets(rec_id, targets)
        await update.message.reply_html(
            f"✅ تم تحديث الأهداف للتوصية <b>#{rec.id}</b>."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر التحديث: {e}")
    finally:
        context.user_data.pop(AWAITING_NEW_TPS, None)

# -------- تجميعة message handlers (اختياري استيرادها في register) --------
def get_management_message_handlers():
    """
    يُستخدم لتجميع مُعالجَي نصوص الإدخال (SL/TP/Exit Price) في group مناسب.
    """
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price),
        MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_sl),
        MessageHandler(filters.TEXT & ~filters.COMMAND, received_new_tps),
    ]
# --- END OF FILE ---