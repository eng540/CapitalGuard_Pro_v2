#--- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import Optional
import re

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard
from .ui_texts import RecCard, ASK_EXIT_PRICE, INVALID_PRICE, CLOSE_CONFIRM, CLOSE_DONE, OPEN_EMPTY

# مفتاح حالة انتظار سعر الإغلاق لكل مستخدم
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

# Regex لقبول "123" أو "123.45" (مع دعم تحويل الأرقام العربية لاحقًا)
_NUM_RE = re.compile(r"^\s*([0-9]+(\.[0-9]+)?)\s*$")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.strip().translate(_ARABIC_DIGITS)
    m = _NUM_RE.match(t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None

# ======================
# أوامر
# ======================
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض التوصيات المفتوحة برسالة لكل توصية، مع تنسيق موحّد وبطاقات نظيفة."""
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
            side  = getattr(getattr(it, "side", None), "value", getattr(it, "side", "?"))
            entry_val = getattr(getattr(it, "entry", None), "value", getattr(it, "entry", "?"))
            sl_val    = getattr(getattr(it, "stop_loss", None), "value", getattr(it, "stop_loss", "?"))
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
# تدفّق الإغلاق
# ======================
async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر: rec:close:<id> → اطلب من المستخدم إرسال السعر واحفظ rec_id في user_data."""
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

    # خزّن rec_id في user_data للمستخدم الحالي
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ تراجع", callback_data=f"rec:cancel_close:{rec_id}")]])
    await query.edit_message_text(ASK_EXIT_PRICE, parse_mode=ParseMode.HTML, reply_markup=kb)

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    تعمل فقط إذا كان المستخدم بانتظار إدخال السعر.
    لا تنفّذ أي إغلاق هنا — فقط تطلب التأكيد.
    """
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return

    # استخرج rec_id بأمان
    try:
        rec_id = int(context.user_data[AWAITING_CLOSE_PRICE_KEY])
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
        await update.message.reply_text("انتهت صلاحية هذه الجلسة. ابدأ من جديد بالأمر /open.")
        return

    # حوّل النص إلى رقم (يدعم أرقام عربية)
    exit_price = _parse_price(update.message.text or "")
    if exit_price is None:
        await update.message.reply_html(INVALID_PRICE)
        return

    # اطلب التأكيد عبر أزرار — لا إغلاق هنا
    await update.message.reply_html(
        CLOSE_CONFIRM(rec_id, exit_price),
        reply_markup=confirm_close_keyboard(rec_id, exit_price),
    )

async def confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر: rec:confirm_close:<rec_id>:<exit_price> → يغلق فعليًا عبر الخدمة."""
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
    try:
        if int(context.user_data.get(AWAITING_CLOSE_PRICE_KEY)) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر: rec:cancel_close:<rec_id> → يلغي العملية وينظّف الحالة إن كانت تخص هذا rec_id."""
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")  # rec:cancel_close:<rec_id>
    rec_id: Optional[int] = None
    if len(parts) == 3:
        try:
            rec_id = int(parts[2])
        except ValueError:
            rec_id = None

    try:
        if rec_id is not None and int(context.user_data.get(AWAITING_CLOSE_PRICE_KEY)) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

    await query.edit_message_text("تم التراجع عن الإغلاق.")
#--- END OF FILE ---