#--- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from .keyboards import recommendation_management_keyboard, confirm_close_keyboard
from .helpers import get_service # ✅ إضافة: استيراد دالة المساعدة الآمنة

AWAITING_CLOSE_PRICE_KEY = "awaiting_close_price_for"

async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ✅ تعديل: استخدام الطريقة الآمنة للوصول إلى الخدمة
    trade_service = get_service(context, "trade_service")
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return
    for it in items:
        text = (f"<b>#{it.id}</b> — <b>{it.asset.value}</b> ({it.side.value})")
        await update.message.reply_html(text, reply_markup=recommendation_management_keyboard(it.id))

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(':')[2])
    context.user_data[AWAITING_CLOSE_PRICE_KEY] = rec_id
    await query.edit_message_text(f"🔻 أرسل الآن سعر الخروج لإغلاق التوصية #{rec_id}.")

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
    rec_id = int(query.data.split(':')[2])
    exit_price = float(query.data.split(':')[3])
    
    # ✅ تعديل: استخدام الطريقة الآمنة للوصول إلى الخدمة
    trade_service = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(f"✅ تم إغلاق التوصية <b>#{rec.id}</b>.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await query.edit_message_text(f"❌ تعذّر إغلاق التوصية: {e}")
    finally:
        if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)

async def cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(':')[2])
    if context.user_data.get(AWAITING_CLOSE_PRICE_KEY) == rec_id:
        context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
    await query.edit_message_text("تم التراجع عن الإغلاق.")

def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:"))
    application.add_handler(CallbackQueryHandler(confirm_close, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close, pattern=r"^rec:cancel_close:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, received_exit_price), group=1)
#--- END OF FILE ---