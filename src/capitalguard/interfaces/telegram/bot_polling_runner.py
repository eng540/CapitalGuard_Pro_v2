# --- START OF FILE: src/capitalguard/interfaces/telegram/bot_polling_runner.py ---
import asyncio
import logging
from dotenv import load_dotenv

# Load environment variables from .env file at the very beginning
load_dotenv()

from capitalguard.interfaces.api.main import create_ptb_app

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def main():
    """Initializes and runs the bot in polling mode."""
    logging.info("Starting bot in polling mode for local development...")
    
    ptb_app = create_ptb_app()
    
    # Initialize the application
    await ptb_app.initialize()
    
    # Start the background tasks like the alert service scheduler
    alert_service = ptb_app.bot_data["services"]["alert_service"]
    alert_service.schedule_job(ptb_app, interval_sec=60)
    
    # Start polling
    await ptb_app.run_polling()
    
    # Shutdown gracefully
    await ptb_app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped manually.")
# --- END OF FILE ---