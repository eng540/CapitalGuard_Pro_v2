# --- START OF FINAL, CORRECTED FILE (Version 9.1.0) ---
# src/capitalguard/interfaces/telegram/bot_polling_runner.py

import asyncio
import logging
from dotenv import load_dotenv

# Load environment variables from .env file at the very beginning
load_dotenv()

# ✅ CRITICAL FIX: Import the single, correct bootstrap function from boot.py
from capitalguard.boot import bootstrap_app
from capitalguard.logging_conf import setup_logging

async def main():
    """Initializes and runs the bot in polling mode for local development."""
    logging.info("Starting bot in polling mode...")
    
    # Use the unified bootstrap function to create a fully configured app
    ptb_app = bootstrap_app()
    if not ptb_app:
        logging.critical("Failed to bootstrap the application. Exiting.")
        return

    # The alert service is already scheduled inside bootstrap_app via main.py's startup logic,
    # but for standalone polling, we might need to schedule it here if not running via FastAPI.
    # However, the current design ties scheduling to FastAPI's startup event.
    # For simplicity and consistency, we will rely on the scheduling done by bootstrap_app's internals.
    
    try:
        # Initialize and run polling
        await ptb_app.initialize()
        
        # Manually schedule alert job if not running under FastAPI
        # This check ensures it doesn't run twice
        if not getattr(ptb_app, '_is_running_via_fastapi', False):
             alert_service = ptb_app.bot_data["services"].get("alert_service")
             if alert_service:
                 alert_service.schedule_job(ptb_app, interval_sec=60)

        await ptb_app.run_polling()
    finally:
        # Shutdown gracefully
        await ptb_app.shutdown()

if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
# --- END OF FINAL, CORRECTED FILE (Version 9.1.0) ---