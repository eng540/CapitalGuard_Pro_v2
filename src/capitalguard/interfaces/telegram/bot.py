from telegram.ext import Application, CommandHandler
from capitalguard.config import settings
from .commands import start, newrec, close, report

def main():
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newrec", newrec))
    app.add_handler(CommandHandler("close", close))
    app.add_handler(CommandHandler("report", report))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
