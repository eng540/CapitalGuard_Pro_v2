from typing import Optional, List
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard

# مفاتيح حالة انتظار إدخال من المستخدم
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"
AWAITING_NEW_SL_KEY = "awaiting_new_sl_for"
AWAITING_NEW_TPS_KEY = "awaiting_new_tps_for"

# ======================
# أوامر
# ======================
async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service: TradeService):
    """يعرض التوصيات المفتوحة برسالة لكل توصية، مع حماية من الحقول المتنوعة."""
    try:
        items = trade_service.list_open()
    except Exception as e:
        await update.message.reply_text(f"❌ تعذّر جلب التوصيات: {e}")
        return

    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return

    for it in items:
        try:
            asset = getattr(getattr(it, "asset", None), "value", getattr(it, "asset", "?"))
            side  = getattr(getattr(it, "side", None), "value", getattr(it, "side", "?"))
            entry_val = getattr(getattr(it, "entry", None), "value", getattr(it, "entry", "?"))
            sl_val    = getattr(getattr(it, "stop_loss", None), "value", getattr(it, "stop_loss", "?"))
            targets   = getattr(getattr(it, "targets", None), "values", getattr(it, "targets", [])) or []
            tps = ", ".join(map(str, targets)) if isinstance(targets, (list, tuple)) else str(targets)

            text = (
                f"<b>#{getattr(it, 'id', '?')}</b> — <b>{asset}</b> ({side})\n"
                f"Entry: <code>{entry_val}</code> | SL: <code>{sl_val}</code>\n"
                f"TPs: <code>{tps}</code>"
            )
            await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(getattr(it, "id", 0)))
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
    await query.edit_message_text(
        f"🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية <b>#{rec_id}</b>.",
        parse_mode=ParseMode.HTML,
    )

async def received_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    تعمل فقط إذا كان المستخدم بانتظار إدخال السعر.
    لا تنفّذ أي إغلاق هنا — فقط تطلب التأكيد.
    """
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return

    try:
        rec_id = int(context.user_data[AWAITING_CLOSE_PRICE_KEY])
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
        await update.message.reply_text("انتهت صلاحية هذه الجلسة. ابدأ من جديد بالأمر /open.")
        return

    txt = (update.message.text or "").strip()
    try:
        exit_price = float(txt)
    except ValueError:
        await update.message.reply_text("⚠️ سعر غير صالح. الرجاء إدخال رقم صحيح.")
        return

    await update.message.reply_html(
        f"هل تريد تأكيد إغلاق التوصية <b>#{rec_id}</b> على سعر <code>{exit_price}</code>؟",
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
            f"✅ تم إغلاق التوصية <b>#{rec.id}</b> على سعر <code>{exit_price}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
        return

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

# ======================
# تعديل SL/الأهداف/السجل — أزرار القناة
# ======================
async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """rec:amend_sl:<id> → طلب SL جديد."""
    q = update.callback_query
    await q.answer()
    try:
        rec_id = int((q.data or "::-1").split(":")[2])
    except Exception:
        rec_id = -1
    context.user_data[AWAITING_NEW_SL_KEY] = rec_id
    await q.edit_message_text(f"🛡️ أرسل قيمة SL الجديدة للتوصية #{rec_id}:")

async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """rec:amend_tp:<id> → طلب قائمة أهداف جديدة."""
    q = update.callback_query
    await q.answer()
    try:
        rec_id = int((q.data or "::-1").split(":")[2])
    except Exception:
        rec_id = -1
    context.user_data[AWAITING_NEW_TPS_KEY] = rec_id
    await q.edit_message_text("🎯 أرسل الأهداف الجديدة مفصولة بمسافة أو فاصلة:")

async def click_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """rec:history:<id> → Placeholder الآن."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("📜 السجل: قريبًا سيتم عرض سجل المعاملات للتوصية.")

# يمكن – مؤقتًا – إعادة استخدام received_exit_price كملتقط للنصوص،
# لكن لتفادي التعارض سنضيف معالجات خاصة أدناه (إن رغبت لاحقًا).

# (اختياري) يمكنك لاحقًا إضافة MessageHandlers لمعالجة قيم SL/TP الجديدة
# واستدعاء trade_service.update_stop_loss / update_targets إذا كانت موجودة.