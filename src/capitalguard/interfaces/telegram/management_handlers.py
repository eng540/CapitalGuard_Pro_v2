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
from .auth import ALLOWED_FILTER
from capitalguard.application.services.trade_service import TradeService
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard

# مفتاح حالة انتظار سعر الإغلاق لكل مستخدم
AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

def _svc(context: ContextTypes.DEFAULT_TYPE, name: str):
    svc = context.application.bot_data.get(name)
    if not svc:
        raise RuntimeError(f"Service '{name}' not initialized in bot_data")
    return svc

# ======================
# أوامر
# ======================
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

# ======================
# تدفّق الإغلاق
# ======================
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
    # يعمل فقط إذا كنّا بانتظار السعر
    if AWAITING_CLOSE_PRICE_KEY not in context.user_data:
        return

    # استخرج rec_id بأمان
    try:
        rec_id = int(context.user_data[AWAITING_CLOSE_PRICE_KEY])
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
        await update.message.reply_text("انتهت صلاحية هذه الجلسة. ابدأ من جديد بالأمر /open.")
        return

    # حوّل النص إلى رقم
    txt = (update.message.text or "").strip()
    try:
        exit_price = float(txt)
    except ValueError:
        await update.message.reply_text("⚠️ سعر غير صالح. الرجاء إدخال رقم صحيح.")
        return

    # اطلب التأكيد عبر أزرار — لا إغلاق هنا
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

    # استخرج القيم بأمان
    try:
        rec_id = int(parts[2])
        exit_price = float(parts[3])
    except ValueError:
        await query.edit_message_text("⚠️ بيانات التأكيد غير صالحة.")
        return

    # نفّذ الإغلاق فعليًا
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

    # تنظيف حالة الانتظار للمستخدم الحالي فقط
    try:
        if int(context.user_data.get(AWAITING_CLOSE_PRICE_KEY)) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    except Exception:
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

    try:
        if rec_id is not None and int(context.user_data.get(AWAITING_CLOSE_PRICE_KEY)) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    except Exception:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

    await query.edit_message_text("تم التراجع عن الإغلاق.")

# ======================
# التسجيل
# ======================
def register_management_handlers(app: Application, services: dict):
    # أمر /open بحقن صريح
    app.add_handler(CommandHandler(
        "open",
        lambda u, c: open_cmd(u, c, trade_service=services["trade_service"]),
        filters=ALLOWED_FILTER,
    ))
    # أزرار الإغلاق
    app.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_close, pattern=r"^rec:confirm_close:\d+:[0-9.]+$"))
    app.add_handler(CallbackQueryHandler(cancel_close, pattern=r"^rec:cancel_close:\d+$"))
    # استقبال السعر (group أعلى من المحادثات لتفادي التضارب)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
# --- END OF FILE ---