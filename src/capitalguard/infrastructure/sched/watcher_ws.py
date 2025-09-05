# --- START OF FILE: src/capitalguard/infrastructure/sched/watcher_ws.py ---
import asyncio
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file for local running
load_dotenv()

from capitalguard.infrastructure.market.ws_client import BinanceWS
from capitalguard.application.services.trade_service import TradeService
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.domain.entities import RecommendationStatus, OrderType

# Use a named logger instead of basicConfig for better integration
log = logging.getLogger("capitalguard.watcher")


async def main():
    """
    The main WebSocket client loop. It subscribes to price streams for all
    open recommendations and triggers actions based on price movements.
    """
    repo = RecommendationRepository()
    notifier = TelegramNotifier()
    # ✅ FIX: Initialize TradeService with all dependencies
    # The original file was missing this crucial wiring.
    trade_service = TradeService(repo=repo, notifier=notifier)
    ws_client = BinanceWS()

    async def on_price_update(symbol: str, price: float, _raw_data):
        """
        The core handler for every price tick received from the WebSocket.
        It checks for two main conditions:
        1. Activating PENDING recommendations.
        2. Auto-closing ACTIVE recommendations on SL hit.
        """
        log.debug(f"[WS] {symbol} -> {price}")
        
        # --- 1. Check for PENDING recommendations to activate ---
        try:
            pending_recs = await asyncio.to_thread(
                trade_service.list_open, symbol=symbol, status="PENDING"
            )
            for rec in pending_recs:
                entry, side = rec.entry.value, rec.side.value
                order_type = rec.order_type.value
                is_triggered = False

                # Correct activation logic for LIMIT orders
                if order_type == OrderType.LIMIT.value:
                    if (side == 'LONG' and price <= entry) or \
                       (side == 'SHORT' and price >= entry):
                        is_triggered = True
                
                # Correct activation logic for STOP_MARKET orders
                elif order_type == OrderType.STOP_MARKET.value:
                    if (side == 'LONG' and price >= entry) or \
                       (side == 'SHORT' and price <= entry):
                        is_triggered = True

                if is_triggered:
                    # Activate using the intended entry price
                    await asyncio.to_thread(trade_service.activate_recommendation, rec.id, rec.entry.value)

        except Exception as e:
            log.error(f"Error checking PENDING recommendations for {symbol}: {e}", exc_info=True)


        # --- 2. Check for ACTIVE recommendations to auto-close ---
        # Note: This is a simplified rapid-response SL checker.
        # The full-featured AlertService handles more complex cases like near-misses and trailing stops.
        try:
            active_recs = await asyncio.to_thread(
                trade_service.list_open, symbol=symbol, status="ACTIVE"
            )
            for rec in active_recs:
                sl, side = rec.stop_loss.value, rec.side.value
                
                sl_hit = (side == "LONG" and price <= sl) or \
                         (side == "SHORT" and price >= sl)
                
                if sl_hit:
                    log.warning(f"SL HIT DETECTED for REC #{rec.id} ({symbol}) at price {price}. Closing...")
                    await asyncio.to_thread(trade_service.close, rec.id, price)
        except Exception as e:
            log.error(f"Error checking ACTIVE recommendations for {symbol}: {e}", exc_info=True)


    while True:
        try:
            # Fetch all unique symbols for open recommendations to subscribe
            open_recs_for_symbols = await asyncio.to_thread(trade_service.list_open)
            symbols_to_watch = {rec.asset.value for rec in open_recs_for_symbols}
            
            if not symbols_to_watch:
                symbols_to_watch = {"BTCUSDT"}
            
            log.info(f"Refreshing WebSocket connections. Symbols under watch: {symbols_to_watch}")
            
            # Create a task for each symbol's price stream
            tasks = [ws_client.mini_ticker(sym, on_price_update) for sym in symbols_to_watch]
            await asyncio.gather(*tasks)

        except Exception as e:
            log.exception(f"Main WebSocket loop error: {e}. Reconnecting in 30s...")
            await asyncio.sleep(30)


if __name__ == "__main__":
    from capitalguard.logging_conf import setup_logging
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Watcher stopped manually.")
# --- END OF FILE ---