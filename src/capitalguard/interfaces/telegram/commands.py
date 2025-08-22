from telegram import Update
from telegram.ext import ContextTypes
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

repo = RecommendationRepository()
notifier = TelegramNotifier()
svc = TradeService(repo, notifier)
rep = ReportService(repo)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بك في CapitalGuard Pro!\nأوامر: /newrec /close /report")

async def newrec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # /newrec BTCUSDT LONG 65000 63000 66000,67000
        _, asset, side, entry, sl, targets = update.message.text.split(maxsplit=5)
        tlist = [float(x) for x in targets.split(',')]
        rec = svc.create(asset, side, float(entry), float(sl), tlist)
        await update.message.reply_text(f"تم إنشاء توصية ID={rec.id}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ تنسيق الأمر غير صحيح: {e}")

async def close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # /close 1 65500
        _, rec_id, exit_price = update.message.text.split()
        rec = svc.close(int(rec_id), float(exit_price))
        await update.message.reply_text(f"✅ Closed ID={rec.id}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = rep.summary()
    text = (
        "📈 تقرير مختصر:\n"
        f"• إجمالي التوصيات: {r['total']}\n"
        f"• المفتوحة: {r['open']} | المغلقة: {r['closed']}\n"
        f"• أكثر أصل تكرارًا: {r['top_asset']} ({r['top_count']})\n"
    )
    await update.message.reply_text(text)
