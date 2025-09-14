#START src/capitalguard/infrastructure/sched/watcher_ws.py
import asyncio
import logging
import os
from dotenv import load_dotenv
import websockets

# Load environment variables at the very beginning
load_dotenv()

from capitalguard.boot import build_services
from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.domain.entities import OrderType, RecommendationStatus
from capitalguard.application.services.trade_service import TradeService

# Setup logging for this specific module
log = logging.getLogger("capitalguard.watcher")

async def main():
    """
    Initializes and runs the WebSocket price watcher.
    This service is responsible for real-time price monitoring and triggering
    events like SL hits or pending order activations.
    """
    # --- Pre-flight Check ---
    # Ensure the watcher is explicitly enabled and configured for Binance
    enable_watcher = os.getenv("ENABLE_WATCHER", "1").lower() in ("1", "true", "yes")
    provider = os.getenv("MARKET_DATA_PROVIDER", "binance").lower()

    if not enable_watcher or provider != "binance":
        log.warning(f"Watcher is disabled. Reason: ENABLE_WATCHER={enable_watcher}, PROVIDER={provider}. Exiting gracefully.")
        return

    # --- Service Initialization ---
    log.info("Building services for the watcher...")
    services = build_services()
    trade_service: TradeService = services["trade_service"]
    ws_client = BinanceWS()
    log.info("Watcher services built successfully.")

    async def on_price_update(symbol: str, price: float, _raw_data: dict):
        """
        Core handler for every price tick received from the WebSocket stream.
        This function contains the critical logic for reacting to price changes.
        """
        log.debug(f"[WS] Price Update: {symbol} -> {price}")
        try:
            # Fetch all open recommendations for the specific symbol that just updated
            # This is more efficient than fetching all open recs every time
            open_recs_for_symbol = await asyncio.to_thread(
                trade_service.repo.list_open_by_symbol, symbol
            )

            if not open_recs_for_symbol:
                return

            # Separate into pending and active for clear logic
            pending_recs = [r for r in open_recs_for_symbol if r.status == RecommendationStatus.PENDING]
            active_recs = [r for r in open_recs_for_symbol if r.status == RecommendationStatus.ACTIVE]

            # --- Process Pending Recommendations ---
            for rec in pending_recs:
                entry, side = rec.entry.value, rec.side.value
                order_type_val = rec.order_type.value
                is_triggered = False
                
                if order_type_val == OrderType.LIMIT.value:
                    if (side == "LONG" and price <= entry) or (side == "SHORT" and price >= entry):
                        is_triggered = True
                elif order_type_val == OrderType.STOP_MARKET.value:
                    if (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry):
                        is_triggered = True
                
                if is_triggered:
                    log.info(f"ACTIVATING pending recommendation #{rec.id} for {symbol} at price {price}.")
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id)

            # --- Process Active Recommendations ---
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                
                if sl_hit:
                    log.warning(f"STOP LOSS HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price, reason="SL_HIT_WATCHER")

        except Exception as e:
            log.error(f"Error during on_price_update for {symbol}: {e}", exc_info=True)

    # --- Main Loop ---
    # This loop ensures the watcher is resilient and reconnects on failure.
    while True:
        try:
            # 1. Get the list of unique symbols to watch
            all_open_recs = await asyncio.to_thread(trade_service.repo.list_open)
            symbols_to_watch = list({rec.asset.value for rec in all_open_recs})
            
            if not symbols_to_watch:
                log.info("No open recommendations to watch. Checking again in 60 seconds.")
                await asyncio.sleep(60)
                continue

            # 2. Connect to the single, efficient combined stream
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
    # This allows running the watcher as a standalone script
    from capitalguard.logging_conf import setup_logging
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually by user.")
#end