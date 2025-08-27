from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from .keyboards import close_buttons

# In-memory stash for 'awaiting exit price' state per user (simple and sufficient for now)
AWAITING_PRICE = {}  # user_id -> rec_id

async def list_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service = context.application.bot_data.get("trade_service")
    if trade_service is None:
        await update.message.reply_text("الخدمة غير متاحة حالياً.")
        return

    # Fetch open recommendations from repo via service
    open_recs = getattr(trade_service, "list_open", lambda: [])()
    if not open_recs:
        await update.message.reply_text("لا توجد توصيات مفتوحة حالياً.")
        return

    for rec in open_recs:
        try:
            rid = int(getattr(rec, "id"))
        except Exception:
            continue
        txt = f"#{rid} — {getattr(rec, 'asset', '—')} ({getattr(rec, 'side', '—')})\nالدخول: {getattr(rec, 'entry', '—')}"
        await update.message.reply_text(txt, reply_markup=close_buttons(rid))

async def on_close_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("cg:close:"):
        return

    rec_id = int(data.split(":")[-1])
    user_id = query.from_user.id

    AWAITING_PRICE[user_id] = rec_id
    await query.edit_message_text(f"أدخل سعر الخروج لإغلاق التوصية #{rec_id}:")

async def on_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in AWAITING_PRICE:
        return  # ignore unrelated messages

    try:
        price = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("رجاءً أدخل رقمًا صحيحًا لسعر الخروج.")
        return

    rec_id = AWAITING_PRICE.pop(user_id)
    trade_service = context.application.bot_data.get("trade_service")
    if trade_service is None:
        await update.message.reply_text("الخدمة غير متاحة حالياً.")
        return

    try:
        trade_service.close(rec_id, price)
        await update.message.reply_text(f"تم إغلاق التوصية #{rec_id} عند {price}.")
    except Exception as e:
        await update.message.reply_text(f"تعذر الإغلاق: {e}")

def register_inline_handlers(app):
    app.add_handler(CommandHandler("list", list_open))
    app.add_handler(CallbackQueryHandler(on_close_button, pattern=r"^cg:close:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_exit_price))