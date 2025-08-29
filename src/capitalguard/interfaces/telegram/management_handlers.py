# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import Optional, List

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard
from .ui_texts import RecCard, _pct, OPEN  # type: ignore  # (OPEN لن نستخدمها الآن)

# مفاتيح حالة انتظار الإدخال لكل مستخدم
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"
AWAITING_NEW_SL_KEY = "awaiting_new_sl_for"
AWAITING_NEW_TPS_KEY = "awaiting_new_tps_for"

# ======================
# أدوات مساعدة
# ======================
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _to_float_safe(text: str) -> Optional[float]:
    try:
        if text is None:
            return None
        t = text.strip().translate(_ARABIC_DIGITS)
        t = t.replace(",", ".") if ("," in t and "." not in t) else t
        return float(t)
    except Exception:
        return None

def _to_float_list(text: str) -> Optional[List[float]]:
    try:
        if text is None:
            return None
        t = text.replace(",", " ")
        vals = [float(x) for x in t.split() if x.strip()]
        if not vals:
            return None
        return vals
    except Exception:
        return None

# ======================
# أوامر
# ======================
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض التوصيات المفتوحة كبطاقات موجزة مع أزرار إدارة."""
    # دعم فلترة مبسطة: /open BTCUSDT
    args = (update.message.text or "").split(maxsplit=1)
    symbol = args[1].strip().upper() if len(args) == 2 else None

    try:
        items = trade_service.list_open(symbol=symbol)
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر جلب التوصيات: {e}")
        return

    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return

    for it in items:
        try:
            asset = getattr(getattr(it, "asset", None), "value", getattr(it, "asset", "?"))
            side  = getattr(getattr(it, "side", None),  "value", getattr(it, "side",  "?"))

            entry_val = getattr(getattr(it, "entry", None), "value", getattr(it, "entry", None))
            sl_val    = getattr(getattr(it, "stop_loss", None), "value", getattr(it, "stop_loss", None))
            targets   = getattr(getattr(it, "targets", None), "values", getattr(it, "targets", [])) or []

            card = RecCard(
                id=int(getattr(it, "id", 0)),
                asset=str(asset),
                side=str(side),
                status=str(getattr(it, "status", "OPEN")),
                entry=float(entry_val),
                stop_loss=float(sl_val),
                targets=list(targets) if isinstance(targets, (list, tuple)) else [],
                exit_price=getattr(it, "exit_price", None),
                market=getattr(it, "market", None),
                notes=getattr(it, "notes", None),
            )
            kb = recommendation_management_keyboard(card.id)
            await update.message.reply_html(card.to_text(), reply_markup=kb)
        except Exception as e:
            await update.message.reply_text(f"⚠️ عنصر غير متوقع: {e}")

async def list_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض عدد التوصيات المفتوحة (تشخيص سريع). يدعم فلترة: /list BTCUSDT"""
    args = (update.message.text or "").split(maxsplit=1)
    symbol = args[1].strip().upper() if len(args) == 2 else None
    try:
        items = trade_service.list_open(symbol=symbol)
        if symbol:
            await update.message.reply_text(f"📦 مفتوحة الآن ({symbol}): {len(items)}")
        else:
            await update.message.reply_text(f"📦 مفتوحة الآن: {len(items)}")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر الجلب: {e}")

# ======================
# تدفّق الإغلاق (قناة → DM)
# ======================
async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.answer("تنسيق غير صحيح.", show_alert=True)
        return

    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.answer("تعذّر قراءة رقم التوصية.", show_alert=True)
        return

    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    try:
        await context.bot.send_message(chat_id=query.from_user.id, text="🔻 أرسل الآن <b>سعر الخروج</b>.", parse_mode=ParseMode.HTML)
        await query.answer("تم إرسال رسالة خاصة لك لبدء الإغلاق.", show_alert=False)
    except Exception:
        await query.edit_message_text("🔻 أرسل الآن <b>سعر الخروج</b>.", parse_mode=ParseMode.HTML)

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return
    rec_id = context.user_data.get(AWAITING_CLOSE_PRICE_KEY)
    price = _to_float_safe((update.message.text or ""))
    if price is None:
        await update.message.reply_html("⚠️ سعر غير صالح. الرجاء إدخال رقم صحيح.")
        return
    await update.message.reply_html(
        f"تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <code>{price:g}</code>؟",
        reply_markup=confirm_close_keyboard(int(rec_id), price),
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
        await query.edit_message_text("⚠️ بيانات التأكيد غير صالحة.")
        return

    try:
        trade_service: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price:g}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return

    context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    await query.edit_message_text("تم التراجع عن الإغلاق.")

# ======================
# تعديل SL
# ======================
async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")  # rec:amend_sl:<id>
    if len(parts) != 3:
        await query.answer("تنسيق غير صحيح.", show_alert=True)
        return
    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.answer("تعذّر قراءة رقم التوصية.", show_alert=True)
        return
    context.user_data[AWAITING_NEW_SL_KEY] = rec_id
    try:
        await context.bot.send_message(chat_id=query.from_user.id, text="🛡️ أرسل قيمة <b>SL</b> الجديدة:", parse_mode=ParseMode.HTML)
        await query.answer("أرسلنا لك رسالة خاصة.", show_alert=False)
    except Exception:
        await query.edit_message_text("🛡️ أرسل قيمة <b>SL</b> الجديدة:", parse_mode=ParseMode.HTML)

async def received_new_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_NEW_SL_KEY not in context.user_data:
        return
    rec_id = int(context.user_data.get(AWAITING_NEW_SL_KEY))
    val = _to_float_safe((update.message.text or ""))
    if val is None:
        await update.message.reply_html("⚠️ قيمة غير صالحة. الرجاء إدخال رقم.")
        return
    try:
        trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
        trade.update_stop_loss(rec_id, val)
        await update.message.reply_html(f"✅ تم تحديث SL للتوصية <b>#{rec_id}</b> إلى <code>{val:g}</code>.")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر التحديث: {e}")
    finally:
        context.user_data.pop(AWAITING_NEW_SL_KEY, None)

# ======================
# تعديل الأهداف
# ======================
async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")  # rec:amend_tp:<id>
    if len(parts) != 3:
        await query.answer("تنسيق غير صحيح.", show_alert=True)
        return
    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.answer("تعذّر قراءة رقم التوصية.", show_alert=True)
        return
    context.user_data[AWAITING_NEW_TPS_KEY] = rec_id
    try:
        await context.bot.send_message(chat_id=query.from_user.id, text="🎯 أرسل الأهداف الجديدة مفصولة بمسافة أو فاصلة:", parse_mode=ParseMode.HTML)
        await query.answer("أرسلنا لك رسالة خاصة.", show_alert=False)
    except Exception:
        await query.edit_message_text("🎯 أرسل الأهداف الجديدة مفصولة بمسافة أو فاصلة:", parse_mode=ParseMode.HTML)

async def received_new_tps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_NEW_TPS_KEY not in context.user_data:
        return
    rec_id = int(context.user_data.get(AWAITING_NEW_TPS_KEY))
    vals = _to_float_list((update.message.text or ""))
    if not vals:
        await update.message.reply_html("⚠️ قائمة غير صالحة. الرجاء إدخال أرقام مفصولة بمسافة أو فاصلة.")
        return
    try:
        trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
        trade.update_targets(rec_id, vals)
        friendly = " • ".join(f"{v:g}" for v in vals)
        await update.message.reply_html(f"✅ تم تحديث الأهداف للتوصية <b>#{rec_id}</b> إلى: <code>{friendly}</code>.")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر التحديث: {e}")
    finally:
        context.user_data.pop(AWAITING_NEW_TPS_KEY, None)

# ======================
# السجل
# ======================
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")  # rec:history:<id>
    if len(parts) != 3:
        await query.answer("تنسيق غير صحيح.", show_alert=True)
        return
    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.answer("تعذّر قراءة رقم التوصية.", show_alert=True)
        return

    trade: TradeService = context.application.bot_data.get("trade_service")  # type: ignore
    rec = trade.get(rec_id)
    if not rec:
        await query.edit_message_text("لم يتم العثور على التوصية.")
        return

    def _fmt_dt(dt) -> str:
        return dt.isoformat(sep=" ", timespec="minutes") if dt else "-"

    text = (
        f"📜 <b>السجل — #{rec.id}</b>\n"
        f"• Created: {_fmt_dt(rec.created_at)}\n"
        f"• Published: {_fmt_dt(rec.published_at)}\n"
        f"• Updated: {_fmt_dt(rec.updated_at)}\n"
        f"• Closed: {_fmt_dt(rec.closed_at)}\n"
        f"• Status: <b>{rec.status}</b>\n"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)
# --- END OF FILE ---