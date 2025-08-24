from typing import Optional, Iterable
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

from capitalguard.config import settings
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService


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
        return


# --- Helpers ---
def _fmt_report(summary: dict) -> str:
    lines = ["<b>تقرير الأداء</b>"]
    for k, v in summary.items():
        lines.append(f"• <b>{k}</b>: {v}")
    return "\n".join(lines)


# --- Commands ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>الأوامر المتاحة:</b>\n\n"
        "• <code>/newrec &lt;asset&gt; &lt;side&gt; &lt;entry&gt; &lt;sl&gt; &lt;tp1,tp2,...&gt; [notes]</code>\n"
        "• <code>/close &lt;id&gt; &lt;exit_price&gt;</code>\n"
        "• <code>/list</code>\n"
        "• <code>/report</code>\n"
    )

async def newrec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, trade_service: TradeService):
    try:
        text = (update.message.text or "").strip()
        parts = text.split(maxsplit=6)
        if len(parts) < 6:
            raise ValueError("صيغة الأمر غير مكتملة.")
        _, asset, side, entry, sl, targets_str = parts[:6]
        notes = parts[6] if len(parts) > 6 else None

        targets = [float(t) for t in targets_str.replace(";", ",").split(",") if t]
        rec = trade_service.create(
            asset=asset,
            side=side.upper(),
            entry=float(entry),
            stop_loss=float(sl),
            targets=targets,
            channel_id=int(settings.TELEGRAM_CHAT_ID) if settings.TELEGRAM_CHAT_ID else None,
            user_id=update.effective_user.id if update.effective_user else None,
            notes=notes,
        )
        await update.message.reply_html(f"✅ تم إنشاء التوصية. <b>ID:</b> <code>{rec.id}</code>")
    except Exception as e:
        await update.message.reply_html(
            f"⚠️ <b>خطأ:</b> <code>{e}</code>\n"
            "الاستخدام:\n<code>/newrec BTCUSDT LONG 65000 63000 66000,67000</code>"
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


# --- Wiring ---
def register_bot_handlers(application: Application, trade_service: TradeService, report_service: ReportService):
    # أولاً: أي رسالة من غير المصرح لهم → ردّ رفض مبكر
    application.add_handler(MessageHandler(filters.ALL, unauthorized_handler), group=-1)

    # بعدها أوامر المصرح لهم فقط
    application.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("newrec", lambda u, c: newrec_cmd(u, c, trade_service), filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("close", lambda u, c: close_cmd(u, c, trade_service), filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("list", lambda u, c: list_cmd(u, c, trade_service), filters=ALLOWED_FILTER))
    application.add_handler(CommandHandler("report", lambda u, c: report_cmd(u, c, report_service), filters=ALLOWED_FILTER))