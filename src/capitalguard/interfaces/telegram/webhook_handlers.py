#--- START OF FILE: src/capitalguard/interfaces/telegram/webhook_handlers.py ---
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from capitalguard.config import settings

# خدمات/بنية تحتية لتجهيز حقن احتياطي عند الحاجة
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService  # ✅

# محادثة إنشاء التوصية + أزرار التأكيد/الإلغاء
from .conversation_handlers import (
    get_recommendation_conversation_handler,
    publish_recommendation,
    cancel_publication,
)

# إدارة التوصيات المفتوحة والإغلاق السهل
from .management_handlers import register_management_handlers

# --- Allowed users ---
ALLOWED_USERS = {int(uid.strip()) for uid in (settings.TELEGRAM_ALLOWED_USERS or "").split(",") if uid.strip()}
ALLOWED_FILTER = filters.User(list(ALLOWED_USERS)) if ALLOWED_USERS else filters.ALL


# --- Unauthorized handler (group=-1) ---
async def unauthorized_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        if update.message:
            await update.message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")


# --- Helpers ---
def _fmt_report(summary: dict) -> str:
    lines = ["<b>تقرير الأداء</b>"]
    for k, v in summary.items():
        lines.append(f"• <b>{k}</b>: {v}")
    return "\n".join(lines)

def _fmt_analytics(summary: dict) -> str:
    return (
        "<b>📊 ملخص الأداء</b>\n"
        f"• <b>الصفقات المغلقة:</b> {summary.get('total_closed_trades', 0)}\n"
        f"• <b>نسبة النجاح:</b> {summary.get('win_rate_percent', 0)}%\n"
        f"• <b>إجمالي PnL:</b> {summary.get('total_pnl_percent', 0)}%\n"
        f"• <b>متوسط PnL:</b> {summary.get('average_pnl_percent', 0)}%\n"
        f"• <b>أفضل صفقة:</b> {summary.get('best_trade_pnl_percent', 0)}%\n"
        f"• <b>أسوأ صفقة:</b> {summary.get('worst_trade_pnl_percent', 0)}%"
    )


def _ensure_services(application: Application):
    """
    ✅ حقن احتياطي للخدمات داخل bot_data إذا كانت مفقودة.
    هذا يحل مشكلة التشغيل عندما لا يكون main.py هو المُشغّل الذي يحقن الخدمات.
    """
    bd = application.bot_data
    needs_inject = any(k not in bd for k in ("trade_service", "report_service", "analytics_service"))
    if not needs_inject:
        return

    # تهيئة البنية التحتية والخدمات
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    trade = TradeService(repo, notifier)
    report = ReportService(repo)
    analytics = AnalyticsService(repo)

    bd["trade_service"] = trade
    bd["report_service"] = report
    bd["analytics_service"] = analytics


# --- Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec</code> — بدء محادثة تفاعلية لإنشاء توصية.\n"
        "• <code>/open</code> — عرض التوصيات المفتوحة مع أزرار الإدارة.\n"
        "• <code>/close &lt;id&gt; &lt;exit_price&gt;</code>\n"
        "• <code>/list</code>\n"
        "• <code>/report</code>\n"
        "• <code>/analytics</code>\n"
        "• <code>/diag_services</code> — فحص الخدمات المحقونة.\n"
        "• <code>/webhook_info</code> — فحص إعداد الويب هوك.\n"
    )

async def close_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, trade_service: TradeService):
    try:
        parts = (update.message.text or "").split()
        if len(parts) != 3:
            raise ValueError("صيغة غير صحيحة.")
        _, rec_id_str, exit_price_str = parts
        rec = trade_service.close(int(rec_id_str), float(exit_price_str))
        await update.message.reply_html(f"✅ تم إغلاق التوصية <b>#{rec.id}</b> ({rec.asset.value})")
    except Exception as e:
        await update.message.reply_html(
            f"⚠️ <b>خطأ:</b> <code>{e}</code>\n"
            "الاستخدام:\n<code>/close 123 65500</code>"
        )

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, trade_service: TradeService):
    items = trade_service.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return
    lines = ["<b>📈 التوصيات المفتوحة:</b>"]
    for it in items:
        lines.append(f"• <b>{it.asset.value}</b> ({it.side.value}) — <code>/close {it.id} [price]</code>")
    await update.message.reply_html("\n".join(lines))

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, report_service: ReportService):
    cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
    summary = report_service.summary(cid)
    await update.message.reply_html(_fmt_report(summary))

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, analytics_service: AnalyticsService):
    try:
        cid = int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None
        summary = analytics_service.performance_summary(cid)
        await update.message.reply_html(_fmt_analytics(summary))
    except Exception as e:
        await update.message.reply_html(f"⚠️ <b>خطأ:</b> <code>{e}</code>")


# --- Diagnostic Commands ---
async def diag_services_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعرض محتويات bot_data (المفاتيح وأنواعها).
    """
    services = context.application.bot_data
    if not services:
        await update.message.reply_text("⚠️ لا توجد خدمات محقونة في bot_data.")
        return
    lines = ["🔎 <b>Diag Services</b>"]
    for k, v in services.items():
        lines.append(f"• {k}: {type(v).__name__}")
    await update.message.reply_html("\n".join(lines))

async def webhook_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يعرض معلومات الويب هوك من تيليجرام.
    """
    try:
        info = await context.bot.get_webhook_info()
        msg = (
            f"🌐 <b>Webhook Info</b>\n"
            f"• URL: {info.url or '-'}\n"
            f"• Pending updates: {info.pending_update_count}\n"
            f"• IP: {info.ip_address or '-'}"
        )
        await update.message.reply_html(msg)
    except Exception as e:
        await update.message.reply_html(f"⚠️ فشل في جلب webhook info: <code>{e}</code>")


# --- Wiring ---
def register_bot_handlers(
    application: Application,
    trade_service: TradeService,
    report_service: ReportService,
    analytics_service: Optional[AnalyticsService] = None,
):
    # ✅ حقن احتياطي قبل التسجيل
    _ensure_services(application)

    # 1) رفض مبكر لغير المصرح لهم
    application.add_handler(MessageHandler(filters.ALL, unauthorized_handler), group=-1)

    # 2) تسجيل محادثة إنشاء التوصية (تبدأ بـ /newrec)
    application.add_handler(get_recommendation_conversation_handler())

    # 3) أزرار التأكيد/الإلغاء بعد المراجعة
    application.add_handler(CallbackQueryHandler(publish_recommendation, pattern=r"^rec:publish:"))
    application.add_handler(CallbackQueryHandler(cancel_publication,   pattern=r"^rec:cancel:"))

    # 4) بقية الأوامر (مقيدة بالمصرح لهم إن وُجدوا)
    application.add_handler(CommandHandler("start",   start_cmd,  filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help",    help_cmd,   filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("close",   lambda u, c: close_cmd(u, c, application.bot_data["trade_service"]),   filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("list",    lambda u, c: list_cmd(u, c, application.bot_data["trade_service"]),    filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("report",  lambda u, c: report_cmd(u, c, application.bot_data["report_service"]), filters=ALLOWED_FILTER))
    if analytics_service is not None or "analytics_service" in application.bot_data:
        application.add_handler(CommandHandler("analytics", lambda u, c: analytics_cmd(u, c, application.bot_data["analytics_service"]), filters=ALLOWED_FILTER))

    # ✅ أوامر التشخيص
    application.add_handler(CommandHandler("diag_services", diag_services_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("webhook_info",  webhook_info_cmd,  filters=ALLOWED_FILTER))

    # ✅ تسجيل معالجات الإدارة ( /open + أزرار الإغلاق السهل )
    register_management_handlers(application)


def register_base_handlers(application: Application):
    """
    توافقية: إذا كان main.py الجديد يستدعي register_base_handlers(application) فقط.
    يجب أن تكون الخدمات قد حُقنت مسبقًا في bot_data؛
    وإن لم تكن، نقوم بحقن احتياطي هنا.
    """
    _ensure_services(application)

    register_bot_handlers(
        application=application,
        trade_service=application.bot_data.get("trade_service"),
        report_service=application.bot_data.get("report_service"),
        analytics_service=application.bot_data.get("analytics_service"),
    )

    # تسجيل معالجات الإدارة هنا أيضًا
    register_management_handlers(application)
#--- END OF FILE ---