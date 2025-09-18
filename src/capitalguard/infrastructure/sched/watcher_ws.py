# --- START OF FINAL, FULLY CORRECTED AND PRODUCTION-READY FILE (Version 8.1.0) ---
# src/capitalguard/infrastructure/sched/watcher_ws.py

import asyncio
import logging
import os
from dotenv import load_dotenv
import websockets

# Load environment variables at the very beginning
load_dotenv()

from capitalguard.boot import bootstrap_app
from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.domain.entities import OrderType, RecommendationStatus
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.base import SessionLocal

# Setup logging for this specific module
log = logging.getLogger("capitalguard.watcher")

async def main():
    """
    Initializes and runs the WebSocket price watcher.
    This service is responsible for real-time price monitoring and triggering
    events like SL hits or pending order activations.
    """
    enable_watcher = os.getenv("ENABLE_WATCHER", "1").lower() in ("1", "true", "yes")
    provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()

    if not enable_watcher or provider != "binance":
        log.warning(f"Watcher is disabled. Reason: ENABLE_WATCHER={enable_watcher}, PROVIDER={provider}. Exiting gracefully.")
        return

    log.info("Bootstrapping application for the watcher...")
    ptb_app = bootstrap_app()
    if not ptb_app:
        log.error("Failed to bootstrap application for watcher. Check TELEGRAM_BOT_TOKEN setting.")
        return
        
    services = ptb_app.bot_data["services"]
    trade_service: TradeService = services["trade_service"]
    ws_client = BinanceWS()
    log.info("Watcher services built and configured successfully.")

    async def on_price_update(symbol: str, price: float, _raw_data: dict):
        """
        Core handler for every price tick. It finds relevant recommendations and
        triggers the appropriate async service methods.
        """
        log.debug(f"[WS] Price Update: {symbol} -> {price}")
        
        # Each price update is a discrete unit of work. The service methods will manage their own sessions.
        try:
            with SessionLocal() as session:
                open_recs_for_symbol = trade_service.repo.list_open_by_symbol(session, symbol)

            if not open_recs_for_symbol:
                return

            pending_recs = [r for r in open_recs_for_symbol if r.status == RecommendationStatus.PENDING]
            active_recs = [r for r in open_recs_for_symbol if r.status == RecommendationStatus.ACTIVE]

            # --- Process Pending Recommendations ---
            for rec in pending_recs:
                entry, side, order_type = rec.entry.value, rec.side.value, rec.order_type.value
                is_triggered = False
                if order_type == OrderType.LIMIT.value and ((side == "LONG" and price <= entry) or (side == "SHORT" and price >= entry)):
                    is_triggered = True
                elif order_type == OrderType.STOP_MARKET.value and ((side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry)):
                    is_triggered = True
                
                if is_triggered:
                    log.info(f"ACTIVATING pending recommendation #{rec.id} for {symbol} at price {price}.")
                    await trade_service.activate_recommendation_async(rec.id)

            # --- Process Active Recommendations ---
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                if (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl):
                    log.warning(f"STOP LOSS HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await trade_service.close_recommendation_for_user_async(rec.id, rec.user_id, price, reason="SL_HIT_WATCHER")
        
        except Exception as e:
            log.error(f"Error during on_price_update for {symbol}: {e}", exc_info=True)

    # --- Main Loop ---
    while True:
        try:
            symbols_to_watch = []
            with SessionLocal() as session:
                all_open_recs = trade_service.repo.list_open(session)
                symbols_to_watch = list({rec.asset.value for rec in all_open_recs})
            
            if not symbols_to_watch:
                log.info("No open recommendations to watch. Checking again in 60 seconds.")
                await asyncio.sleep(60)
                continue

            await ws_client.combined_stream(symbols_to_watch, on_price_update)

        except (websockets.ConnectionClosedError, websockets.ConnectionClosedOK):
            log.warning("WebSocket connection closed. Reconnecting in 10 seconds...")
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            log.info("Watcher task has been cancelled. Shutting down gracefully.")
            break
        except Exception as e:
            log.exception(f"An unexpected error occurred in the main watcher loop: {e}. Reconnecting in 30 seconds...")
            await asyncio.sleep(30)

if __name__ == "__main__":
    from capitalguard.logging_conf import setup_logging
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually by user.")

# --- END OF FINAL, FULLY CORRECTED AND PRODUCTION-READY FILE (Version 8.1.0) ---