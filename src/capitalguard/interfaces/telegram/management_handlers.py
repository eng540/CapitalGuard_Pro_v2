# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard
from .ui_texts import RecCard, ASK_EXIT_PRICE, INVALID_PRICE, CLOSE_CONFIRM, CLOSE_DONE, OPEN_EMPTY

# مفتاح حالة انتظار سعر الإغلاق لكل مستخدم (يُخزَّن داخل context.user_data)
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

# ======================
# أدوات مساعدة
# ======================
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _to_float_safe(text: str) -> Optional[float]:
    try:
        if text is None:
            return None
        t = text.strip().translate(_ARABIC_DIGITS)
        # السماح بفاصلة كفاصل عشري أيضًا
        t = t.replace(",", ".") if ("," in t and "." not in t) else t
        return float(t)
    except Exception:
        return None

# ======================
# أوامر
# ======================
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض التوصيات المفتوحة كبطاقات موجزة مع أزرار إدارة."""
    try:
        items = trade_service.list_open()
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر جلب التوصيات: {e}")
        return

    if not items:
        await update.message.reply_text(OPEN_EMPTY)
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
            )
            kb = recommendation_management_keyboard(card.id)
            await update.message.reply_html(card.to_text(), reply_markup=kb)
        except Exception as e:
            await update.message.reply_text(f"⚠️ عنصر غير متوقع: {e}")

async def list_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض عدد التوصيات المفتوحة (تشخيص سريع)."""
    try:
        items = trade_service.list_open()
        await update.message.reply_text(f"📦 مفتوحة الآن: {len(items)}")
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر الجلب: {e}")

# ======================
# تدفّق الإغلاق (قناة → DM)
# ======================
async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    زر: rec:close:<id> → أرسل DM لطلب السعر، واحفظ rec_id في مساحة المستخدم (context.user_data).
    """
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # pattern: rec:close:<id>
    if len(parts) != 3:
        await query.answer("تنسيق غير صحيح.", show_alert=True)
        return

    try:
        rec_id = int(parts[2])
    except ValueError:
        await query.answer("تعذّر قراءة رقم التوصية.", show_alert=True)
        return

    # خزّن rec_id في user_data للمستخدم الذي ضغط الزر
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id

    # أرسل رسالة خاصة لطلب السعر (قد تفشل إذا المستخدم لم يبدأ محادثة مع البوت)
    try:
        await context.bot.send_message(chat_id=query.from_user.id, text=ASK_EXIT_PRICE, parse_mode=ParseMode.HTML)
        await query.answer("تم إرسال رسالة خاصة لك لبدء الإغلاق.", show_alert=False)
    except Exception:
        # fallback: تحرير الرسالة الحالية (إذا كان DM أصلًا)
        await query.edit_message_text(ASK_EXIT_PRICE, parse_mode=ParseMode.HTML)

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعمل فقط إذا كان المستخدم بانتظار إدخال السعر (داخل DM).
    يعتمد على القيمة المخزنة في context.user_data[AWAITING_CLOSE_PRICE_KEY].
    """
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return

    rec_id = context.user_data.get(AWAITING_CLOSE_PRICE_KEY)

    txt = (update.message.text or "").strip()
    exit_price = _to_float_safe(txt)
    if exit_price is None:
        await update.message.reply_html(INVALID_PRICE)
        return

    await update.message.reply_html(
        CLOSE_CONFIRM(int(rec_id), exit_price),
        reply_markup=confirm_close_keyboard(int(rec_id), exit_price),
    )

async def confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر: rec:confirm_close:<rec_id>:<exit_price> → يغلق فعليًا عبر الخدمة ثم يحدّث بطاقة القناة."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # pattern: rec:confirm_close:<rec_id>:<exit_price>
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
        if not isinstance(trade_service, TradeService):
            raise RuntimeError("TradeService ليس مهيأً في bot_data")
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(
            CLOSE_DONE(rec.id, exit_price),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return

    # تنظيف حالة الانتظار للمستخدم الحالي فقط
    context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر: rec:cancel_close:<rec_id> → يلغي العملية وينظّف الحالة إن كانت تخص هذا rec_id."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # rec:cancel_close:<rec_id>
    try:
        rec_id = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        rec_id = None

    # نظّف الحالة فقط إذا تخص نفس التوصية
    if rec_id is None or context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

    await query.edit_message_text("تم التراجع عن الإغلاق.")
# --- END OF FILE ---