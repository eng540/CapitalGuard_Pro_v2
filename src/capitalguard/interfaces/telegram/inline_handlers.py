from io import BytesIO
from datetime import datetime, timedelta
from typing import List, Any

from telegram import Update, InputFile
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

from .keyboards import close_buttons, confirm_close_buttons, list_nav_buttons

# حالات مؤقتة بالذاكرة (لا DB)
AWAITING_PRICE = {}   # user_id -> rec_id
PAGINATION = {        # chat_id -> {"page": int, "page_size": int}
    # افتراضيًا سنبدأ بـ page=0, size=5
}

PAGE_SIZE_DEFAULT = 5

# --------- أدوات مساعدة بسيطة ---------
def _as_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

def _as_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def _paginate(items: List[Any], page: int, page_size: int):
    total = len(items)
    start = page * page_size
    end = start + page_size
    chunk = items[start:end]
    has_prev = page > 0
    has_next = end < total
    return chunk, has_prev, has_next

# --------- أوامر عامة ---------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *CapitalGuard Bot — المساعدة*\n\n"
        "/help — هذه القائمة\n"
        "/list — عرض التوصيات المفتوحة مع صفحات\n"
        "/stats [period] — إحصائيات الأداء (period: today|week|all)\n"
        "/report [period] — تقرير CSV + ملخص (period: today|week|all)\n\n"
        "*أمثلة:*\n"
        "/stats today\n"
        "/report week\n"
    )
    await update.message.reply_markdown(text)

def _period_bounds(period: str):
    now = datetime.utcnow()
    if period == "today":
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
    elif period == "week":
        start = now - timedelta(days=7)
        end = now
    else:
        start = None
        end = None
    return start, end

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    period = (context.args[0].lower() if context.args else "all")
    start, end = _period_bounds(period)
    analytics: Any = context.application.bot_data.get("analytics_service")
    if analytics is None:
        await update.message.reply_text("خدمة التحليلات غير متاحة.")
        return

    try:
        # نتوقع أن AnalyticsService يدعم شيئًا مثل: summary(start=None, end=None)
        summary = analytics.summary(start=start, end=end)
        # توقّع الحقول التالية أو ما يشبهها:
        closed = summary.get("closed", 0)
        winrate = summary.get("winrate", 0.0)
        pnl_total = summary.get("pnl_total", 0.0)
        pnl_avg = summary.get("pnl_avg", 0.0)
        best = summary.get("best", 0.0)
        worst = summary.get("worst", 0.0)
        txt = (
            f"📊 *ملخص الأداء ({period})*\n"
            f"• الصفقات المغلقة: {closed}\n"
            f"• نسبة النجاح: {winrate:.2f}%\n"
            f"• إجمالي PnL: {pnl_total:.2f}%\n"
            f"• متوسط PnL: {pnl_avg:.2f}%\n"
            f"• أفضل صفقة: {best:.2f}%\n"
            f"• أسوأ صفقة: {worst:.2f}%\n"
        )
        await update.message.reply_markdown(txt)
    except Exception as e:
        await update.message.reply_text(f"تعذر حساب الإحصائيات: {e}")

async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    period = (context.args[0].lower() if context.args else "all")
    start, end = _period_bounds(period)
    analytics: Any = context.application.bot_data.get("analytics_service")
    if analytics is None:
        await update.message.reply_text("خدمة التحليلات غير متاحة.")
        return

    try:
        # نتوقع أن analytics لديه: export_rows(start, end) -> List[dict]
        rows = analytics.export_rows(start=start, end=end)  # اكتبها عندك إن لم توجد
        if not rows:
            await update.message.reply_text("لا توجد بيانات للتقرير.")
            return

        # توليد CSV بالذاكرة
        import csv
        buf = BytesIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        buf.seek(0)

        # إرسال الملف + ملخص مختصر باستخدام summary()
        summary = analytics.summary(start=start, end=end)
        caption = (
            f"📄 تقرير {period}\n"
            f"Closed={summary.get('closed',0)} | "
            f"WinRate={summary.get('winrate',0):.2f}% | "
            f"TotalPnL={summary.get('pnl_total',0):.2f}%"
        )
        filename = f"capitalguard_report_{period}.csv"
        await update.message.reply_document(document=InputFile(buf, filename=filename), caption=caption)
    except Exception as e:
        await update.message.reply_text(f"تعذر إنشاء التقرير: {e}")

# --------- /list مع صفحات + إغلاق ---------
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_service: Any = context.application.bot_data.get("trade_service")
    if trade_service is None:
        await update.message.reply_text("الخدمة غير متاحة حالياً.")
        return

    # إعداد الصفحة الافتراضية
    chat_id = update.effective_chat.id
    state = PAGINATION.get(chat_id, {"page": 0, "page_size": PAGE_SIZE_DEFAULT})

    try:
        open_recs: List[Any] = getattr(trade_service, "list_open", lambda: [])()
    except Exception as e:
        await update.message.reply_text(f"تعذر جلب التوصيات: {e}")
        return

    page = state["page"]
    page_size = state["page_size"]
    chunk, has_prev, has_next = _paginate(open_recs, page, page_size)

    if not chunk:
        await update.message.reply_text("لا توجد توصيات مفتوحة حالياً.")
        return

    for rec in chunk:
        rid = _as_int(getattr(rec, "id", None))
        asset = getattr(rec, "asset", "—")
        side = getattr(rec, "side", "—")
        entry = getattr(rec, "entry", "—")
        text = f"#{rid} — {asset} ({side})\nالدخول: {entry}"
        await update.message.reply_text(text, reply_markup=close_buttons(rid))

    # شريط تنقل بالأسفل (زر التالي/السابق)
    nav = list_nav_buttons(page, has_prev, has_next)
    if nav:
        await update.message.reply_text(f"صفحة {page+1}", reply_markup=nav)

async def on_list_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (query.data or "").split(":")
    # صيغة: cg:list:page:<n>
    if len(data) != 4:
        return
    page = _as_int(data[-1], 0)

    chat_id = query.message.chat_id
    state = PAGINATION.get(chat_id, {"page": 0, "page_size": PAGE_SIZE_DEFAULT})
    state["page"] = max(0, page)
    PAGINATION[chat_id] = state

    # بدل إعادة طباعة كل شيء هنا، اطلب من المستخدم أمر /list مجددًا لعرض الصفحة
    await query.edit_message_text(f"تم الانتقال إلى الصفحة {page+1}. أرسل /list للعرض.")

# زر “إغلاق” الأولي: يطلب سعر الخروج
async def on_close_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("cg:close:"):
        return

    rec_id = _as_int(data.split(":")[-1])
    user_id = query.from_user.id

    AWAITING_PRICE[user_id] = rec_id
    await query.edit_message_text(f"أدخل سعر الخروج لإغلاق التوصية #{rec_id}:")

# استقبال سعر الخروج، ثم طلب “تأكيد/إلغاء”
async def on_exit_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in AWAITING_PRICE:
        return

    txt = (update.message.text or "").strip()
    price = _as_float(txt)
    if price is None or price <= 0:
        await update.message.reply_text("رجاءً أدخل رقمًا صالحًا لسعر الخروج.")
        return

    rec_id = AWAITING_PRICE[user_id]  # لا نحذف الآن.. حتى يؤكد
    # عرض أزرار التأكيد
    await update.message.reply_text(
        f"تأكيد إغلاق التوصية #{rec_id} عند {price}؟",
        reply_markup=confirm_close_buttons(rec_id, price)
    )

# تأكيد/إلغاء الإغلاق
async def on_confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (query.data or "")

    # إلغاء
    if data.startswith("cg:cancelclose:"):
        rec_id = _as_int(data.split(":")[-1])
        # تنظيف الحالة
        uid = query.from_user.id
        if uid in AWAITING_PRICE:
            AWAITING_PRICE.pop(uid, None)
        await query.edit_message_text(f"تم إلغاء إغلاق التوصية #{rec_id}.")
        return

    # تأكيد
    if data.startswith("cg:confirmclose:"):
        _, _, rec_id_s, price_s = data.split(":")
        rec_id = _as_int(rec_id_s)
        price = _as_float(price_s)
        uid = query.from_user.id

        trade_service: Any = context.application.bot_data.get("trade_service")
        if trade_service is None:
            await query.edit_message_text("الخدمة غير متاحة حالياً.")
            return

        try:
            # نظّف الحالة قبل التنفيذ
            if uid in AWAITING_PRICE:
                AWAITING_PRICE.pop(uid, None)

            trade_service.close(rec_id, price)
            await query.edit_message_text(f"تم إغلاق التوصية #{rec_id} عند {price}.")
        except Exception as e:
            await query.edit_message_text(f"تعذر الإغلاق: {e}")

# تسجيل المعالجات
def register_inline_handlers(app):
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("list", cmd_list))

    app.add_handler(CallbackQueryHandler(on_list_nav, pattern=r"^cg:list:page:\d+$"))
    app.add_handler(CallbackQueryHandler(on_close_button, pattern=r"^cg:close:\d+$"))
    app.add_handler(CallbackQueryHandler(on_confirm_cancel, pattern=r"^cg:(confirmclose|cancelclose):.*$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_exit_price))